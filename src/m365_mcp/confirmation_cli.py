"""Issue a single-use write confirmation JWT for local verification.

In production, the change approval system should issue tokens with the same
claims. This CLI exists for controlled testing and break-glass operations.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from m365_mcp.catalog import ActionCatalog
from m365_mcp.confirmation import canonical_params_sha256, mint_confirmation_token


def _read_private_key(path: str, env_name: str) -> str:
    if env_name and env_name in __import__("os").environ:
        return __import__("os").environ[env_name]
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"cannot read private key: {path}") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="m365-confirmation")
    parser.add_argument("--server", choices=["graph", "powershell"], required=True)
    parser.add_argument("--action", required=True)
    parser.add_argument("--params", default="{}", help="JSON object or @path/to/params.json")
    parser.add_argument("--actor", required=True, help="Agent app client ID bound to the token")
    parser.add_argument("--approver", required=True, help="Approver UPN or change record ID")
    parser.add_argument("--ttl", type=int, default=300, choices=range(30, 901), metavar="30-900")
    parser.add_argument("--algorithm", choices=["RS256", "HS256"], default="RS256")
    parser.add_argument("--private-key", default="", help="PEM private key path")
    parser.add_argument("--private-key-env", default="MCP_CONFIRMATION_PRIVATE_KEY")
    parser.add_argument("--issuer", default="m365-tool-approval")
    parser.add_argument("--audience", default="")
    parser.add_argument("--catalog-dir", default="")
    parser.add_argument("--config-dir", default="")
    parser.add_argument("--project-root", default="")
    parser.add_argument("--json", action="store_true", help="Print token and claims as JSON")
    return parser


def _params(value: str) -> dict[str, object]:
    if value.startswith("@"):
        raw = json.loads(Path(value[1:]).read_text(encoding="utf-8"))
    else:
        raw = json.loads(value)
    if not isinstance(raw, dict):
        raise ValueError("params must be a JSON object")
    return raw


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        values = _params(args.params)
        root = Path(args.project_root).resolve() if args.project_root else Path.cwd()
        catalog_dir = (
            Path(args.catalog_dir).resolve() if args.catalog_dir else root / "src/m365_mcp/catalogs"
        )
        config_dir = Path(args.config_dir).resolve() if args.config_dir else root / "config"
        catalog = ActionCatalog(
            args.server,
            str(catalog_dir / f"{args.server}.json"),
            str(config_dir / "enabledTools.json"),
            str(config_dir / "review-registry.json"),
        )
        action = catalog.actions.get(args.action)
        if action is None:
            raise ValueError(f"unknown action: {args.action}")
        if action.operation_type != "write":
            raise ValueError(f"{args.action} is not a write action")
        if catalog.review_status(args.action) != "approved":
            raise ValueError("action must be approved before a confirmation can be issued")
        catalog.validate_params(action, values)
        key = _read_private_key(args.private_key, args.private_key_env)
        if args.algorithm == "HS256" and len(key) < 32:
            raise ValueError("HS256 secret must be at least 32 characters")
        audience = args.audience or f"m365-{args.server}-mcp"
        token = mint_confirmation_token(
            action=args.action,
            params=values,
            actor=args.actor,
            approver=args.approver,
            server_name=args.server,
            algorithm=args.algorithm,
            private_key=key,
            issuer=args.issuer,
            audience=audience,
            ttl_seconds=args.ttl,
        )
        if args.json:
            print(
                json.dumps(
                    {
                        "token": token,
                        "action": args.action,
                        "actor": args.actor,
                        "approver": args.approver,
                        "audience": audience,
                        "issuer": args.issuer,
                        "params_sha256": canonical_params_sha256(values),
                        "ttl_seconds": args.ttl,
                    },
                    ensure_ascii=False,
                )
            )
        else:
            print(token)
        return 0
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
