"""Tool inventory validation, review workflow and enablement CLI."""

from __future__ import annotations

import argparse
import csv
import fcntl
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from m365_mcp.catalog import ActionCatalog
from m365_mcp.config import ServerName
from m365_mcp.errors import M365Error


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _paths(args: argparse.Namespace) -> tuple[Path, Path]:
    root = Path(args.project_root).resolve() if args.project_root else _project_root()
    catalog_dir = (
        Path(args.catalog_dir).resolve() if args.catalog_dir else root / "src/m365_mcp/catalogs"
    )
    config_dir = Path(args.config_dir).resolve() if args.config_dir else root / "config"
    return catalog_dir, config_dir


def _catalog(server: ServerName, args: argparse.Namespace) -> ActionCatalog:
    catalog_dir, config_dir = _paths(args)
    return ActionCatalog(
        server,
        str(catalog_dir / f"{server}.json"),
        str(config_dir / "enabledTools.json"),
        str(config_dir / "review-registry.json"),
    )


def _servers(value: str) -> list[ServerName]:
    if value == "all":
        return ["graph", "powershell"]
    return [value]  # type: ignore[list-item]


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", dir=path.parent, prefix=f".{path.name}.", encoding="utf-8", delete=False
    ) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    os.chmod(temporary, 0o640)
    os.replace(temporary, path)


class _Lock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.handle: Any = None

    def __enter__(self) -> _Lock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+", encoding="utf-8")
        fcntl.flock(self.handle, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc: Any) -> None:
        fcntl.flock(self.handle, fcntl.LOCK_UN)
        self.handle.close()


def command_validate(args: argparse.Namespace) -> int:
    failures = 0
    for server in _servers(args.server):
        try:
            catalog = _catalog(server, args)
            actions = catalog.actions.values()
            implemented = sum(a.implementation_status == "implemented" for a in actions)
            approved = sum(catalog.review_status(a.name) == "approved" for a in actions)
            enabled = len(catalog.policy.enabled)
            print(
                f"{server}: ok actions={len(catalog.actions)} implemented={implemented} "
                f"approved={approved} enabled={enabled}"
            )
        except (M365Error, ValueError) as exc:
            failures += 1
            print(f"{server}: INVALID {exc}", file=sys.stderr)
    return 1 if failures else 0


