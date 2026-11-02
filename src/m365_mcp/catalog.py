"""Tool inventory loading, policy gating and deterministic discovery."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterable
from datetime import datetime
from importlib import resources
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from m365_mcp.config import ServerName
from m365_mcp.errors import ActionNotFoundError, CatalogError
from m365_mcp.models import ActionDefinition
from m365_mcp.search_lexicon import (
    analyze_query,
    canonical_domain,
    concept_boost,
    contains_phrase,
    normalize_text,
)


class ReviewRegistry:
    """Hot-reloadable human review decisions kept separate from generated catalogs."""

    def __init__(self, path: str, server: ServerName, actions: dict[str, ActionDefinition]) -> None:
        self.path = Path(path)
        self.server = server
        self.actions = actions
        self._lock = threading.Lock()
        self._mtime_ns: int | None = None
        self._reviews: dict[str, dict[str, Any]] = {}
        self.refresh(force=True)

    def refresh(self, *, force: bool = False) -> dict[str, dict[str, Any]]:
        with self._lock:
            try:
                mtime_ns = self.path.stat().st_mtime_ns
            except FileNotFoundError:
                self._mtime_ns = -1
                self._reviews = {}
                return self._reviews
            if not force and mtime_ns == self._mtime_ns:
                return self._reviews
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                reviews = self._parse(raw)
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                raise CatalogError(
                    "invalid_review_registry",
                    f"Cannot load review registry: {exc}",
                    {"path": str(self.path)},
                ) from exc
            self._mtime_ns = mtime_ns
            self._reviews = reviews
            return self._reviews

    def _parse(self, raw: Any) -> dict[str, dict[str, Any]]:
        if not isinstance(raw, dict):
            raise ValueError("registry must be a JSON object")
        reviews_root = raw.get("reviews", {})
        if not isinstance(reviews_root, dict):
            raise ValueError("reviews must be an object")
        unknown_servers = set(reviews_root) - {"graph", "powershell"}
        if unknown_servers:
            raise ValueError(f"unknown server keys: {sorted(unknown_servers)}")
        server_reviews = reviews_root.get(self.server, {})
        if not isinstance(server_reviews, dict):
            raise ValueError(f"reviews.{self.server} must be an object")
        parsed: dict[str, dict[str, Any]] = {}
        for name, review in server_reviews.items():
            if name not in self.actions:
                raise ValueError(f"review references unknown action: {name}")
            if not isinstance(review, dict):
                raise ValueError(f"review for {name} must be an object")
            status = review.get("status")
            if status not in {"pending", "approved", "rejected"}:
                raise ValueError(f"invalid review status for {name}: {status!r}")
            reviewed_by = review.get("reviewed_by")
            reviewed_at = review.get("reviewed_at")
            evidence = review.get("evidence")
            if status in {"approved", "rejected"}:
                if not isinstance(reviewed_by, str) or not reviewed_by.strip():
                    raise ValueError(f"reviewed_by is required for {name} when {status}")
                if not isinstance(reviewed_at, str):
                    raise ValueError(f"reviewed_at is required for {name} when {status}")
                try:
                    datetime.fromisoformat(reviewed_at.replace("Z", "+00:00"))
                except ValueError as exc:
                    raise ValueError(
                        f"reviewed_at for {name} must be an ISO 8601 timestamp"
                    ) from exc
                if not isinstance(evidence, str) or not evidence.strip():
                    raise ValueError(f"evidence is required for {name} when {status}")
            parsed[name] = {
                "status": status,
                "reviewed_by": reviewed_by,
                "reviewed_at": reviewed_at,
                "evidence": evidence,
            }
        return parsed

    def status(self, action: ActionDefinition) -> str:
        review = self.refresh().get(action.name)
        return str(review["status"]) if review else action.review_status

    def info(self, action: ActionDefinition) -> dict[str, Any]:
        review = self.refresh().get(action.name)
        if review:
            return dict(review)
        return {
            "status": action.review_status,
            "reviewed_by": None,
            "reviewed_at": None,
            "evidence": None,
        }


class EnabledPolicy:
    """Hot-reloadable enabledTools allow-list.

    A file typo or an attempt to enable an unreviewed action fails closed with a
    clear catalog error. Disabling takes effect on the next operation; enabling a
    newly reviewed action also takes effect without a process restart.
    """

    def __init__(
        self,
        path: str,
        server: ServerName,
        actions: dict[str, ActionDefinition],
        review_approved: Callable[[str], bool],
    ) -> None:
        self.path = Path(path)
        self.server = server
        self.actions = actions
        self._review_approved = review_approved
        self._lock = threading.Lock()
        self._mtime_ns: int | None = None
        self._enabled: frozenset[str] = frozenset()
        self.refresh(force=True)

    def refresh(self, *, force: bool = False) -> frozenset[str]:
        with self._lock:
            try:
                mtime_ns = self.path.stat().st_mtime_ns
            except FileNotFoundError:
                mtime_ns = -1
            if not force and mtime_ns == self._mtime_ns:
                return self._enabled
            if mtime_ns == -1:
                self._mtime_ns = -1
                self._enabled = frozenset()
                return self._enabled
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                entries = self._parse(raw)
                self._validate(entries)
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                raise CatalogError(
                    "invalid_enabled_tools",
                    f"Cannot load enabledTools config: {exc}",
                    {"path": str(self.path)},
                ) from exc
            self._mtime_ns = mtime_ns
            self._enabled = frozenset(entries)
            return self._enabled

    def _parse(self, raw: Any) -> list[str]:
        if not isinstance(raw, dict):
            raise ValueError("config must be a JSON object")
        mapping = raw.get("enabledTools", raw)
        if not isinstance(mapping, dict):
            raise ValueError("enabledTools must be an object")
        unknown_servers = set(mapping) - {"graph", "powershell"}
        if unknown_servers:
            raise ValueError(f"unknown server keys: {sorted(unknown_servers)}")
        values = mapping.get(self.server, [])
        if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
            raise ValueError(f"enabledTools.{self.server} must be an array of action names")
        if len(values) != len(set(values)):
            raise ValueError(f"enabledTools.{self.server} contains duplicates")
        return values

    def _validate(self, names: Iterable[str]) -> None:
        for name in names:
            action = self.actions.get(name)
            if action is None:
                raise ValueError(f"unknown action for server {self.server}: {name}")
            if action.implementation_status != "implemented":
                raise ValueError(
                    f"{name} cannot be enabled: implementation_status="
                    f"{action.implementation_status}"
                )
            if not self._review_approved(name):
                raise ValueError(f"{name} cannot be enabled: review is not approved")

    @property
    def enabled(self) -> frozenset[str]:
        return self.refresh()

    def is_enabled(self, name: str) -> bool:
        return name in self.refresh()


class ActionCatalog:
    """Validated catalog with search and execution-policy checks."""

    def __init__(
        self,
        server: ServerName,
        catalog_path: str,
        enabled_tools_path: str,
        review_registry_path: str = "",
    ) -> None:
        self.server = server
        self.actions = self._load(catalog_path)
        self.reviews = ReviewRegistry(
            review_registry_path or f"/app/config/review-registry-{server}.json",
            server,
            self.actions,
        )
        self.policy = EnabledPolicy(
            enabled_tools_path,
            server,
            self.actions,
            lambda name: self.review_status(name) == "approved",
        )
        self._validators = {
            name: Draft202012Validator(action.input_schema) for name, action in self.actions.items()
        }

    def _load(self, catalog_path: str) -> dict[str, ActionDefinition]:
        if catalog_path:
            raw_text = Path(catalog_path).read_text(encoding="utf-8")
        else:
            resource = resources.files("m365_mcp.catalogs").joinpath(f"{self.server}.json")
            raw_text = resource.read_text(encoding="utf-8")
        try:
            raw = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise CatalogError("invalid_catalog", f"Catalog is not valid JSON: {exc}") from exc
        if not isinstance(raw, list):
            raise CatalogError("invalid_catalog", "Catalog root must be an array")
        actions: dict[str, ActionDefinition] = {}
        for index, item in enumerate(raw):
            try:
                action = ActionDefinition.model_validate(item)
            except Exception as exc:
                raise CatalogError(
                    "invalid_catalog_entry",
                    f"Invalid catalog entry at index {index}: {exc}",
                ) from exc
            if action.backend != self.server:
                raise CatalogError(
                    "invalid_catalog_entry",
                    f"{action.name} belongs to {action.backend}, not {self.server}",
                )
            try:
                Draft202012Validator.check_schema(action.input_schema)
            except SchemaError as exc:
                raise CatalogError(
                    "invalid_input_schema",
                    f"Invalid JSON Schema for {action.name}: {exc.message}",
                ) from exc
            if action.name in actions:
                raise CatalogError("duplicate_action", f"Duplicate action name: {action.name}")
            actions[action.name] = action
        if not actions:
            raise CatalogError("empty_catalog", "Catalog must contain at least one action")
        return actions

    def validate_params(self, action: ActionDefinition, params: dict[str, Any]) -> None:
        errors = sorted(
            self._validators[action.name].iter_errors(params),
            key=lambda error: list(error.absolute_path),
        )
        if not errors:
            return
        details = []
        for error in errors[:20]:
            path = ".".join(str(part) for part in error.absolute_path) or "$"
            details.append({"path": path, "message": error.message})
        raise CatalogError(
            "invalid_parameters",
            f"Parameters for {action.name} do not match its input schema",
            {"validation_errors": details},
        )

    def require_action(
        self, name: str, *, operation: str, require_enabled: bool = True
    ) -> ActionDefinition:
        action = self.actions.get(name)
        if action is None:
            suggestions = self.search(name.replace("_", " "), limit=3, include_disabled=True)
            raise ActionNotFoundError(
                "action_not_found",
                f"Unknown action: {name}",
                {"suggestions": [item["name"] for item in suggestions]},
            )
        if action.operation_type != operation:
            raise ActionNotFoundError(
                "operation_mismatch",
                f"{name} is a {action.operation_type} action and cannot use execute_{operation}",
                {"correct_tool": f"execute_{action.operation_type}"},
            )
        if require_enabled and not self.is_enabled(action):
            raise PermissionError(
                f"{name} is not enabled. An inventory reviewer must set "
                f"review_status=approved and add it to enabledTools.{self.server}."
            )
        return action

    def review_status(self, name: str) -> str:
        action = self.actions[name]
        return self.reviews.status(action)

    def is_enabled(self, action: ActionDefinition) -> bool:
        return (
            action.name in self.policy.enabled
            and action.implementation_status == "implemented"
            and self.review_status(action.name) == "approved"
        )

    def _public(self, action: ActionDefinition) -> dict[str, Any]:
        enabled = self.is_enabled(action)
        payload = action.public_dict(runtime_enabled=enabled)
        review = self.reviews.info(action)
        payload["review_status"] = review["status"]
        payload["review"] = review
        if not enabled:
            if review["status"] != "approved":
                payload["disabled_reason"] = (
                    f"Review status is {review['status']}; approval is required."
                )
            elif action.implementation_status != "implemented":
                payload["disabled_reason"] = (
                    f"Implementation status is {action.implementation_status}."
                )
            else:
                payload["disabled_reason"] = "Action is absent from enabledTools."
        return payload

    def describe(self, name: str) -> dict[str, Any]:
        action = self.actions.get(name)
        if action is None:
            raise ActionNotFoundError("action_not_found", f"Unknown action: {name}")
        return self.public_action(action)

    def public_action(self, action: ActionDefinition) -> dict[str, Any]:
        """Return one action with runtime enabled and review state."""

        return self._public(action)

    def lexical_candidates(
        self,
        intent: str,
        *,
        domain: str | None = None,
        operation_type: str | None = None,
        include_disabled: bool = False,
    ) -> list[tuple[ActionDefinition, float]]:
        """Score every filtered candidate with the deterministic lexical scorer."""

        if not intent.strip():
            raise CatalogError("invalid_query", "intent must not be empty")
        if len(intent) > 2048:
            raise CatalogError("invalid_query", "intent must not exceed 2048 characters")
        parsed = analyze_query(intent)
        scored: list[tuple[ActionDefinition, float]] = []
        for action in self.actions.values():
            if not include_disabled and not self.is_enabled(action):
                continue
            if domain and not self.domain_matches(action.domain, domain):
                continue
            if operation_type and action.operation_type != operation_type:
                continue
            name = normalize_text(action.name)
            domain_text = normalize_text(action.domain)
            keywords = normalize_text(" ".join(action.keywords))
            description = normalize_text(action.description)
            haystack = " ".join((name, domain_text, keywords, description))
            score = concept_boost(parsed.concepts, name)
            if parsed.normalized and contains_phrase(haystack, parsed.normalized):
                score += 25
            for token in parsed.tokens:
                if token in name:
                    score += 9
                if token in keywords:
                    score += 7
                if token in domain_text:
                    score += 5
                if token in description:
                    score += 2
            for alias in parsed.aliases:
                if contains_phrase(haystack, alias):
                    score += 6
            if score > 0:
                if action.operation_type == "read":
                    score += 0.5
                if action.risk_level == "low":
                    score += 0.25
            scored.append((action, score))
        scored.sort(key=lambda item: (-item[1], item[0].name))
        return scored

    def search(
        self,
        intent: str,
        *,
        limit: int = 10,
        domain: str | None = None,
        operation_type: str | None = None,
        include_disabled: bool = False,
    ) -> list[dict[str, Any]]:
        scored = self.lexical_candidates(
            intent,
            domain=domain,
            operation_type=operation_type,
            include_disabled=include_disabled,
        )
        return [
            {
                **self.public_action(action),
                "score": round(score, 2),
            }
            for action, score in scored
            if score > 0
        ][:limit]

    def inventory(self, *, include_disabled: bool = True) -> list[dict[str, Any]]:
        actions = sorted(self.actions.values(), key=lambda item: (item.domain, item.name))
        return [
            self._public(action)
            for action in actions
            if include_disabled or self.is_enabled(action)
        ]

    @staticmethod
    def domain_matches(action_domain: str, requested: str) -> bool:
        return normalize_text(action_domain) == canonical_domain(requested)


def schema_validation_message(error: ValidationError) -> str:
    path = ".".join(str(part) for part in error.absolute_path) or "$"
    return f"{path}: {error.message}"
