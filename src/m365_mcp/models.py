"""Validated tool catalog models.

The catalog is the security boundary: both executors may only dispatch fixed
Graph endpoints or fixed PowerShell cmdlets declared here. Free-form URLs,
HTTP methods, scripts and cmdlets are unrepresentable.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

OperationType = Literal["read", "write"]
RiskLevel = Literal["low", "medium", "high", "critical"]
ImplementationStatus = Literal["implemented", "planned", "unsupported"]
ReviewStatus = Literal["pending", "approved", "rejected"]
Backend = Literal["graph", "powershell"]

_ACTION_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
_CMDLET_RE = re.compile(
    r"^(?:Get|New|Set|Remove|Add|Update|Clear|Enable|Disable|Grant|Revoke|Search|Start|Stop|"
    r"Release|Preview)-[A-Za-z][A-Za-z0-9]+$"
)
_PATH_RE = re.compile(r"^/(?!/)[A-Za-z0-9_./{}$:'()=-]+$")
_BLOCKED_CMDLETS = {
    "Invoke-Expression",
    "Invoke-Command",
    "Invoke-RestMethod",
    "Invoke-WebRequest",
    "Start-Process",
    "New-Object",
    "Add-Type",
    "Set-ExecutionPolicy",
    "Import-Module",
    "Remove-Item",
}
_TRUSTED_DOC_HOSTS = ("https://learn.microsoft.com/", "https://pnp.github.io/")


class GraphRequestSpec(BaseModel):
    """A fixed Microsoft Graph operation."""

    model_config = ConfigDict(extra="forbid")

    method: Literal["GET", "POST", "PATCH", "PUT", "DELETE"]
    path: str
    query: dict[str, str] = Field(default_factory=dict)
    body: dict[str, str] = Field(default_factory=dict)
    body_const: dict[str, Any] = Field(default_factory=dict)
    response: Literal["json", "text"] = "json"

    @model_validator(mode="after")
    def _validate_request(self) -> GraphRequestSpec:
        if not _PATH_RE.fullmatch(self.path):
            raise ValueError(f"unsafe Graph path: {self.path!r}")
        if "?" in self.path or "#" in self.path or "//" in self.path:
            raise ValueError("Graph path must not contain query, fragment or empty segments")
        if self.method in {"GET", "DELETE"} and (self.body or self.body_const):
            raise ValueError(f"{self.method} action cannot declare a request body")
        if self.body == {"payload": "payload"} and self.body_const:
            raise ValueError("payload body mode cannot be combined with body_const")
        return self


class PowerShellRequestSpec(BaseModel):
    """A fixed PowerShell cmdlet invocation."""

    model_config = ConfigDict(extra="forbid")

    service: Literal["exchange", "compliance", "teams", "sharepoint"]
    cmdlet: str
    parameters: dict[str, str] = Field(default_factory=dict)
    constant_parameters: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_command(self) -> PowerShellRequestSpec:
        if not _CMDLET_RE.fullmatch(self.cmdlet):
            raise ValueError(f"unsafe cmdlet name: {self.cmdlet!r}")
        if self.cmdlet in _BLOCKED_CMDLETS:
            raise ValueError(f"blocked cmdlet: {self.cmdlet}")
        for business_name, ps_name in self.parameters.items():
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", ps_name):
                raise ValueError(
                    f"invalid PowerShell parameter mapping for {business_name}: {ps_name!r}"
                )
        for name in self.constant_parameters:
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name):
                raise ValueError(f"invalid constant PowerShell parameter: {name!r}")
        overlap = set(self.parameters.values()) & set(self.constant_parameters)
        if overlap:
            raise ValueError(f"parameters both mapped and constant: {sorted(overlap)}")
        return self


class ActionDefinition(BaseModel):
    """One reviewable M365 business action."""

    model_config = ConfigDict(extra="forbid")

    name: str
    domain: str = Field(min_length=2, max_length=64)
    backend: Backend
    description: str = Field(min_length=5, max_length=1000)
    required_permissions: list[str] = Field(min_length=1)
    operation_type: OperationType
    risk_level: RiskLevel
    implementation_status: ImplementationStatus
    review_status: ReviewStatus = "pending"
    enabled: bool = False
    keywords: list[str] = Field(default_factory=list)
    docs_url: str
    input_schema: dict[str, Any]
    graph: GraphRequestSpec | None = None
    powershell: PowerShellRequestSpec | None = None

    @model_validator(mode="after")
    def _validate_action(self) -> ActionDefinition:
        if not _ACTION_NAME_RE.fullmatch(self.name):
            raise ValueError(f"invalid action name: {self.name!r}")
        if self.enabled:
            raise ValueError(
                "catalog actions must default disabled; use enabledTools config after review"
            )
        if not self.docs_url.startswith(_TRUSTED_DOC_HOSTS):
            raise ValueError(f"untrusted docs_url: {self.docs_url!r}")
        self._validate_schema()
        if self.backend == "graph":
            if self.powershell is not None:
                raise ValueError("Graph action cannot contain a PowerShell request")
            if self.implementation_status == "implemented" and self.graph is None:
                raise ValueError("implemented Graph action requires a graph request")
            if self.graph is not None:
                self._validate_graph_bindings(self.graph)
        else:
            if self.graph is not None:
                raise ValueError("PowerShell action cannot contain a Graph request")
            if self.implementation_status == "implemented" and self.powershell is None:
                raise ValueError("implemented PowerShell action requires a powershell request")
            if self.powershell is not None:
                self._validate_powershell_bindings(self.powershell)
        return self

    def _validate_schema(self) -> None:
        if self.input_schema.get("type") != "object":
            raise ValueError("input_schema.type must be object")
        if self.input_schema.get("additionalProperties") is not False:
            raise ValueError("input_schema.additionalProperties must be false")
        properties = self.input_schema.get("properties", {})
        if not isinstance(properties, dict):
            raise ValueError("input_schema.properties must be an object")
        required = self.input_schema.get("required", [])
        if not isinstance(required, list) or any(item not in properties for item in required):
            raise ValueError("input_schema.required contains unknown properties")
        for prop_name, prop_schema in properties.items():
            if not isinstance(prop_schema, dict):
                raise ValueError(f"input schema property {prop_name!r} must be an object")
            if "description" not in prop_schema:
                raise ValueError(f"input schema property {prop_name!r} needs a description")

    def _validate_graph_bindings(self, request: GraphRequestSpec) -> None:
        props = set(self.input_schema.get("properties", {}))
        placeholders = set(re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", request.path))
        unknown = placeholders - props
        if unknown:
            raise ValueError(f"path placeholders missing from schema: {sorted(unknown)}")
        required = set(self.input_schema.get("required", []))
        optional_placeholders = placeholders - required
        if optional_placeholders:
            raise ValueError(f"path placeholders must be required: {sorted(optional_placeholders)}")
        if request.method == "GET" and self.operation_type != "read":
            raise ValueError("GET must be a read action")
        if request.method in {"DELETE"} and self.operation_type != "write":
            raise ValueError("DELETE must be a write action")
        for source in request.query.values():
            if source not in props:
                raise ValueError(f"query source {source!r} is not in input_schema")
        for source in request.body.values():
            if source not in props:
                raise ValueError(f"body source {source!r} is not in input_schema")
        if request.body == {"payload": "payload"} and "payload" not in props:
            raise ValueError("payload body mode requires a payload property")
        if (placement := placeholders & set(request.body.values())) and len(placeholders) != 1:
            raise ValueError(f"ambiguous path/body parameter(s): {sorted(placement)}")
        for placeholder in self._constant_placeholders(request.body_const):
            if placeholder not in props:
                raise ValueError(
                    f"body_const placeholder {placeholder!r} is missing from input_schema"
                )
            if placeholder not in required:
                raise ValueError(f"body_const placeholder {placeholder!r} must be required")

    @classmethod
    def _constant_placeholders(cls, value: Any) -> set[str]:
        if isinstance(value, str):
            return set(re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", value))
        if isinstance(value, dict):
            found: set[str] = set()
            for item in value.values():
                found |= cls._constant_placeholders(item)
            return found
        if isinstance(value, list):
            found = set()
            for item in value:
                found |= cls._constant_placeholders(item)
            return found
        return set()

    def _validate_powershell_bindings(self, request: PowerShellRequestSpec) -> None:
        props = set(self.input_schema.get("properties", {}))
        unknown = set(request.parameters) - props
        if unknown:
            raise ValueError(f"PowerShell parameter sources missing from schema: {sorted(unknown)}")
        if (
            request.cmdlet.startswith(("Get-", "Search-", "Preview-"))
            and self.operation_type != "read"
        ):
            raise ValueError(f"{request.cmdlet} must be classified as read")
        if request.cmdlet.startswith(("Remove-", "Clear-")) and self.operation_type != "write":
            raise ValueError(f"{request.cmdlet} must be classified as write")
        if request.cmdlet == "Clear-MobileDevice" and self.risk_level != "critical":
            raise ValueError("device wipe must be critical risk")

    def public_dict(self, *, runtime_enabled: bool) -> dict[str, Any]:
        """Return fields safe to expose to an MCP caller."""

        return {
            "name": self.name,
            "title": self.name.replace("_", " ").title(),
            "domain": self.domain,
            "backend": self.backend,
            "description": self.description,
            "required_permissions": self.required_permissions,
            "operation_type": self.operation_type,
            "risk_level": self.risk_level,
            "implementation_status": self.implementation_status,
            "review_status": self.review_status,
            "enabled": runtime_enabled,
            "keywords": self.keywords,
            "docs_url": self.docs_url,
            "input_schema": self.input_schema,
        }

    def inventory_dict(self, *, runtime_enabled: bool) -> dict[str, Any]:
        return self.public_dict(runtime_enabled=runtime_enabled)
