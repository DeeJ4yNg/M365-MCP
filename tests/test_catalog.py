from __future__ import annotations

import json
from pathlib import Path

import pytest

from m365_mcp.catalog import ActionCatalog
from m365_mcp.errors import ActionNotFoundError, CatalogError
from m365_mcp.search_lexicon import CONCEPTS, analyze_query, normalize_text
from tests.helpers import graph_action, make_catalog, write_catalog_files


def test_catalog_fails_closed_until_approved_and_enabled(tmp_path: Path) -> None:
    action = graph_action()
    paths = write_catalog_files(tmp_path, "graph", [action], enabled=[action["name"]])
    with pytest.raises(CatalogError, match="review is not approved"):
        ActionCatalog("graph", str(paths["catalog"]), str(paths["enabled"]), str(paths["review"]))

    paths = write_catalog_files(
        tmp_path, "graph", [action], enabled=[action["name"]], approved=[action["name"]]
    )
    catalog = ActionCatalog(
        "graph", str(paths["catalog"]), str(paths["enabled"]), str(paths["review"])
    )
    assert catalog.is_enabled(catalog.actions[action["name"]])
    payload = catalog.describe(action["name"])
    assert payload["review_status"] == "approved"
    assert payload["enabled"] is True
    assert payload["review"]["evidence"] == "CHG-TEST-001"


def test_unknown_enabled_action_is_rejected(tmp_path: Path) -> None:
    action = graph_action()
    paths = write_catalog_files(tmp_path, "graph", [action], enabled=["not_a_real_action"])
    with pytest.raises(CatalogError, match="unknown action"):
        ActionCatalog("graph", str(paths["catalog"]), str(paths["enabled"]), str(paths["review"]))


def test_schema_validation_rejects_extra_and_invalid_parameters(tmp_path: Path) -> None:
    action = graph_action()
    catalog = make_catalog(
        tmp_path,
        "graph",
        [action],
        enabled=[action["name"]],
        approved=[action["name"]],
    )
    with pytest.raises(CatalogError) as extra:
        catalog.validate_params(catalog.actions[action["name"]], {"limit": 1, "arbitrary": True})
    assert extra.value.details["validation_errors"][0]["path"] == "$"

    with pytest.raises(CatalogError):
        catalog.validate_params(catalog.actions[action["name"]], {"limit": 0})


def test_search_excludes_disabled_by_default(tmp_path: Path) -> None:
    action = graph_action()
    catalog = make_catalog(tmp_path, "graph", [action])
    assert catalog.search("users") == []
    assert len(catalog.search("users", include_disabled=True)) == 1
    with pytest.raises(PermissionError, match="not enabled"):
        catalog.require_action(action["name"], operation="read")


def test_operation_mismatch_is_explicit(tmp_path: Path) -> None:
    action = graph_action()
    catalog = make_catalog(
        tmp_path,
        "graph",
        [action],
        enabled=[action["name"]],
        approved=[action["name"]],
    )
    with pytest.raises(ActionNotFoundError) as exc:
        catalog.require_action(action["name"], operation="write")
    assert exc.value.code == "operation_mismatch"
    assert exc.value.details["correct_tool"] == "execute_read"


def test_hot_reload_takes_effect(tmp_path: Path) -> None:
    action = graph_action()
    paths = write_catalog_files(tmp_path, "graph", [action], approved=[action["name"]])
    catalog = ActionCatalog(
        "graph", str(paths["catalog"]), str(paths["enabled"]), str(paths["review"])
    )
    assert not catalog.is_enabled(catalog.actions[action["name"]])
    raw = json.loads(paths["enabled"].read_text(encoding="utf-8"))
    raw["enabledTools"]["graph"] = [action["name"]]
    paths["enabled"].write_text(json.dumps(raw), encoding="utf-8")
    assert catalog.is_enabled(catalog.actions[action["name"]])


