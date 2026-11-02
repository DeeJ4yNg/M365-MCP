from __future__ import annotations

from pathlib import Path

from m365_mcp.inventory_cli import main
from tests.helpers import graph_action, write_catalog_files


def _args(tmp_path: Path) -> list[str]:
    return [
        "--catalog-dir",
        str(tmp_path / "catalogs"),
        "--config-dir",
        str(tmp_path / "config"),
    ]


def test_review_enable_disable_and_export(tmp_path, capsys) -> None:
    action = graph_action()
    write_catalog_files(tmp_path, "graph", [action])

    assert (
        main(
            [
                *_args(tmp_path),
                "review",
                "--server",
                "graph",
                "--action",
                action["name"],
                "--status",
                "approved",
                "--reviewer",
                "reviewer@example.com",
                "--evidence",
                "CHG-1",
            ]
        )
        == 0
    )
    assert main([*_args(tmp_path), "enable", "--server", "graph", "--action", action["name"]]) == 0
    assert main([*_args(tmp_path), "validate", "--server", "graph"]) == 0
    output = capsys.readouterr().out
    assert "enabled=1" in output

    assert main([*_args(tmp_path), "disable", "--server", "graph", "--action", action["name"]]) == 0
    assert main([*_args(tmp_path), "validate", "--server", "graph"]) == 0
    assert "enabled=0" in capsys.readouterr().out

    export_dir = tmp_path / "inventory"
    assert (
        main(
            [
                *_args(tmp_path),
                "export",
                "--server",
                "graph",
                "--output",
                str(export_dir),
            ]
        )
        == 0
    )
    assert (export_dir / "M365-Tool-Inventory.json").is_file()
    assert (export_dir / "M365-Tool-Inventory.csv").is_file()
    assert (export_dir / "M365-Tool-Inventory.md").is_file()


def test_enable_pending_action_is_rejected(tmp_path, capsys) -> None:
    action = graph_action()
    write_catalog_files(tmp_path, "graph", [action])
    result = main([*_args(tmp_path), "enable", "--server", "graph", "--action", action["name"]])
    assert result == 2
    assert "must be approved" in capsys.readouterr().err
