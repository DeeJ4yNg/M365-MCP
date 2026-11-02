from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

import pytest

from m365_mcp.errors import CatalogError
from m365_mcp.powershell.executor import PowerShellError, PowerShellExecutor
from tests.helpers import make_catalog, powershell_action, powershell_settings, write_catalog_files


def _fake_pwsh(tmp_path: Path, body: str) -> str:
    script = tmp_path / "fake-pwsh"
    script.write_text(f"#!{sys.executable}\n{body}\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return str(script)


def _ok_script(tmp_path: Path, result: object | None = None) -> str:
    payload = json.dumps(result if result is not None else {"ok": True, "result": []})
    return _fake_pwsh(
        tmp_path,
        "import json, os, sys\n"
        "request = json.load(sys.stdin)\n"
        f"payload = {payload!r}\n"
        "print('__M365_RESULT__' + payload)\n",
    )


@pytest.mark.asyncio
async def test_executes_fixed_cmdlet_with_mapped_parameters(tmp_path) -> None:
    action = powershell_action()
    paths = write_catalog_files(
        tmp_path, "powershell", [action], enabled=[action["name"]], approved=[action["name"]]
    )
    settings = powershell_settings(
        tmp_path,
        paths,
        pwsh_path=_fake_pwsh(
            tmp_path,
            "import json, sys\n"
            "request = json.load(sys.stdin)\n"
            "print('__M365_RESULT__' + json.dumps({'ok': True, 'result': [request]}))\n",
        ),
    )
    catalog = make_catalog(
        tmp_path, "powershell", [action], enabled=[action["name"]], approved=[action["name"]]
    )
    executor = PowerShellExecutor(settings, catalog)
    result = await executor.execute(
        catalog.actions[action["name"]],
        {"identity": "alice@contoso.com"},
        correlation_id="corr-ps-1",
    )
    received = result["data"][0]
    assert received["action"] == action["name"]
    assert received["parameters"] == {"Identity": "alice@contoso.com"}


@pytest.mark.asyncio
async def test_constants_are_added_and_cannot_be_overridden(tmp_path) -> None:
    action = powershell_action(
        operation="write",
        cmdlet="Set-Mailbox",
        properties={
            "identity": {"type": "string", "description": "Mailbox."},
            "hidden": {"type": "boolean", "description": "Hidden flag."},
        },
        parameters={"identity": "Identity", "hidden": "HiddenFromAddressListsEnabled"},
        constants={"Confirm": False},
    )
    paths = write_catalog_files(
        tmp_path, "powershell", [action], enabled=[action["name"]], approved=[action["name"]]
    )
    settings = powershell_settings(
        tmp_path,
        paths,
        pwsh_path=_fake_pwsh(
            tmp_path,
            "import json, sys\n"
            "request = json.load(sys.stdin)\n"
            "print('__M365_RESULT__' + json.dumps({'ok': True, 'result': [request]}))\n",
        ),
    )
    catalog = make_catalog(
        tmp_path, "powershell", [action], enabled=[action["name"]], approved=[action["name"]]
    )
    result = await PowerShellExecutor(settings, catalog).execute(
        catalog.actions[action["name"]],
        {"identity": "room1", "hidden": True},
        correlation_id="corr-ps-2",
    )
    assert result["data"][0]["parameters"] == {
        "Identity": "room1",
        "HiddenFromAddressListsEnabled": True,
        "Confirm": False,
    }


@pytest.mark.asyncio
async def test_timeout_kills_worker(tmp_path) -> None:
    action = powershell_action()
    paths = write_catalog_files(
        tmp_path, "powershell", [action], enabled=[action["name"]], approved=[action["name"]]
    )
    settings = powershell_settings(
        tmp_path,
        paths,
        pwsh_path=_fake_pwsh(tmp_path, "import time\ntime.sleep(10)\n"),
    )
    settings = settings.model_copy(update={"worker_timeout_seconds": 0.2})
    catalog = make_catalog(
        tmp_path, "powershell", [action], enabled=[action["name"]], approved=[action["name"]]
    )
    with pytest.raises(PowerShellError) as exc:
        await PowerShellExecutor(settings, catalog).execute(
            catalog.actions[action["name"]], {"identity": "x"}, correlation_id="corr"
        )
    assert exc.value.code == "powershell_timeout"


@pytest.mark.asyncio
async def test_malformed_worker_output_is_not_treated_as_success(tmp_path) -> None:
    action = powershell_action()
    paths = write_catalog_files(
        tmp_path, "powershell", [action], enabled=[action["name"]], approved=[action["name"]]
    )
    settings = powershell_settings(
        tmp_path, paths, pwsh_path=_fake_pwsh(tmp_path, "print('no envelope here')")
    )
    catalog = make_catalog(
        tmp_path, "powershell", [action], enabled=[action["name"]], approved=[action["name"]]
    )
    with pytest.raises(PowerShellError) as exc:
        await PowerShellExecutor(settings, catalog).execute(
            catalog.actions[action["name"]], {"identity": "x"}, correlation_id="corr"
        )
    assert exc.value.code == "powershell_protocol_error"


@pytest.mark.asyncio
async def test_oversized_write_response_is_a_success_with_omitted_data(tmp_path) -> None:
    action = powershell_action(operation="write", cmdlet="Set-Mailbox")
    paths = write_catalog_files(
        tmp_path, "powershell", [action], enabled=[action["name"]], approved=[action["name"]]
    )
    settings = powershell_settings(
        tmp_path, paths, pwsh_path=_ok_script(tmp_path, {"ok": True, "result": ["x" * 20000]})
    )
    catalog = make_catalog(
        tmp_path, "powershell", [action], enabled=[action["name"]], approved=[action["name"]]
    )
    result = await PowerShellExecutor(settings, catalog).execute(
        catalog.actions[action["name"]], {"identity": "x"}, correlation_id="corr"
    )
    assert result["data"]["completed"] is True
    assert result["data"]["response_omitted"] is True


@pytest.mark.asyncio
async def test_oversized_read_response_is_rejected(tmp_path) -> None:
    action = powershell_action()
    paths = write_catalog_files(
        tmp_path, "powershell", [action], enabled=[action["name"]], approved=[action["name"]]
    )
    settings = powershell_settings(
        tmp_path, paths, pwsh_path=_ok_script(tmp_path, {"ok": True, "result": ["x" * 20000]})
    )
    catalog = make_catalog(
        tmp_path, "powershell", [action], enabled=[action["name"]], approved=[action["name"]]
    )
    with pytest.raises(CatalogError) as exc:
        await PowerShellExecutor(settings, catalog).execute(
            catalog.actions[action["name"]], {"identity": "x"}, correlation_id="corr"
        )
    assert exc.value.code == "result_too_large"