def test_invalid_review_registry_fails_closed(tmp_path: Path) -> None:
    action = graph_action()
    paths = write_catalog_files(tmp_path, "graph", [action])
    paths["review"].write_text(
        json.dumps(
            {
                "reviews": {
                    "graph": {
                        action["name"]: {
                            "status": "approved",
                            "reviewed_by": "",
                            "reviewed_at": "not-a-date",
                            "evidence": "",
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(CatalogError, match="reviewed_by"):
        ActionCatalog("graph", str(paths["catalog"]), str(paths["enabled"]), str(paths["review"]))


def test_real_powershell_catalog_is_safe_and_complete() -> None:
    root = Path(__file__).resolve().parents[1]
    raw = json.loads((root / "src/m365_mcp/catalogs/powershell.json").read_text(encoding="utf-8"))
    assert len(raw) >= 100
    names = [item["name"] for item in raw]
    assert len(names) == len(set(names))
    for item in raw:
        assert item["review_status"] == "pending"
        assert item["enabled"] is False
        assert item["backend"] == "powershell"
        if item["implementation_status"] == "implemented":
            assert item["powershell"] is not None
            assert not item["powershell"]["cmdlet"].startswith(("Invoke-", "Start-Process"))
    assert any(item["implementation_status"] == "unsupported" for item in raw)
    assert {item["domain"] for item in raw} >= {
        "Exchange Online",
        "SharePoint Online",
        "Microsoft Teams",
        "Security & Compliance",
    }


def test_bilingual_search_quality_and_domain_aliases() -> None:
    root = Path(__file__).resolve().parents[1]
    config = root / "config"
    graph = ActionCatalog(
        "graph",
        "",
        str(config / "enabledTools.json"),
        str(config / "review-registry.json"),
    )
    powershell = ActionCatalog(
        "powershell",
        "",
        str(config / "enabledTools.json"),
        str(config / "review-registry.json"),
    )

    users = graph.search(
        "查找已启用用户", domain="Entra", operation_type="read", include_disabled=True
    )
    assert users[0]["name"] == "entra_list_users"

    devices = graph.search(
        "reset intune device passcode",
        domain="终端",
        operation_type="write",
        include_disabled=True,
    )
    assert devices[0]["name"] == "intune_reset_passcode_device"

    health = graph.search(
        "查看服务健康", domain="Microsoft 365", operation_type="read", include_disabled=True
    )
    assert {
        "m365_get_service_health",
        "m365_list_service_health",
    } & {item["name"] for item in health[:3]}

    meetings = graph.search(
        "查询 Teams 在线会议", domain="团队", operation_type="read", include_disabled=True
    )
    assert meetings[0]["name"] in {
        "teams_get_online_meeting",
        "teams_list_online_meetings",
    }

    quarantine = powershell.search(
        "释放隔离邮件", domain="Exchange", operation_type="write", include_disabled=True
    )
    assert quarantine[0]["name"] == "release_quarantine_message"

    mailbox_permissions = powershell.search(
        "列出邮箱权限", domain="Exchange", operation_type="read", include_disabled=True
    )
    assert mailbox_permissions[0]["name"] == "get_mailbox_permissions"

    assert graph.search("zzzz nonsense", include_disabled=True) == []


@pytest.mark.parametrize(
    ("server", "intent", "domain", "operation", "expected_any"),
    [
        (
            "graph",
            "user cannot sign in to Outlook after MFA reset, getting AADSTS50076",
            None,
            "read",
            {
                "entra_mfa_registration_report",
                "entra_list_auth_methods",
                "entra_get_sign_in_log",
            },
        ),
        (
            "powershell",
            "release quarantined email",
            "Exchange",
            "write",
            {"release_quarantine_message"},
        ),
        (
            "graph",
            "lost laptop needs remote wipe",
            None,
            "write",
            {"intune_wipe_device"},
        ),
        (
            "powershell",
            "remove user from DL finance-team",
            "Exchange",
            "write",
            {"remove_distribution_group_member"},
        ),
        (
            "graph",
            "Autopilot ESP stuck during enrollment",
            "Intune",
            "read",
            {
                "intune_list_enrollment_configurations",
                "intune_get_autopilot_device",
                "intune_list_autopilot_devices",
            },
        ),
        (
            "graph",
            "SharePoint site access denied",
            "SharePoint",
            "read",
            {
                "sharepoint_get_site",
                "sharepoint_list_sites",
                "sharepoint_list_item_permissions",
            },
        ),
        (
            "powershell",
            "mailbox full and not receiving email",
            "Exchange",
            "read",
            {
                "get_exchange_mailbox",
                "get_exchange_mailbox_statistics",
                "get_exchange_mailbox_folder_statistics",
            },
        ),
    ],
)
def test_english_ticket_language_search(
    server: str,
    intent: str,
    domain: str | None,
    operation: str,
    expected_any: set[str],
) -> None:
    root = Path(__file__).resolve().parents[1]
    config = root / "config"
    catalog = ActionCatalog(
        server,  # type: ignore[arg-type]
        "",
        str(config / "enabledTools.json"),
        str(config / "review-registry.json"),
    )
    results = catalog.search(
        intent,
        limit=3,
        domain=domain,
        operation_type=operation,
        include_disabled=True,
    )
    assert expected_any & {item["name"] for item in results}


def test_english_normalization_and_phrase_expansion() -> None:
    assert normalize_text("ResetPassword for M365") == "reset password for microsoft 365"
    assert normalize_text("AADSTS50076 Sign-In") == "aadsts50076 sign in"
    parsed = analyze_query("user cannot login with 2FA after MFA reset")
    assert "sign in" in parsed.aliases
    assert "mfa" in parsed.aliases
    assert any(concept.key == "identity.auth.signin" for concept in parsed.concepts)


def test_search_lexicon_entries_are_normalized_and_unique() -> None:
    keys = [concept.key for concept in CONCEPTS]
    assert len(keys) == len(set(keys))
    assert len(CONCEPTS) >= 35
    for concept in CONCEPTS:
        assert concept.terms
        assert concept.aliases
        assert concept.action_hints
        for hint in concept.action_hints:
            assert hint == normalize_text(hint), (concept.key, hint)
        for term in concept.terms:
            assert normalize_text(term), (concept.key, term)


def test_real_graph_catalog_is_safe_and_complete() -> None:
    root = Path(__file__).resolve().parents[1]
    raw = json.loads((root / "src/m365_mcp/catalogs/graph.json").read_text(encoding="utf-8"))
    assert len(raw) >= 150
    names = [item["name"] for item in raw]
    assert len(names) == len(set(names))
    for item in raw:
        assert item["review_status"] == "pending"
        assert item["enabled"] is False
        assert item["backend"] == "graph"
        assert item["graph"] is not None
        assert not item["graph"]["path"].startswith("/beta")
        assert "://" not in item["graph"]["path"]
    assert {item["domain"] for item in raw} >= {
        "Entra ID",
        "Intune",
        "SharePoint Online",
        "OneDrive",
        "Microsoft Teams",
        "Microsoft 365",
        "Security & Compliance",
    }