def command_stats(args: argparse.Namespace) -> int:
    for server in _servers(args.server):
        catalog = _catalog(server, args)
        by_domain: dict[str, int] = {}
        by_status: dict[str, int] = {}
        for action in catalog.actions.values():
            by_domain[action.domain] = by_domain.get(action.domain, 0) + 1
            by_status[action.implementation_status] = (
                by_status.get(action.implementation_status, 0) + 1
            )
        print(
            json.dumps(
                {
                    "server": server,
                    "total": len(catalog.actions),
                    "by_domain": dict(sorted(by_domain.items())),
                    "by_status": dict(sorted(by_status.items())),
                    "approved": sum(
                        catalog.review_status(name) == "approved" for name in catalog.actions
                    ),
                    "enabled": len(catalog.policy.enabled),
                    "writes_enabled": sum(
                        catalog.actions[name].operation_type == "write"
                        for name in catalog.policy.enabled
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    return 0


def command_review(args: argparse.Namespace) -> int:
    _, config_dir = _paths(args)
    catalog = _catalog(args.server, args)
    if args.action not in catalog.actions:
        print(f"Unknown action: {args.action}", file=sys.stderr)
        return 2
    if (
        args.status == "approved"
        and catalog.actions[args.action].implementation_status != "implemented"
    ):
        print("Only implemented actions can be approved.", file=sys.stderr)
        return 2
    if args.status in {"approved", "rejected"} and (not args.reviewer or not args.evidence):
        print("--reviewer and --evidence are required for approved/rejected", file=sys.stderr)
        return 2
    registry_path = config_dir / "review-registry.json"
    with _Lock(registry_path.with_suffix(".lock")):
        raw: dict[str, Any] = {"schemaVersion": 1, "reviews": {"graph": {}, "powershell": {}}}
        if registry_path.exists():
            raw = json.loads(registry_path.read_text(encoding="utf-8"))
        reviews = raw.setdefault("reviews", {})
        reviews.setdefault("graph", {})
        reviews.setdefault("powershell", {})
        if args.status == "pending":
            reviews[args.server].pop(args.action, None)
        else:
            from datetime import UTC, datetime

            reviews[args.server][args.action] = {
                "status": args.status,
                "reviewed_by": args.reviewer,
                "reviewed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "evidence": args.evidence,
            }
        _atomic_json(registry_path, raw)
    _catalog(args.server, args).reviews.refresh(force=True)
    print(f"{args.server}/{args.action}: review={args.status}")
    return 0


def command_enable(args: argparse.Namespace) -> int:
    _, config_dir = _paths(args)
    catalog = _catalog(args.server, args)
    action = catalog.actions.get(args.action)
    if action is None:
        print(f"Unknown action: {args.action}", file=sys.stderr)
        return 2
    if catalog.review_status(args.action) != "approved":
        print("Action must be approved before it can be enabled.", file=sys.stderr)
        return 2
    if action.implementation_status != "implemented":
        print("Only implemented actions can be enabled.", file=sys.stderr)
        return 2
    config_path = config_dir / "enabledTools.json"
    with _Lock(config_path.with_suffix(".lock")):
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        enabled = raw.setdefault("enabledTools", {})
        enabled.setdefault("graph", [])
        enabled.setdefault("powershell", [])
        if args.action not in enabled[args.server]:
            enabled[args.server].append(args.action)
            enabled[args.server].sort()
        _atomic_json(config_path, raw)
    print(f"{args.server}/{args.action}: enabled")
    return 0


def command_disable(args: argparse.Namespace) -> int:
    _, config_dir = _paths(args)
    config_path = config_dir / "enabledTools.json"
    with _Lock(config_path.with_suffix(".lock")):
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        enabled = raw.setdefault("enabledTools", {})
        before = enabled.get(args.server, [])
        enabled[args.server] = [name for name in before if name != args.action]
        _atomic_json(config_path, raw)
    print(f"{args.server}/{args.action}: disabled")
    return 0


def command_export(args: argparse.Namespace) -> int:
    output_dir = Path(args.output).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    combined: list[dict[str, Any]] = []
    for server in _servers(args.server):
        catalog = _catalog(server, args)
        for action in catalog.inventory(include_disabled=True):
            combined.append({"server": server, **action})
    _atomic_json(output_dir / "M365-Tool-Inventory.json", combined)
    csv_path = output_dir / "M365-Tool-Inventory.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            lineterminator="\n",
            fieldnames=[
                "server",
                "name",
                "domain",
                "backend",
                "operation_type",
                "risk_level",
                "implementation_status",
                "review_status",
                "enabled",
                "required_permissions",
                "description",
                "docs_url",
            ],
        )
        writer.writeheader()
        for item in combined:
            writer.writerow(
                {
                    **{key: item.get(key) for key in writer.fieldnames},
                    "required_permissions": "; ".join(item["required_permissions"]),
                }
            )
    md_path = output_dir / "M365-Tool-Inventory.md"
    lines = [
        "# Microsoft 365 MCP Tool Inventory",
        "",
        "This file is generated by `m365-inventory export`; do not edit it manually.",
        "",
        f"Total actions: {len(combined)}",
        "",
        "| Server | Tool Name | Domain | Operation | Risk | Implemented | Review | Enabled |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for item in combined:
        lines.append(
            f"| {item['server']} | `{item['name']}` | {item['domain']} | "
            f"{item['operation_type']} | {item['risk_level']} | "
            f"{item['implementation_status']} | {item['review_status']} | "
            f"{'yes' if item['enabled'] else 'no'} |"
        )
    lines.append("")
    md_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"exported {len(combined)} actions to {output_dir}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="m365-inventory",
        description="Validate, review, enable and export the M365 MCP tool inventory.",
    )
    parser.add_argument("--project-root", default="")
    parser.add_argument("--catalog-dir", default="")
    parser.add_argument("--config-dir", default="")
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate")
    validate.add_argument("--server", choices=["graph", "powershell", "all"], default="all")
    validate.set_defaults(func=command_validate)

    stats = sub.add_parser("stats")
    stats.add_argument("--server", choices=["graph", "powershell", "all"], default="all")
    stats.set_defaults(func=command_stats)

    review = sub.add_parser("review")
    review.add_argument("--server", choices=["graph", "powershell"], required=True)
    review.add_argument("--action", required=True)
    review.add_argument("--status", choices=["pending", "approved", "rejected"], required=True)
    review.add_argument("--reviewer", default="")
    review.add_argument("--evidence", default="")
    review.set_defaults(func=command_review)

    enable = sub.add_parser("enable")
    enable.add_argument("--server", choices=["graph", "powershell"], required=True)
    enable.add_argument("--action", required=True)
    enable.set_defaults(func=command_enable)

    disable = sub.add_parser("disable")
    disable.add_argument("--server", choices=["graph", "powershell"], required=True)
    disable.add_argument("--action", required=True)
    disable.set_defaults(func=command_disable)

    export = sub.add_parser("export")
    export.add_argument("--server", choices=["graph", "powershell", "all"], default="all")
    export.add_argument("--output", required=True)
    export.set_defaults(func=command_export)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (M365Error, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
