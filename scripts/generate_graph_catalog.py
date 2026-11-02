#!/usr/bin/env python3
"""Generate the reviewed-shape Graph action catalog.

This script is the source of truth for graph.json. Every action has one fixed
Graph v1.0 endpoint and an explicit JSON input schema. Run it after changing
action definitions:

    uv run python scripts/generate_graph_catalog.py

Review status remains pending and every action remains disabled. Use
`m365-inventory review` and `m365-inventory enable` after security review.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "src/m365_mcp/catalogs/graph.json"


def prop(kind: str, description: str, **extra: Any) -> dict[str, Any]:
    value: dict[str, Any] = {"type": kind, "description": description}
    value.update(extra)
    return value


def obj(description: str) -> dict[str, Any]:
    return {"type": "object", "description": description}


def array(kind: str, description: str, **extra: Any) -> dict[str, Any]:
    return {"type": "array", "items": {"type": kind}, "description": description, **extra}


def schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": required or [],
    }


def page_props(
    *, filter_: bool = True, select_: bool = True, search: bool = False, expand: bool = False
) -> dict[str, Any]:
    props: dict[str, Any] = {
        "limit": prop(
            "integer",
            "Maximum records per Graph page, from 1 to 999.",
            minimum=1,
            maximum=999,
        )
    }
    if filter_:
        props["filter"] = prop(
            "string",
            "OData filter expression passed as $filter. Use Graph field names.",
            maxLength=1024,
        )
    if select_:
        props["select"] = array(
            "string", "Fields returned in the response, passed as $select.", maxItems=100
        )
    if search:
        props["search"] = prop(
            "string",
            'Graph $search expression, including quotes where required, for example "displayName:Alice".',
            maxLength=512,
        )
    if expand:
        props["expand"] = array(
            "string", "Navigation properties to expand, passed as $expand.", maxItems=20
        )
    return props


def make(
    *,
    name: str,
    domain: str,
    description: str,
    permissions: list[str],
    operation: str,
    risk: str,
    docs: str,
    path: str | None,
    method: str,
    params: dict[str, Any],
    required: list[str] | None = None,
    query: dict[str, str] | None = None,
    body: dict[str, str] | None = None,
    body_const: dict[str, Any] | None = None,
    response: str = "json",
    status: str = "implemented",
    keywords: list[str] | None = None,
) -> dict[str, Any]:
    graph = None
    if path is not None:
        graph = {
            "method": method,
            "path": path,
            "query": query or {},
            "body": body or {},
            "body_const": body_const or {},
            "response": response,
        }
    return {
        "name": name,
        "domain": domain,
        "backend": "graph",
        "description": description,
        "required_permissions": permissions,
        "operation_type": operation,
        "risk_level": risk,
        "implementation_status": status,
        "review_status": "pending",
        "enabled": False,
        "keywords": keywords or [],
        "docs_url": docs,
        "input_schema": schema(params, required),
        "graph": graph,
    }


def read(
    *,
    name: str,
    domain: str,
    description: str,
    permissions: list[str],
    path: str,
    docs: str,
    params: dict[str, Any] | None = None,
    required: list[str] | None = None,
    query: dict[str, str] | None = None,
    response: str = "json",
    risk: str = "low",
    keywords: list[str] | None = None,
) -> dict[str, Any]:
    return make(
        name=name,
        domain=domain,
        description=description,
        permissions=permissions,
        operation="read",
        risk=risk,
        docs=docs,
        path=path,
        method="GET",
        params=params or {},
        required=required,
        query=query,
        response=response,
        keywords=keywords,
    )


def write(
    *,
    name: str,
    domain: str,
    description: str,
    permissions: list[str],
    path: str,
    method: str,
    docs: str,
    params: dict[str, Any] | None = None,
    required: list[str] | None = None,
    query: dict[str, str] | None = None,
    body: dict[str, str] | None = None,
    body_const: dict[str, Any] | None = None,
    risk: str = "medium",
    keywords: list[str] | None = None,
) -> dict[str, Any]:
    return make(
        name=name,
        domain=domain,
        description=description,
        permissions=permissions,
        operation="write",
        risk=risk,
        docs=docs,
        path=path,
        method=method,
        params=params or {},
        required=required,
        query=query,
        body=body,
        body_const=body_const,
        keywords=keywords,
    )


def payload_props(
    description: str = "Graph request payload documented by the linked endpoint.",
) -> dict[str, Any]:
    return {"payload": obj(description)}


def ident_props() -> dict[str, Any]:
    return {"id": prop("string", "Microsoft Graph object identifier.", minLength=1, maxLength=512)}


def entra_actions() -> list[dict[str, Any]]:
    base = "https://learn.microsoft.com/en-us/graph/api"
    d = "Entra ID"
    actions: list[dict[str, Any]] = [
        read(
            name="entra_list_users",
            domain=d,
            description="List tenant users with optional server-side filtering and field selection.",
            permissions=["User.Read.All"],
            path="/users",
            docs=f"{base}/user-list",
            params=page_props(search=True, expand=False),
            query={"$top": "limit", "$filter": "filter", "$select": "select", "$search": "search"},
            keywords=["user", "account", "list", "directory"],
        ),
        read(
            name="entra_get_user",
            domain=d,
            description="Get one user by object ID or user principal name.",
            permissions=["User.Read.All"],
            path="/users/{user_id}",
            docs=f"{base}/user-get",
            params={
                "user_id": prop(
                    "string", "User object ID or user principal name.", minLength=1, maxLength=320
                ),
                "select": array("string", "Fields to return.", maxItems=100),
            },
            required=["user_id"],
            query={"$select": "select"},
            keywords=["user", "account", "profile"],
        ),
        write(
            name="entra_create_user",
            domain=d,
            description="Create a user from a Graph user payload, including account and password profile.",
            permissions=["User.ReadWrite.All"],
            path="/users",
            method="POST",
            docs=f"{base}/user-post",
            params=payload_props(
                "Graph user object; passwordProfile is required by Graph when creating a user."
            ),
            required=["payload"],
            body={"payload": "payload"},
            risk="high",
            keywords=["user", "create", "provision"],
        ),
        write(
            name="entra_update_user",
            domain=d,
            description="Update selected properties on an existing user from a Graph user payload.",
            permissions=["User.ReadWrite.All"],
            path="/users/{user_id}",
            method="PATCH",
            docs=f"{base}/user-update",
            params={
                "user_id": prop(
                    "string", "User object ID or user principal name.", minLength=1, maxLength=320
                ),
                **payload_props(),
            },
            required=["user_id", "payload"],
            body={"payload": "payload"},
            risk="medium",
            keywords=["user", "update", "account"],
        ),
        write(
            name="entra_delete_user",
            domain=d,
            description="Soft-delete a user. The object remains recoverable from deleted items until retention expires.",
            permissions=["User.ReadWrite.All"],
            path="/users/{user_id}",
            method="DELETE",
            docs=f"{base}/user-delete",
            params={
                "user_id": prop(
                    "string", "User object ID or user principal name.", minLength=1, maxLength=320
                )
            },
            required=["user_id"],
            risk="critical",
            keywords=["user", "delete", "disable", "offboarding"],
        ),
        write(
            name="entra_assign_user_licenses",
            domain=d,
            description="Add or remove license assignments for one user.",
            permissions=["User.ReadWrite.All", "Directory.ReadWrite.All"],
            path="/users/{user_id}/assignLicense",
            method="POST",
            docs=f"{base}/user-assignlicense",
            params={
                "user_id": prop(
                    "string", "User object ID or user principal name.", minLength=1, maxLength=320
                ),
                **payload_props("Payload with addLicenses and removeLicenses arrays."),
            },
            required=["user_id", "payload"],
            body={"payload": "payload"},
            risk="high",
            keywords=["license", "sku", "assign", "user"],
        ),
        read(
            name="entra_list_groups",
            domain=d,
            description="List security groups and Microsoft 365 groups with optional filtering.",
            permissions=["Group.Read.All"],
            path="/groups",
            docs=f"{base}/group-list",
            params=page_props(search=True),
            query={"$top": "limit", "$filter": "filter", "$select": "select", "$search": "search"},
            keywords=["group", "list", "security", "directory"],
        ),
        read(
            name="entra_get_group",
            domain=d,
            description="Get a group by object ID.",
            permissions=["Group.Read.All"],
            path="/groups/{group_id}",
            docs=f"{base}/group-get",
            params={
                "group_id": prop("string", "Group object ID.", minLength=1, maxLength=512),
                "select": array("string", "Fields to return.", maxItems=100),
            },
            required=["group_id"],
            query={"$select": "select"},
            keywords=["group", "detail"],
        ),
        write(
            name="entra_create_group",
            domain=d,
            description="Create a security group or Microsoft 365 group from a Graph group payload.",
            permissions=["Group.ReadWrite.All"],
            path="/groups",
            method="POST",
            docs=f"{base}/group-post",
            params=payload_props(
                "Graph group object with displayName, mailEnabled, securityEnabled and groupTypes."
            ),
            required=["payload"],
            body={"payload": "payload"},
            risk="medium",
            keywords=["group", "create"],
        ),
        write(
            name="entra_update_group",
            domain=d,
            description="Update properties on an existing group.",
            permissions=["Group.ReadWrite.All"],
            path="/groups/{group_id}",
            method="PATCH",
            docs=f"{base}/group-update",
            params={
                "group_id": prop("string", "Group object ID.", minLength=1, maxLength=512),
                **payload_props(),
            },
            required=["group_id", "payload"],
            body={"payload": "payload"},
            risk="medium",
            keywords=["group", "update"],
        ),
        write(
            name="entra_delete_group",
            domain=d,
            description="Delete a group. Microsoft 365 group deletion also removes its connected workspace.",
            permissions=["Group.ReadWrite.All"],
            path="/groups/{group_id}",
            method="DELETE",
            docs=f"{base}/group-delete",
            params={"group_id": prop("string", "Group object ID.", minLength=1, maxLength=512)},
            required=["group_id"],
            risk="critical",
            keywords=["group", "delete"],
        ),
        read(
            name="entra_list_group_members",
            domain=d,
            description="List direct members of a group.",
            permissions=["GroupMember.Read.All"],
            path="/groups/{group_id}/members",
            docs=f"{base}/group-list-members",
            params={
                "group_id": prop("string", "Group object ID.", minLength=1, maxLength=512),
                **page_props(filter_=False, select_=True),
            },
            required=["group_id"],
            query={"$top": "limit", "$select": "select"},
            keywords=["group", "member", "membership"],
        ),
        read(
            name="entra_list_group_owners",
            domain=d,
            description="List owners of a group.",
            permissions=["GroupMember.Read.All"],
            path="/groups/{group_id}/owners",
            docs=f"{base}/group-list-owners",
            params={"group_id": prop("string", "Group object ID.", minLength=1, maxLength=512)},
            required=["group_id"],
            keywords=["group", "owner"],
        ),
        write(
            name="entra_add_group_member",
            domain=d,
            description="Add a directory object to a group using an object reference.",
            permissions=["GroupMember.ReadWrite.All"],
            path="/groups/{group_id}/members/$ref",
            method="POST",
            docs=f"{base}/group-post-members",
            params={
                "group_id": prop("string", "Group object ID.", minLength=1, maxLength=512),
                "member_id": prop(
                    "string", "Directory object ID to add.", minLength=1, maxLength=512
                ),
            },
            required=["group_id", "member_id"],
            body_const={
                "@odata.id": "https://graph.microsoft.com/v1.0/directoryObjects/{member_id}"
            },
            risk="high",
            keywords=["group", "member", "add"],
        ),
        write(
            name="entra_remove_group_member",
            domain=d,
            description="Remove a direct member from a group.",
            permissions=["GroupMember.ReadWrite.All"],
            path="/groups/{group_id}/members/{member_id}/$ref",
            method="DELETE",
            docs=f"{base}/group-delete-members",
            params={
                "group_id": prop("string", "Group object ID.", minLength=1, maxLength=512),
                "member_id": prop(
                    "string", "Directory object ID to remove.", minLength=1, maxLength=512
                ),
            },
            required=["group_id", "member_id"],
            risk="high",
            keywords=["group", "member", "remove"],
        ),
        read(
            name="entra_list_sign_in_logs",
            domain=d,
            description="List Entra sign-in logs for troubleshooting and identity investigations.",
            permissions=["AuditLog.Read.All"],
            path="/auditLogs/signIns",
            docs=f"{base}/signin-list",
            params=page_props(filter_=True, select_=False),
            query={"$top": "limit", "$filter": "filter"},
            risk="medium",
            keywords=["sign-in", "login", "audit", "log"],
        ),
        read(
            name="entra_get_sign_in_log",
            domain=d,
            description="Get one sign-in log event by event ID.",
            permissions=["AuditLog.Read.All"],
            path="/auditLogs/signIns/{sign_in_id}",
            docs=f"{base}/signin-get",
            params={
                "sign_in_id": prop(
                    "string", "Sign-in event identifier.", minLength=1, maxLength=512
                )
            },
            required=["sign_in_id"],
            risk="medium",
            keywords=["sign-in", "login", "audit"],
        ),
        read(
            name="entra_list_directory_audits",
            domain=d,
            description="List directory audit events for changes to Entra objects.",
            permissions=["AuditLog.Read.All"],
            path="/auditLogs/directoryAudits",
            docs=f"{base}/directoryaudit-list",
            params=page_props(filter_=False, select_=False),
            query={"$top": "limit"},
            risk="medium",
            keywords=["audit", "directory", "change"],
        ),
        read(
            name="entra_list_auth_methods",
            domain=d,
            description="List registered authentication methods for one user.",
            permissions=["UserAuthenticationMethod.Read.All"],
            path="/users/{user_id}/authenticationMethods",
            docs=f"{base}/authenticationmethods-list",
            params={"user_id": prop("string", "User object ID.", minLength=1, maxLength=512)},
            required=["user_id"],
            risk="medium",
            keywords=["mfa", "authentication", "method"],
        ),
        write(
            name="entra_delete_auth_method",
            domain=d,
            description="Delete one authentication method from a user. Removing the last method can lock the user out.",
            permissions=["UserAuthenticationMethod.ReadWrite.All"],
            path="/users/{user_id}/authenticationMethods/{method_id}",
            method="DELETE",
            docs=f"{base}/authenticationmethod-delete",
            params={
                "user_id": prop("string", "User object ID.", minLength=1, maxLength=512),
                "method_id": prop(
                    "string", "Authentication method identifier.", minLength=1, maxLength=512
                ),
            },
            required=["user_id", "method_id"],
            risk="critical",
            keywords=["mfa", "authentication", "delete"],
        ),
        read(
            name="entra_mfa_registration_report",
            domain=d,
            description="Read per-user MFA and passwordless registration status from the authentication methods report.",
            permissions=["Reports.Read.All"],
            path="/reports/authenticationMethods/userRegistrationDetails",
            docs=f"{base}/userregistrationdetails-list",
            params=page_props(filter_=True, select_=False),
            query={"$top": "limit", "$filter": "filter"},
            risk="medium",
            keywords=["mfa", "registration", "report"],
        ),
        read(
            name="entra_list_conditional_access_policies",
            domain=d,
            description="List Conditional Access policies and their state.",
            permissions=["Policy.Read.All"],
            path="/identity/conditionalAccess/policies",
            docs=f"{base}/conditionalaccesspolicy-list",
            params=page_props(select_=False),
            query={"$top": "limit", "$filter": "filter"},
            risk="medium",
            keywords=["conditional access", "policy", "zero trust"],
        ),
        read(
            name="entra_get_conditional_access_policy",
            domain=d,
            description="Get one Conditional Access policy by identifier.",
            permissions=["Policy.Read.All"],
            path="/identity/conditionalAccess/policies/{policy_id}",
            docs=f"{base}/conditionalaccesspolicy-get",
            params={
                "policy_id": prop(
                    "string", "Conditional Access policy ID.", minLength=1, maxLength=512
                )
            },
            required=["policy_id"],
            risk="medium",
            keywords=["conditional access", "policy"],
        ),
        write(
            name="entra_create_conditional_access_policy",
            domain=d,
            description="Create a Conditional Access policy from a complete Graph policy payload.",
            permissions=["Policy.ReadWrite.ConditionalAccess"],
            path="/identity/conditionalAccess/policies",
            method="POST",
            docs=f"{base}/conditionalaccesspolicy-post",
            params=payload_props(
                "Conditional Access policy including conditions, grantControls and state."
            ),
            required=["payload"],
            body={"payload": "payload"},
            risk="critical",
            keywords=["conditional access", "policy", "create"],
        ),
        write(
            name="entra_update_conditional_access_policy",
            domain=d,
            description="Update a Conditional Access policy from a Graph policy payload.",
            permissions=["Policy.ReadWrite.ConditionalAccess"],
            path="/identity/conditionalAccess/policies/{policy_id}",
            method="PATCH",
            docs=f"{base}/conditionalaccesspolicy-update",
            params={
                "policy_id": prop(
                    "string", "Conditional Access policy ID.", minLength=1, maxLength=512
                ),
                **payload_props(),
            },
            required=["policy_id", "payload"],
            body={"payload": "payload"},
            risk="critical",
            keywords=["conditional access", "policy", "update"],
        ),
        write(
            name="entra_delete_conditional_access_policy",
            domain=d,
            description="Delete a Conditional Access policy. This can immediately weaken tenant access controls.",
            permissions=["Policy.ReadWrite.ConditionalAccess"],
            path="/identity/conditionalAccess/policies/{policy_id}",
            method="DELETE",
            docs=f"{base}/conditionalaccesspolicy-delete",
            params={
                "policy_id": prop(
                    "string", "Conditional Access policy ID.", minLength=1, maxLength=512
                )
            },
            required=["policy_id"],
            risk="critical",
            keywords=["conditional access", "policy", "delete"],
        ),
        read(
            name="entra_list_applications",
            domain=d,
            description="List Entra application registrations.",
            permissions=["Application.Read.All"],
            path="/applications",
            docs=f"{base}/application-list",
            params=page_props(filter_=True, select_=True),
            query={"$top": "limit", "$filter": "filter", "$select": "select"},
            risk="medium",
            keywords=["application", "app registration", "list"],
        ),
        read(
            name="entra_get_application",
            domain=d,
            description="Get one application registration by object ID.",
            permissions=["Application.Read.All"],
            path="/applications/{application_id}",
            docs=f"{base}/application-get",
            params={
                "application_id": prop(
                    "string", "Application object ID.", minLength=1, maxLength=512
                )
            },
            required=["application_id"],
            risk="medium",
            keywords=["application", "app registration"],
        ),
        write(
            name="entra_create_application",
            domain=d,
            description="Create an application registration from a Graph application payload.",
            permissions=["Application.ReadWrite.All"],
            path="/applications",
            method="POST",
            docs=f"{base}/application-post",
            params=payload_props(
                "Application object including displayName and required configuration."
            ),
            required=["payload"],
            body={"payload": "payload"},
            risk="high",
            keywords=["application", "create", "registration"],
        ),
        write(
            name="entra_update_application",
            domain=d,
            description="Update an application registration from a Graph application payload.",
            permissions=["Application.ReadWrite.All"],
            path="/applications/{application_id}",
            method="PATCH",
            docs=f"{base}/application-update",
            params={
                "application_id": prop(
                    "string", "Application object ID.", minLength=1, maxLength=512
                ),
                **payload_props(),
            },
            required=["application_id", "payload"],
            body={"payload": "payload"},
            risk="high",
            keywords=["application", "update"],
        ),
        write(
            name="entra_delete_application",
            domain=d,
            description="Delete an application registration. Existing service principals must be reviewed separately.",
            permissions=["Application.ReadWrite.All"],
            path="/applications/{application_id}",
            method="DELETE",
            docs=f"{base}/application-delete",
            params={
                "application_id": prop(
                    "string", "Application object ID.", minLength=1, maxLength=512
                )
            },
            required=["application_id"],
            risk="critical",
            keywords=["application", "delete"],
        ),
        read(
            name="entra_list_service_principals",
            domain=d,
            description="List service principals in the tenant.",
            permissions=["Application.Read.All"],
            path="/servicePrincipals",
            docs=f"{base}/serviceprincipal-list",
            params=page_props(filter_=True, select_=True),
            query={"$top": "limit", "$filter": "filter", "$select": "select"},
            risk="medium",
            keywords=["service principal", "enterprise app", "list"],
        ),
        read(
            name="entra_get_service_principal",
            domain=d,
            description="Get one service principal by object ID.",
            permissions=["Application.Read.All"],
            path="/servicePrincipals/{service_principal_id}",
            docs=f"{base}/serviceprincipal-get",
            params={
                "service_principal_id": prop(
                    "string", "Service principal object ID.", minLength=1, maxLength=512
                )
            },
            required=["service_principal_id"],
            risk="medium",
            keywords=["service principal", "enterprise app"],
        ),
        write(
            name="entra_create_service_principal",
            domain=d,
            description="Create a service principal from an existing application appId.",
            permissions=["Application.ReadWrite.All"],
            path="/servicePrincipals",
            method="POST",
            docs=f"{base}/serviceprincipal-post",
            params=payload_props("Service principal object, including appId."),
            required=["payload"],
            body={"payload": "payload"},
            risk="high",
            keywords=["service principal", "create"],
        ),
        write(
            name="entra_update_service_principal",
            domain=d,
            description="Update selected properties on a service principal.",
            permissions=["Application.ReadWrite.All"],
            path="/servicePrincipals/{service_principal_id}",
            method="PATCH",
            docs=f"{base}/serviceprincipal-update",
            params={
                "service_principal_id": prop(
                    "string", "Service principal object ID.", minLength=1, maxLength=512
                ),
                **payload_props(),
            },
            required=["service_principal_id", "payload"],
            body={"payload": "payload"},
            risk="high",
            keywords=["service principal", "update"],
        ),
        write(
            name="entra_delete_service_principal",
            domain=d,
            description="Delete a service principal, removing the enterprise application from the tenant.",
            permissions=["Application.ReadWrite.All"],
            path="/servicePrincipals/{service_principal_id}",
            method="DELETE",
            docs=f"{base}/serviceprincipal-delete",
            params={
                "service_principal_id": prop(
                    "string", "Service principal object ID.", minLength=1, maxLength=512
                )
            },
            required=["service_principal_id"],
            risk="critical",
            keywords=["service principal", "delete"],
        ),
        read(
            name="entra_list_directory_roles",
            domain=d,
            description="List activated directory roles in the tenant.",
            permissions=["Directory.Read.All"],
            path="/directoryRoles",
            docs=f"{base}/directoryrole-list",
            params={},
            risk="medium",
            keywords=["role", "admin", "directory"],
        ),
        read(
            name="entra_list_role_definitions",
            domain=d,
            description="List directory role definitions and their permission sets.",
            permissions=["RoleManagement.Read.Directory"],
            path="/roleManagement/directory/roleDefinitions",
            docs=f"{base}/rbacapplication-list-roledefinitions",
            params={},
            risk="medium",
            keywords=["role", "definition", "permission"],
        ),
        read(
            name="entra_list_role_assignments",
            domain=d,
            description="List active directory role assignments.",
            permissions=["RoleManagement.Read.Directory"],
            path="/roleManagement/directory/roleAssignments",
            docs=f"{base}/rbacapplication-list-roleassignments",
            params=page_props(select_=False),
            query={"$top": "limit", "$filter": "filter"},
            risk="medium",
            keywords=["role", "assignment", "admin"],
        ),
        write(
            name="entra_assign_directory_role",
            domain=d,
            description="Create a directory role assignment from a Graph roleAssignment payload.",
            permissions=["RoleManagement.ReadWrite.Directory"],
            path="/roleManagement/directory/roleAssignments",
            method="POST",
            docs=f"{base}/rbacapplication-post-roleassignments",
            params=payload_props(
                "Role assignment payload with principalId, roleDefinitionId and directoryScopeId."
            ),
            required=["payload"],
            body={"payload": "payload"},
            risk="critical",
            keywords=["role", "assign", "admin", "privilege"],
        ),
        write(
            name="entra_remove_directory_role_assignment",
            domain=d,
            description="Delete an active directory role assignment.",
            permissions=["RoleManagement.ReadWrite.Directory"],
            path="/roleManagement/directory/roleAssignments/{assignment_id}",
            method="DELETE",
            docs=f"{base}/rbacapplication-delete-roleassignments",
            params={
                "assignment_id": prop("string", "Role assignment ID.", minLength=1, maxLength=512)
            },
            required=["assignment_id"],
            risk="critical",
            keywords=["role", "remove", "admin"],
        ),
        read(
            name="entra_list_devices",
            domain=d,
            description="List Entra device objects registered or joined to the tenant.",
            permissions=["Device.Read.All"],
            path="/devices",
            docs=f"{base}/device-list",
            params=page_props(filter_=True, select_=True),
            query={"$top": "limit", "$filter": "filter", "$select": "select"},
            risk="low",
            keywords=["device", "entra", "registered"],
        ),
        read(
            name="entra_get_device",
            domain=d,
            description="Get one Entra device object by ID.",
            permissions=["Device.Read.All"],
            path="/devices/{device_id}",
            docs=f"{base}/device-get",
            params={"device_id": prop("string", "Device object ID.", minLength=1, maxLength=512)},
            required=["device_id"],
            keywords=["device", "entra"],
        ),
        read(
            name="entra_list_domains",
            domain=d,
            description="List verified and unverified tenant domains.",
            permissions=["Domain.Read.All"],
            path="/domains",
            docs=f"{base}/domain-list",
            params={},
            keywords=["domain", "tenant", "dns"],
        ),
        read(
            name="entra_get_organization",
            domain=d,
            description="Read tenant organization profile and verified domain information.",
            permissions=["Organization.Read.All"],
            path="/organization",
            docs=f"{base}/organization-get",
            params={},
            keywords=["organization", "tenant", "profile"],
        ),
    ]
    return actions


def intune_actions() -> list[dict[str, Any]]:
    base = "https://learn.microsoft.com/en-us/graph/api"
    d = "Intune"
    actions: list[dict[str, Any]] = [
        read(
            name="intune_list_managed_devices",
            domain=d,
            description="List Intune managed devices with optional filters.",
            permissions=["DeviceManagementManagedDevices.Read.All"],
            path="/deviceManagement/managedDevices",
            docs=f"{base}/intune-manageddevice-list",
            params=page_props(filter_=True, select_=True),
            query={"$top": "limit", "$filter": "filter", "$select": "select"},
            risk="low",
            keywords=["intune", "device", "managed", "inventory"],
        ),
        read(
            name="intune_get_managed_device",
            domain=d,
            description="Get one Intune managed device and its compliance and ownership state.",
            permissions=["DeviceManagementManagedDevices.Read.All"],
            path="/deviceManagement/managedDevices/{device_id}",
            docs=f"{base}/intune-manageddevice-get",
            params={
                "device_id": prop("string", "Intune managed device ID.", minLength=1, maxLength=512)
            },
            required=["device_id"],
            keywords=["intune", "device", "detail"],
        ),
        write(
            name="intune_update_managed_device",
            domain=d,
            description="Update selected Intune managed device properties from a Graph payload.",
            permissions=["DeviceManagementManagedDevices.ReadWrite.All"],
            path="/deviceManagement/managedDevices/{device_id}",
            method="PATCH",
            docs=f"{base}/intune-manageddevice-update",
            params={
                "device_id": prop(
                    "string", "Intune managed device ID.", minLength=1, maxLength=512
                ),
                **payload_props(),
            },
            required=["device_id", "payload"],
            body={"payload": "payload"},
            risk="high",
            keywords=["intune", "device", "update"],
        ),
        write(
            name="intune_delete_managed_device",
            domain=d,
            description="Delete an Intune managed device record without wiping the physical device.",
            permissions=["DeviceManagementManagedDevices.PrivilegedOperations.All"],
            path="/deviceManagement/managedDevices/{device_id}",
            method="DELETE",
            docs=f"{base}/intune-manageddevice-delete",
            params={
                "device_id": prop("string", "Intune managed device ID.", minLength=1, maxLength=512)
            },
            required=["device_id"],
            risk="critical",
            keywords=["intune", "device", "delete", "record"],
        ),
        read(
            name="intune_get_managed_device_overview",
            domain=d,
            description="Read aggregate Intune managed device counts and platform summary.",
            permissions=["DeviceManagementManagedDevices.Read.All"],
            path="/deviceManagement/managedDeviceOverview",
            docs=f"{base}/intune-manageddeviceoverview-get",
            params={},
            keywords=["intune", "device", "overview", "report"],
        ),
        read(
            name="intune_list_detected_apps",
            domain=d,
            description="List applications detected on one Intune managed device.",
            permissions=["DeviceManagementManagedDevices.Read.All"],
            path="/deviceManagement/managedDevices/{device_id}/detectedApps",
            docs=f"{base}/intune-manageddevice-list-detectedapps",
            params={
                "device_id": prop("string", "Intune managed device ID.", minLength=1, maxLength=512)
            },
            required=["device_id"],
            keywords=["intune", "device", "application", "inventory"],
        ),
        read(
            name="intune_list_compliance_policies",
            domain=d,
            description="List Intune device compliance policies.",
            permissions=["DeviceManagementConfiguration.Read.All"],
            path="/deviceManagement/deviceCompliancePolicies",
            docs=f"{base}/intune-devicecompliancepolicy-list",
            params=page_props(filter_=True, select_=False),
            query={"$top": "limit", "$filter": "filter"},
            keywords=["intune", "compliance", "policy"],
        ),
        read(
            name="intune_get_compliance_policy",
            domain=d,
            description="Get one Intune compliance policy by ID.",
            permissions=["DeviceManagementConfiguration.Read.All"],
            path="/deviceManagement/deviceCompliancePolicies/{policy_id}",
            docs=f"{base}/intune-devicecompliancepolicy-get",
            params={
                "policy_id": prop("string", "Compliance policy ID.", minLength=1, maxLength=512)
            },
            required=["policy_id"],
            keywords=["intune", "compliance", "policy"],
        ),
        write(
            name="intune_create_compliance_policy",
            domain=d,
            description="Create a typed Intune compliance policy from a Graph policy payload.",
            permissions=["DeviceManagementConfiguration.ReadWrite.All"],
            path="/deviceManagement/deviceCompliancePolicies",
            method="POST",
            docs=f"{base}/intune-devicecompliancepolicy-create",
            params=payload_props(
                "Typed compliance policy payload. The @odata.type discriminator selects the platform policy type."
            ),
            required=["payload"],
            body={"payload": "payload"},
            risk="high",
            keywords=["intune", "compliance", "create"],
        ),
        write(
            name="intune_update_compliance_policy",
            domain=d,
            description="Update an Intune compliance policy from a Graph policy payload.",
            permissions=["DeviceManagementConfiguration.ReadWrite.All"],
            path="/deviceManagement/deviceCompliancePolicies/{policy_id}",
            method="PATCH",
            docs=f"{base}/intune-devicecompliancepolicy-update",
            params={
                "policy_id": prop("string", "Compliance policy ID.", minLength=1, maxLength=512),
                **payload_props(),
            },
            required=["policy_id", "payload"],
            body={"payload": "payload"},
            risk="high",
            keywords=["intune", "compliance", "update"],
        ),
        write(
            name="intune_delete_compliance_policy",
            domain=d,
            description="Delete an Intune compliance policy.",
            permissions=["DeviceManagementConfiguration.ReadWrite.All"],
            path="/deviceManagement/deviceCompliancePolicies/{policy_id}",
            method="DELETE",
            docs=f"{base}/intune-devicecompliancepolicy-delete",
            params={
                "policy_id": prop("string", "Compliance policy ID.", minLength=1, maxLength=512)
            },
            required=["policy_id"],
            risk="critical",
            keywords=["intune", "compliance", "delete"],
        ),
        read(
            name="intune_list_compliance_assignments",
            domain=d,
            description="List group assignments for an Intune compliance policy.",
            permissions=["DeviceManagementConfiguration.Read.All"],
            path="/deviceManagement/deviceCompliancePolicies/{policy_id}/assignments",
            docs=f"{base}/intune-devicecompliancepolicy-list-assignments",
            params={
                "policy_id": prop("string", "Compliance policy ID.", minLength=1, maxLength=512)
            },
            required=["policy_id"],
            keywords=["intune", "compliance", "assignment"],
        ),
        write(
            name="intune_assign_compliance_policy",
            domain=d,
            description="Replace group assignments for an Intune compliance policy.",
            permissions=["DeviceManagementConfiguration.ReadWrite.All"],
            path="/deviceManagement/deviceCompliancePolicies/{policy_id}/assign",
            method="POST",
            docs=f"{base}/intune-devicecompliancepolicy-assign",
            params={
                "policy_id": prop("string", "Compliance policy ID.", minLength=1, maxLength=512),
                **payload_props("Assignments payload with a targets collection."),
            },
            required=["policy_id", "payload"],
            body={"payload": "payload"},
            risk="high",
            keywords=["intune", "compliance", "assign"],
        ),
        read(
            name="intune_list_device_configurations",
            domain=d,
            description="List classic Intune device configuration profiles.",
            permissions=["DeviceManagementConfiguration.Read.All"],
            path="/deviceManagement/deviceConfigurations",
            docs=f"{base}/intune-deviceconfiguration-list",
            params=page_props(filter_=True, select_=False),
            query={"$top": "limit", "$filter": "filter"},
            keywords=["intune", "configuration", "profile"],
        ),
        read(
            name="intune_get_device_configuration",
            domain=d,
            description="Get one classic Intune device configuration profile.",
            permissions=["DeviceManagementConfiguration.Read.All"],
            path="/deviceManagement/deviceConfigurations/{configuration_id}",
            docs=f"{base}/intune-deviceconfiguration-get",
            params={
                "configuration_id": prop(
                    "string", "Configuration profile ID.", minLength=1, maxLength=512
                )
            },
            required=["configuration_id"],
            keywords=["intune", "configuration", "profile"],
        ),
        write(
            name="intune_create_device_configuration",
            domain=d,
            description="Create a typed classic Intune device configuration profile.",
            permissions=["DeviceManagementConfiguration.ReadWrite.All"],
            path="/deviceManagement/deviceConfigurations",
            method="POST",
            docs=f"{base}/intune-deviceconfiguration-create",
            params=payload_props(
                "Typed device configuration payload with an @odata.type discriminator."
            ),
            required=["payload"],
            body={"payload": "payload"},
            risk="high",
            keywords=["intune", "configuration", "create"],
        ),
        write(
            name="intune_update_device_configuration",
            domain=d,
            description="Update a classic Intune device configuration profile.",
            permissions=["DeviceManagementConfiguration.ReadWrite.All"],
            path="/deviceManagement/deviceConfigurations/{configuration_id}",
            method="PATCH",
            docs=f"{base}/intune-deviceconfiguration-update",
            params={
                "configuration_id": prop(
                    "string", "Configuration profile ID.", minLength=1, maxLength=512
                ),
                **payload_props(),
            },
            required=["configuration_id", "payload"],
            body={"payload": "payload"},
            risk="high",
            keywords=["intune", "configuration", "update"],
        ),
        write(
            name="intune_delete_device_configuration",
            domain=d,
            description="Delete a classic Intune device configuration profile.",
            permissions=["DeviceManagementConfiguration.ReadWrite.All"],
            path="/deviceManagement/deviceConfigurations/{configuration_id}",
            method="DELETE",
            docs=f"{base}/intune-deviceconfiguration-delete",
            params={
                "configuration_id": prop(
                    "string", "Configuration profile ID.", minLength=1, maxLength=512
                )
            },
            required=["configuration_id"],
            risk="critical",
            keywords=["intune", "configuration", "delete"],
        ),
        read(
            name="intune_list_configuration_assignments",
            domain=d,
            description="List group assignments for a classic device configuration profile.",
            permissions=["DeviceManagementConfiguration.Read.All"],
            path="/deviceManagement/deviceConfigurations/{configuration_id}/assignments",
            docs=f"{base}/intune-deviceconfiguration-list-assignments",
            params={
                "configuration_id": prop(
                    "string", "Configuration profile ID.", minLength=1, maxLength=512
                )
            },
            required=["configuration_id"],
            keywords=["intune", "configuration", "assignment"],
        ),
        write(
            name="intune_assign_device_configuration",
            domain=d,
            description="Replace group assignments for a classic device configuration profile.",
            permissions=["DeviceManagementConfiguration.ReadWrite.All"],
            path="/deviceManagement/deviceConfigurations/{configuration_id}/assign",
            method="POST",
            docs=f"{base}/intune-deviceconfiguration-assign",
            params={
                "configuration_id": prop(
                    "string", "Configuration profile ID.", minLength=1, maxLength=512
                ),
                **payload_props("Assignments payload with a targets collection."),
            },
            required=["configuration_id", "payload"],
            body={"payload": "payload"},
            risk="high",
            keywords=["intune", "configuration", "assign"],
        ),
        read(
            name="intune_list_settings_catalog_policies",
            domain=d,
            description="List Intune Settings Catalog configuration policies.",
            permissions=["DeviceManagementConfiguration.Read.All"],
            path="/deviceManagement/configurationPolicies",
            docs=f"{base}/intune-configurationpolicy-list",
            params=page_props(filter_=True, select_=False),
            query={"$top": "limit", "$filter": "filter"},
            keywords=["intune", "settings catalog", "configuration"],
        ),
        read(
            name="intune_get_settings_catalog_policy",
            domain=d,
            description="Get one Intune Settings Catalog policy and its settings.",
            permissions=["DeviceManagementConfiguration.Read.All"],
            path="/deviceManagement/configurationPolicies/{policy_id}",
            docs=f"{base}/intune-configurationpolicy-get",
            params={
                "policy_id": prop(
                    "string", "Settings Catalog policy ID.", minLength=1, maxLength=512
                )
            },
            required=["policy_id"],
            keywords=["intune", "settings catalog"],
        ),
        write(
            name="intune_create_settings_catalog_policy",
            domain=d,
            description="Create an Intune Settings Catalog policy from a Graph payload.",
            permissions=["DeviceManagementConfiguration.ReadWrite.All"],
            path="/deviceManagement/configurationPolicies",
            method="POST",
            docs=f"{base}/intune-configurationpolicy-create",
            params=payload_props(
                "Settings Catalog policy with platforms, technologies and settings."
            ),
            required=["payload"],
            body={"payload": "payload"},
            risk="high",
            keywords=["intune", "settings catalog", "create"],
        ),
        write(
            name="intune_update_settings_catalog_policy",
            domain=d,
            description="Update an Intune Settings Catalog policy.",
            permissions=["DeviceManagementConfiguration.ReadWrite.All"],
            path="/deviceManagement/configurationPolicies/{policy_id}",
            method="PATCH",
            docs=f"{base}/intune-configurationpolicy-update",
            params={
                "policy_id": prop(
                    "string", "Settings Catalog policy ID.", minLength=1, maxLength=512
                ),
                **payload_props(),
            },
            required=["policy_id", "payload"],
            body={"payload": "payload"},
            risk="high",
            keywords=["intune", "settings catalog", "update"],
        ),
        write(
            name="intune_delete_settings_catalog_policy",
            domain=d,
            description="Delete an Intune Settings Catalog policy.",
            permissions=["DeviceManagementConfiguration.ReadWrite.All"],
            path="/deviceManagement/configurationPolicies/{policy_id}",
            method="DELETE",
            docs=f"{base}/intune-configurationpolicy-delete",
            params={
                "policy_id": prop(
                    "string", "Settings Catalog policy ID.", minLength=1, maxLength=512
                )
            },
            required=["policy_id"],
            risk="critical",
            keywords=["intune", "settings catalog", "delete"],
        ),
        read(
            name="intune_list_apps",
            domain=d,
            description="List Intune applications.",
            permissions=["DeviceManagementApps.Read.All"],
            path="/deviceAppManagement/mobileApps",
            docs=f"{base}/intune-mobileapp-list",
            params=page_props(filter_=True, select_=False),
            query={"$top": "limit", "$filter": "filter"},
            keywords=["intune", "application", "app"],
        ),
        read(
            name="intune_get_app",
            domain=d,
            description="Get one Intune application by ID.",
            permissions=["DeviceManagementApps.Read.All"],
            path="/deviceAppManagement/mobileApps/{app_id}",
            docs=f"{base}/intune-mobileapp-get",
            params={"app_id": prop("string", "Intune application ID.", minLength=1, maxLength=512)},
            required=["app_id"],
            keywords=["intune", "application"],
        ),
        write(
            name="intune_create_app",
            domain=d,
            description="Create a typed Intune application from a Graph payload.",
            permissions=["DeviceManagementApps.ReadWrite.All"],
            path="/deviceAppManagement/mobileApps",
            method="POST",
            docs=f"{base}/intune-mobileapp-create",
            params=payload_props(
                "Typed Intune application payload with an @odata.type discriminator."
            ),
            required=["payload"],
            body={"payload": "payload"},
            risk="high",
            keywords=["intune", "application", "create"],
        ),
        write(
            name="intune_update_app",
            domain=d,
            description="Update an Intune application.",
            permissions=["DeviceManagementApps.ReadWrite.All"],
            path="/deviceAppManagement/mobileApps/{app_id}",
            method="PATCH",
            docs=f"{base}/intune-mobileapp-update",
            params={
                "app_id": prop("string", "Intune application ID.", minLength=1, maxLength=512),
                **payload_props(),
            },
            required=["app_id", "payload"],
            body={"payload": "payload"},
            risk="high",
            keywords=["intune", "application", "update"],
        ),
        write(
            name="intune_delete_app",
            domain=d,
            description="Delete an Intune application record.",
            permissions=["DeviceManagementApps.ReadWrite.All"],
            path="/deviceAppManagement/mobileApps/{app_id}",
            method="DELETE",
            docs=f"{base}/intune-mobileapp-delete",
            params={"app_id": prop("string", "Intune application ID.", minLength=1, maxLength=512)},
            required=["app_id"],
            risk="critical",
            keywords=["intune", "application", "delete"],
        ),
        write(
            name="intune_assign_app",
            domain=d,
            description="Replace assignment targets for an Intune application.",
            permissions=["DeviceManagementApps.ReadWrite.All"],
            path="/deviceAppManagement/mobileApps/{app_id}/assign",
            method="POST",
            docs=f"{base}/intune-mobileapp-assign",
            params={
                "app_id": prop("string", "Intune application ID.", minLength=1, maxLength=512),
                **payload_props("Mobile app assignment payload containing targets."),
            },
            required=["app_id", "payload"],
            body={"payload": "payload"},
            risk="high",
            keywords=["intune", "application", "assign", "deploy"],
        ),
        read(
            name="intune_list_app_device_statuses",
            domain=d,
            description="List per-device installation states for an Intune application.",
            permissions=["DeviceManagementApps.Read.All"],
            path="/deviceAppManagement/mobileApps/{app_id}/deviceStatuses",
            docs=f"{base}/intune-mobileapp-list-devicestatuses",
            params={"app_id": prop("string", "Intune application ID.", minLength=1, maxLength=512)},
            required=["app_id"],
            keywords=["intune", "application", "install", "status"],
        ),
        read(
            name="intune_list_enrollment_configurations",
            domain=d,
            description="List Intune device enrollment configurations.",
            permissions=["DeviceManagementServiceConfig.Read.All"],
            path="/deviceManagement/deviceEnrollmentConfigurations",
            docs=f"{base}/intune-deviceenrollmentconfiguration-list",
            params=page_props(select_=False),
            query={"$top": "limit", "$filter": "filter"},
            keywords=["intune", "enrollment", "autopilot"],
        ),
        read(
            name="intune_get_enrollment_configuration",
            domain=d,
            description="Get one Intune enrollment configuration.",
            permissions=["DeviceManagementServiceConfig.Read.All"],
            path="/deviceManagement/deviceEnrollmentConfigurations/{configuration_id}",
            docs=f"{base}/intune-deviceenrollmentconfiguration-get",
            params={
                "configuration_id": prop(
                    "string", "Enrollment configuration ID.", minLength=1, maxLength=512
                )
            },
            required=["configuration_id"],
            keywords=["intune", "enrollment"],
        ),
        write(
            name="intune_create_enrollment_configuration",
            domain=d,
            description="Create a typed Intune enrollment configuration from a Graph payload.",
            permissions=["DeviceManagementServiceConfig.ReadWrite.All"],
            path="/deviceManagement/deviceEnrollmentConfigurations",
            method="POST",
            docs=f"{base}/intune-deviceenrollmentconfiguration-create",
            params=payload_props(
                "Typed enrollment configuration with an @odata.type discriminator."
            ),
            required=["payload"],
            body={"payload": "payload"},
            risk="high",
            keywords=["intune", "enrollment", "create"],
        ),
        read(
            name="intune_list_autopilot_devices",
            domain=d,
            description="List Windows Autopilot device identities.",
            permissions=["DeviceManagementServiceConfig.Read.All"],
            path="/deviceManagement/windowsAutopilotDeviceIdentities",
            docs=f"{base}/intune-windowsautopilotdeviceidentity-list",
            params=page_props(filter_=False, select_=False),
            query={"$top": "limit"},
            keywords=["intune", "autopilot", "device", "hash"],
        ),
        read(
            name="intune_get_autopilot_device",
            domain=d,
            description="Get one Windows Autopilot device identity.",
            permissions=["DeviceManagementServiceConfig.Read.All"],
            path="/deviceManagement/windowsAutopilotDeviceIdentities/{autopilot_id}",
            docs=f"{base}/intune-windowsautopilotdeviceidentity-get",
            params={
                "autopilot_id": prop(
                    "string", "Autopilot device identity ID.", minLength=1, maxLength=512
                )
            },
            required=["autopilot_id"],
            keywords=["intune", "autopilot", "device"],
        ),
        write(
            name="intune_import_autopilot_device",
            domain=d,
            description="Import a Windows Autopilot device identity from a Graph payload.",
            permissions=["DeviceManagementServiceConfig.ReadWrite.All"],
            path="/deviceManagement/importedWindowsAutopilotDeviceIdentities",
            method="POST",
            docs=f"{base}/intune-importedwindowsautopilotdeviceidentity-create",
            params=payload_props(
                "Imported Autopilot identity payload including serial number and hardware identifier."
            ),
            required=["payload"],
            body={"payload": "payload"},
            risk="high",
            keywords=["intune", "autopilot", "import", "hash"],
        ),
        write(
            name="intune_delete_autopilot_device",
            domain=d,
            description="Delete an Autopilot device identity from Intune.",
            permissions=["DeviceManagementServiceConfig.ReadWrite.All"],
            path="/deviceManagement/windowsAutopilotDeviceIdentities/{autopilot_id}",
            method="DELETE",
            docs=f"{base}/intune-windowsautopilotdeviceidentity-delete",
            params={
                "autopilot_id": prop(
                    "string", "Autopilot device identity ID.", minLength=1, maxLength=512
                )
            },
            required=["autopilot_id"],
            risk="critical",
            keywords=["intune", "autopilot", "delete"],
        ),
        read(
            name="intune_list_autopilot_profiles",
            domain=d,
            description="List Windows Autopilot deployment profiles.",
            permissions=["DeviceManagementServiceConfig.Read.All"],
            path="/deviceManagement/windowsAutopilotDeploymentProfiles",
            docs=f"{base}/intune-windowsautopilotdeploymentprofile-list",
            params=page_props(filter_=False, select_=False),
            query={"$top": "limit"},
            keywords=["intune", "autopilot", "profile", "deployment"],
        ),
        read(
            name="intune_get_autopilot_profile",
            domain=d,
            description="Get one Windows Autopilot deployment profile.",
            permissions=["DeviceManagementServiceConfig.Read.All"],
            path="/deviceManagement/windowsAutopilotDeploymentProfiles/{profile_id}",
            docs=f"{base}/intune-windowsautopilotdeploymentprofile-get",
            params={
                "profile_id": prop(
                    "string", "Autopilot deployment profile ID.", minLength=1, maxLength=512
                )
            },
            required=["profile_id"],
            keywords=["intune", "autopilot", "profile"],
        ),
        write(
            name="intune_create_autopilot_profile",
            domain=d,
            description="Create a Windows Autopilot deployment profile from a Graph payload.",
            permissions=["DeviceManagementServiceConfig.ReadWrite.All"],
            path="/deviceManagement/windowsAutopilotDeploymentProfiles",
            method="POST",
            docs=f"{base}/intune-windowsautopilotdeploymentprofile-create",
            params=payload_props("Typed Autopilot deployment profile payload."),
            required=["payload"],
            body={"payload": "payload"},
            risk="high",
            keywords=["intune", "autopilot", "profile", "create"],
        ),
        write(
            name="intune_update_autopilot_profile",
            domain=d,
            description="Update a Windows Autopilot deployment profile.",
            permissions=["DeviceManagementServiceConfig.ReadWrite.All"],
            path="/deviceManagement/windowsAutopilotDeploymentProfiles/{profile_id}",
            method="PATCH",
            docs=f"{base}/intune-windowsautopilotdeploymentprofile-update",
            params={
                "profile_id": prop(
                    "string", "Autopilot deployment profile ID.", minLength=1, maxLength=512
                ),
                **payload_props(),
            },
            required=["profile_id", "payload"],
            body={"payload": "payload"},
            risk="high",
            keywords=["intune", "autopilot", "profile", "update"],
        ),
        write(
            name="intune_assign_autopilot_profile",
            domain=d,
            description="Assign a Windows Autopilot deployment profile to groups.",
            permissions=["DeviceManagementServiceConfig.ReadWrite.All"],
            path="/deviceManagement/windowsAutopilotDeploymentProfiles/{profile_id}/assign",
            method="POST",
            docs=f"{base}/intune-windowsautopilotdeploymentprofile-assign",
            params={
                "profile_id": prop(
                    "string", "Autopilot deployment profile ID.", minLength=1, maxLength=512
                ),
                **payload_props("Assignment payload with targets."),
            },
            required=["profile_id", "payload"],
            body={"payload": "payload"},
            risk="high",
            keywords=["intune", "autopilot", "assign"],
        ),
        write(
            name="intune_delete_autopilot_profile",
            domain=d,
            description="Delete a Windows Autopilot deployment profile.",
            permissions=["DeviceManagementServiceConfig.ReadWrite.All"],
            path="/deviceManagement/windowsAutopilotDeploymentProfiles/{profile_id}",
            method="DELETE",
            docs=f"{base}/intune-windowsautopilotdeploymentprofile-delete",
            params={
                "profile_id": prop(
                    "string", "Autopilot deployment profile ID.", minLength=1, maxLength=512
                )
            },
            required=["profile_id"],
            risk="critical",
            keywords=["intune", "autopilot", "profile", "delete"],
        ),
        read(
            name="intune_list_quality_update_policies",
            domain=d,
            description="List Intune Windows quality update policies.",
            permissions=["DeviceManagementConfiguration.Read.All"],
            path="/deviceManagement/windowsQualityUpdatePolicies",
            docs=f"{base}/intune-windowsqualityupdatepolicy-list",
            params=page_props(filter_=False, select_=False),
            query={"$top": "limit"},
            keywords=["intune", "windows update", "quality"],
        ),
        read(
            name="intune_get_quality_update_policy",
            domain=d,
            description="Get one Intune Windows quality update policy.",
            permissions=["DeviceManagementConfiguration.Read.All"],
            path="/deviceManagement/windowsQualityUpdatePolicies/{policy_id}",
            docs=f"{base}/intune-windowsqualityupdatepolicy-get",
            params={
                "policy_id": prop("string", "Quality update policy ID.", minLength=1, maxLength=512)
            },
            required=["policy_id"],
            keywords=["intune", "windows update", "quality"],
        ),
        write(
            name="intune_create_quality_update_policy",
            domain=d,
            description="Create an Intune Windows quality update policy.",
            permissions=["DeviceManagementConfiguration.ReadWrite.All"],
            path="/deviceManagement/windowsQualityUpdatePolicies",
            method="POST",
            docs=f"{base}/intune-windowsqualityupdatepolicy-create",
            params=payload_props("Quality update policy payload."),
            required=["payload"],
            body={"payload": "payload"},
            risk="high",
            keywords=["intune", "windows update", "create"],
        ),
        write(
            name="intune_update_quality_update_policy",
            domain=d,
            description="Update an Intune Windows quality update policy.",
            permissions=["DeviceManagementConfiguration.ReadWrite.All"],
            path="/deviceManagement/windowsQualityUpdatePolicies/{policy_id}",
            method="PATCH",
            docs=f"{base}/intune-windowsqualityupdatepolicy-update",
            params={
                "policy_id": prop(
                    "string", "Quality update policy ID.", minLength=1, maxLength=512
                ),
                **payload_props(),
            },
            required=["policy_id", "payload"],
            body={"payload": "payload"},
            risk="high",
            keywords=["intune", "windows update", "update"],
        ),
        write(
            name="intune_delete_quality_update_policy",
            domain=d,
            description="Delete an Intune Windows quality update policy.",
            permissions=["DeviceManagementConfiguration.ReadWrite.All"],
            path="/deviceManagement/windowsQualityUpdatePolicies/{policy_id}",
            method="DELETE",
            docs=f"{base}/intune-windowsqualityupdatepolicy-delete",
            params={
                "policy_id": prop("string", "Quality update policy ID.", minLength=1, maxLength=512)
            },
            required=["policy_id"],
            risk="critical",
            keywords=["intune", "windows update", "delete"],
        ),
        read(
            name="intune_list_feature_update_profiles",
            domain=d,
            description="List Intune Windows feature update profiles.",
            permissions=["DeviceManagementConfiguration.Read.All"],
            path="/deviceManagement/windowsFeatureUpdateProfiles",
            docs=f"{base}/intune-windowsfeatureupdateprofile-list",
            params=page_props(filter_=False, select_=False),
            query={"$top": "limit"},
            keywords=["intune", "windows update", "feature"],
        ),
        write(
            name="intune_create_feature_update_profile",
            domain=d,
            description="Create an Intune Windows feature update profile.",
            permissions=["DeviceManagementConfiguration.ReadWrite.All"],
            path="/deviceManagement/windowsFeatureUpdateProfiles",
            method="POST",
            docs=f"{base}/intune-windowsfeatureupdateprofile-create",
            params=payload_props("Feature update profile payload."),
            required=["payload"],
            body={"payload": "payload"},
            risk="high",
            keywords=["intune", "windows update", "feature", "create"],
        ),
        write(
            name="intune_delete_feature_update_profile",
            domain=d,
            description="Delete an Intune Windows feature update profile.",
            permissions=["DeviceManagementConfiguration.ReadWrite.All"],
            path="/deviceManagement/windowsFeatureUpdateProfiles/{profile_id}",
            method="DELETE",
            docs=f"{base}/intune-windowsfeatureupdateprofile-delete",
            params={
                "profile_id": prop(
                    "string", "Feature update profile ID.", minLength=1, maxLength=512
                )
            },
            required=["profile_id"],
            risk="critical",
            keywords=["intune", "windows update", "delete"],
        ),
        read(
            name="intune_list_device_categories",
            domain=d,
            description="List Intune device categories.",
            permissions=["DeviceManagementConfiguration.Read.All"],
            path="/deviceManagement/deviceCategories",
            docs=f"{base}/intune-devicecategory-list",
            params={},
            keywords=["intune", "device", "category"],
        ),
    ]

    actions.extend(
        _device_action("sync", "syncDevice", "synchronise the device with Intune", "medium")
    )
    actions.extend(
        _device_action("reboot", "rebootNow", "reboot the managed Windows device", "high")
    )
    actions.extend(
        _device_action("shutdown", "shutdown", "shut down the managed Windows device", "high")
    )
    actions.extend(
        _device_action("retire", "retire", "retire the device and remove corporate data", "high")
    )
    actions.extend(_device_action("wipe", "wipe", "factory reset the device", "critical"))
    actions.extend(_device_action("remote_lock", "remoteLock", "lock the device remotely", "high"))
    actions.extend(
        _device_action("reset_passcode", "resetPasscode", "reset the device passcode", "high")
    )
    actions.extend(_device_action("locate", "locateDevice", "request the device location", "high"))
    actions.extend(
        _device_action(
            "defender_scan", "windowsDefenderScan", "start a Windows Defender scan", "medium"
        )
    )
    actions.extend(
        _device_action(
            "update_defender_signatures",
            "windowsDefenderUpdateSignatures",
            "update Windows Defender signatures",
            "medium",
        )
    )
    return actions


def _device_action(
    suffix: str, graph_action_name: str, description: str, risk: str
) -> list[dict[str, Any]]:
    return [
        write(
            name=f"intune_{suffix}_device",
            domain="Intune",
            description=f"Use the Intune {graph_action_name} action to {description}.",
            permissions=["DeviceManagementManagedDevices.PrivilegedOperations.All"],
            path=f"/deviceManagement/managedDevices/{{device_id}}/{graph_action_name}",
            method="POST",
            docs=f"https://learn.microsoft.com/en-us/graph/api/intune-devices-manageddevice-{graph_action_name}",
            params={
                "device_id": prop("string", "Intune managed device ID.", minLength=1, maxLength=512)
            },
            required=["device_id"],
            risk=risk,
            keywords=["intune", "device action", suffix.replace("_", " ")],
        )
    ]


def sharepoint_onedrive_actions() -> list[dict[str, Any]]:
    base = "https://learn.microsoft.com/en-us/graph/api"
    sp = "SharePoint Online"
    od = "OneDrive"
    actions: list[dict[str, Any]] = [
        read(
            name="sharepoint_list_sites",
            domain=sp,
            description="Search SharePoint sites by keyword using the Graph site search endpoint.",
            permissions=["Sites.Read.All"],
            path="/sites",
            docs=f"{base}/site-getall",
            params={
                "query": prop(
                    "string",
                    "Site search keyword passed to the Graph search parameter.",
                    minLength=1,
                    maxLength=256,
                ),
                **page_props(filter_=False, select_=True),
            },
            required=["query"],
            query={"search": "query", "$top": "limit", "$select": "select"},
            keywords=["sharepoint", "site", "search"],
        ),
        read(
            name="sharepoint_get_site",
            domain=sp,
            description="Get one SharePoint site by composite site ID.",
            permissions=["Sites.Read.All"],
            path="/sites/{site_id}",
            docs=f"{base}/site-get",
            params={
                "site_id": prop(
                    "string",
                    "Composite SharePoint site ID hostname,siteCollectionId,webId.",
                    minLength=1,
                    maxLength=1024,
                )
            },
            required=["site_id"],
            keywords=["sharepoint", "site", "detail"],
        ),
        read(
            name="sharepoint_list_lists",
            domain=sp,
            description="List document libraries and lists in a SharePoint site.",
            permissions=["Sites.Read.All"],
            path="/sites/{site_id}/lists",
            docs=f"{base}/list-list",
            params={
                "site_id": prop(
                    "string", "Composite SharePoint site ID.", minLength=1, maxLength=1024
                ),
                "limit": prop("integer", "Maximum lists per page.", minimum=1, maximum=999),
                "select": array("string", "List fields to return.", maxItems=50),
            },
            required=["site_id"],
            query={"$top": "limit", "$select": "select"},
            keywords=["sharepoint", "list", "library"],
        ),
        read(
            name="sharepoint_get_list",
            domain=sp,
            description="Get a SharePoint list or document library definition.",
            permissions=["Sites.Read.All"],
            path="/sites/{site_id}/lists/{list_id}",
            docs=f"{base}/list-get",
            params={
                "site_id": prop(
                    "string", "Composite SharePoint site ID.", minLength=1, maxLength=1024
                ),
                "list_id": prop("string", "List ID.", minLength=1, maxLength=512),
            },
            required=["site_id", "list_id"],
            keywords=["sharepoint", "list", "library"],
        ),
        write(
            name="sharepoint_create_list",
            domain=sp,
            description="Create a SharePoint list from a Graph list payload.",
            permissions=["Sites.Manage.All"],
            path="/sites/{site_id}/lists",
            method="POST",
            docs=f"{base}/list-create",
            params={
                "site_id": prop(
                    "string", "Composite SharePoint site ID.", minLength=1, maxLength=1024
                ),
                **payload_props(
                    "List definition including displayName, columns and list template."
                ),
            },
            required=["site_id", "payload"],
            body={"payload": "payload"},
            risk="medium",
            keywords=["sharepoint", "list", "create"],
        ),
        write(
            name="sharepoint_update_list",
            domain=sp,
            description="Update a SharePoint list definition.",
            permissions=["Sites.Manage.All"],
            path="/sites/{site_id}/lists/{list_id}",
            method="PATCH",
            docs=f"{base}/list-update",
            params={
                "site_id": prop(
                    "string", "Composite SharePoint site ID.", minLength=1, maxLength=1024
                ),
                "list_id": prop("string", "List ID.", minLength=1, maxLength=512),
                **payload_props(),
            },
            required=["site_id", "list_id", "payload"],
            body={"payload": "payload"},
            risk="medium",
            keywords=["sharepoint", "list", "update"],
        ),
        write(
            name="sharepoint_delete_list",
            domain=sp,
            description="Delete a SharePoint list or document library.",
            permissions=["Sites.Manage.All"],
            path="/sites/{site_id}/lists/{list_id}",
            method="DELETE",
            docs=f"{base}/list-delete",
            params={
                "site_id": prop(
                    "string", "Composite SharePoint site ID.", minLength=1, maxLength=1024
                ),
                "list_id": prop("string", "List ID.", minLength=1, maxLength=512),
            },
            required=["site_id", "list_id"],
            risk="critical",
            keywords=["sharepoint", "list", "delete"],
        ),
        read(
            name="sharepoint_list_items",
            domain=sp,
            description="List items in a SharePoint list with optional field filtering.",
            permissions=["Sites.Read.All"],
            path="/sites/{site_id}/lists/{list_id}/items",
            docs=f"{base}/listitem-list",
            params={
                "site_id": prop(
                    "string", "Composite SharePoint site ID.", minLength=1, maxLength=1024
                ),
                "list_id": prop("string", "List ID.", minLength=1, maxLength=512),
                "limit": prop("integer", "Maximum items per page.", minimum=1, maximum=999),
                "filter": prop("string", "OData filter on item fields.", maxLength=1024),
                "expand": array(
                    "string", "Navigation properties to expand, commonly fields.", maxItems=10
                ),
            },
            required=["site_id", "list_id"],
            query={"$top": "limit", "$filter": "filter", "$expand": "expand"},
            keywords=["sharepoint", "list", "item"],
        ),
        read(
            name="sharepoint_get_list_item",
            domain=sp,
            description="Get one SharePoint list item and optionally its field values.",
            permissions=["Sites.Read.All"],
            path="/sites/{site_id}/lists/{list_id}/items/{item_id}",
            docs=f"{base}/listitem-get",
            params={
                "site_id": prop(
                    "string", "Composite SharePoint site ID.", minLength=1, maxLength=1024
                ),
                "list_id": prop("string", "List ID.", minLength=1, maxLength=512),
                "item_id": prop("string", "List item ID.", minLength=1, maxLength=128),
                "expand": array("string", "Navigation properties to expand.", maxItems=10),
            },
            required=["site_id", "list_id", "item_id"],
            query={"$expand": "expand"},
            keywords=["sharepoint", "list", "item", "detail"],
        ),
        write(
            name="sharepoint_create_list_item",
            domain=sp,
            description="Create a SharePoint list item from a fields payload.",
            permissions=["Sites.ReadWrite.All"],
            path="/sites/{site_id}/lists/{list_id}/items",
            method="POST",
            docs=f"{base}/listitem-create",
            params={
                "site_id": prop(
                    "string", "Composite SharePoint site ID.", minLength=1, maxLength=1024
                ),
                "list_id": prop("string", "List ID.", minLength=1, maxLength=512),
                **payload_props('Item payload using the form {"fields": {"Title": "value"}}.'),
            },
            required=["site_id", "list_id", "payload"],
            body={"payload": "payload"},
            risk="medium",
            keywords=["sharepoint", "list", "item", "create"],
        ),
        write(
            name="sharepoint_update_list_item",
            domain=sp,
            description="Update fields on an existing SharePoint list item.",
            permissions=["Sites.ReadWrite.All"],
            path="/sites/{site_id}/lists/{list_id}/items/{item_id}",
            method="PATCH",
            docs=f"{base}/listitem-update",
            params={
                "site_id": prop(
                    "string", "Composite SharePoint site ID.", minLength=1, maxLength=1024
                ),
                "list_id": prop("string", "List ID.", minLength=1, maxLength=512),
                "item_id": prop("string", "List item ID.", minLength=1, maxLength=128),
                **payload_props('Item payload using the form {"fields": {"Title": "value"}}.'),
            },
            required=["site_id", "list_id", "item_id", "payload"],
            body={"payload": "payload"},
            risk="medium",
            keywords=["sharepoint", "list", "item", "update"],
        ),
        write(
            name="sharepoint_delete_list_item",
            domain=sp,
            description="Delete one item from a SharePoint list.",
            permissions=["Sites.ReadWrite.All"],
            path="/sites/{site_id}/lists/{list_id}/items/{item_id}",
            method="DELETE",
            docs=f"{base}/listitem-delete",
            params={
                "site_id": prop(
                    "string", "Composite SharePoint site ID.", minLength=1, maxLength=1024
                ),
                "list_id": prop("string", "List ID.", minLength=1, maxLength=512),
                "item_id": prop("string", "List item ID.", minLength=1, maxLength=128),
            },
            required=["site_id", "list_id", "item_id"],
            risk="high",
            keywords=["sharepoint", "list", "item", "delete"],
        ),
        read(
            name="sharepoint_get_site_drive",
            domain=sp,
            description="Get the default document library drive for a SharePoint site.",
            permissions=["Files.Read.All"],
            path="/sites/{site_id}/drive",
            docs=f"{base}/drive-get",
            params={
                "site_id": prop(
                    "string", "Composite SharePoint site ID.", minLength=1, maxLength=1024
                )
            },
            required=["site_id"],
            keywords=["sharepoint", "drive", "library", "storage"],
        ),
        read(
            name="sharepoint_list_drive_items",
            domain=sp,
            description="List files and folders in a SharePoint drive folder.",
            permissions=["Files.Read.All"],
            path="/sites/{site_id}/drive/items/{item_id}/children",
            docs=f"{base}/drive-list-children",
            params={
                "site_id": prop(
                    "string", "Composite SharePoint site ID.", minLength=1, maxLength=1024
                ),
                "item_id": prop("string", "Drive item ID or root.", minLength=1, maxLength=1024),
                "limit": prop("integer", "Maximum children per page.", minimum=1, maximum=999),
                "select": array("string", "Drive item fields to return.", maxItems=50),
            },
            required=["site_id", "item_id"],
            query={"$top": "limit", "$select": "select"},
            keywords=["sharepoint", "file", "folder", "list"],
        ),
        read(
            name="sharepoint_get_drive_item",
            domain=sp,
            description="Get metadata for one SharePoint drive item.",
            permissions=["Files.Read.All"],
            path="/sites/{site_id}/drive/items/{item_id}",
            docs=f"{base}/driveitem-get",
            params={
                "site_id": prop(
                    "string", "Composite SharePoint site ID.", minLength=1, maxLength=1024
                ),
                "item_id": prop("string", "Drive item ID.", minLength=1, maxLength=1024),
            },
            required=["site_id", "item_id"],
            keywords=["sharepoint", "file", "metadata"],
        ),
        read(
            name="sharepoint_list_item_permissions",
            domain=sp,
            description="List sharing permissions on a SharePoint drive item.",
            permissions=["Sites.FullControl.All"],
            path="/sites/{site_id}/drive/items/{item_id}/permissions",
            docs=f"{base}/permission-list",
            params={
                "site_id": prop(
                    "string", "Composite SharePoint site ID.", minLength=1, maxLength=1024
                ),
                "item_id": prop("string", "Drive item ID.", minLength=1, maxLength=1024),
            },
            required=["site_id", "item_id"],
            risk="medium",
            keywords=["sharepoint", "permission", "sharing"],
        ),
        write(
            name="sharepoint_create_sharing_link",
            domain=sp,
            description="Create an anonymous, organization or specific-people sharing link for a drive item.",
            permissions=["Files.ReadWrite.All", "Sites.FullControl.All"],
            path="/sites/{site_id}/drive/items/{item_id}/createLink",
            method="POST",
            docs=f"{base}/driveitem-createlink",
            params={
                "site_id": prop(
                    "string", "Composite SharePoint site ID.", minLength=1, maxLength=1024
                ),
                "item_id": prop("string", "Drive item ID.", minLength=1, maxLength=1024),
                "link_type": prop("string", "Link type.", enum=["view", "edit", "embed"]),
                "scope": prop(
                    "string", "Sharing scope.", enum=["anonymous", "organization", "users"]
                ),
            },
            required=["site_id", "item_id", "link_type", "scope"],
            body={"type": "link_type", "scope": "scope"},
            risk="high",
            keywords=["sharepoint", "sharing", "link", "external"],
        ),
        write(
            name="sharepoint_delete_item_permission",
            domain=sp,
            description="Delete one sharing permission from a SharePoint drive item.",
            permissions=["Files.ReadWrite.All", "Sites.FullControl.All"],
            path="/sites/{site_id}/drive/items/{item_id}/permissions/{permission_id}",
            method="DELETE",
            docs=f"{base}/permission-delete",
            params={
                "site_id": prop(
                    "string", "Composite SharePoint site ID.", minLength=1, maxLength=1024
                ),
                "item_id": prop("string", "Drive item ID.", minLength=1, maxLength=1024),
                "permission_id": prop("string", "Permission ID.", minLength=1, maxLength=512),
            },
            required=["site_id", "item_id", "permission_id"],
            risk="high",
            keywords=["sharepoint", "permission", "revoke"],
        ),
        read(
            name="onedrive_get_user_drive",
            domain=od,
            description="Get the default OneDrive drive for a user.",
            permissions=["Files.Read.All"],
            path="/users/{user_id}/drive",
            docs=f"{base}/drive-get",
            params={
                "user_id": prop("string", "User object ID or UPN.", minLength=1, maxLength=320)
            },
            required=["user_id"],
            keywords=["onedrive", "drive", "storage", "user"],
        ),
        read(
            name="onedrive_list_root_children",
            domain=od,
            description="List files and folders at the root of a user's OneDrive.",
            permissions=["Files.Read.All"],
            path="/users/{user_id}/drive/root/children",
            docs=f"{base}/drive-list-children",
            params={
                "user_id": prop("string", "User object ID or UPN.", minLength=1, maxLength=320),
                "limit": prop("integer", "Maximum children per page.", minimum=1, maximum=999),
                "select": array("string", "Drive item fields to return.", maxItems=50),
            },
            required=["user_id"],
            query={"$top": "limit", "$select": "select"},
            keywords=["onedrive", "file", "folder", "list"],
        ),
        read(
            name="onedrive_get_item",
            domain=od,
            description="Get metadata for one OneDrive item.",
            permissions=["Files.Read.All"],
            path="/users/{user_id}/drive/items/{item_id}",
            docs=f"{base}/driveitem-get",
            params={
                "user_id": prop("string", "User object ID or UPN.", minLength=1, maxLength=320),
                "item_id": prop("string", "Drive item ID.", minLength=1, maxLength=1024),
            },
            required=["user_id", "item_id"],
            keywords=["onedrive", "file", "metadata"],
        ),
        read(
            name="onedrive_list_item_permissions",
            domain=od,
            description="List sharing permissions on a OneDrive item.",
            permissions=["Files.Read.All"],
            path="/users/{user_id}/drive/items/{item_id}/permissions",
            docs=f"{base}/permission-list",
            params={
                "user_id": prop("string", "User object ID or UPN.", minLength=1, maxLength=320),
                "item_id": prop("string", "Drive item ID.", minLength=1, maxLength=1024),
            },
            required=["user_id", "item_id"],
            risk="medium",
            keywords=["onedrive", "permission", "sharing"],
        ),
        write(
            name="onedrive_create_sharing_link",
            domain=od,
            description="Create a sharing link for a OneDrive item.",
            permissions=["Files.ReadWrite.All"],
            path="/users/{user_id}/drive/items/{item_id}/createLink",
            method="POST",
            docs=f"{base}/driveitem-createlink",
            params={
                "user_id": prop("string", "User object ID or UPN.", minLength=1, maxLength=320),
                "item_id": prop("string", "Drive item ID.", minLength=1, maxLength=1024),
                "link_type": prop("string", "Link type.", enum=["view", "edit", "embed"]),
                "scope": prop(
                    "string", "Sharing scope.", enum=["anonymous", "organization", "users"]
                ),
            },
            required=["user_id", "item_id", "link_type", "scope"],
            body={"type": "link_type", "scope": "scope"},
            risk="high",
            keywords=["onedrive", "sharing", "link"],
        ),
        write(
            name="onedrive_delete_item_permission",
            domain=od,
            description="Delete one sharing permission from a OneDrive item.",
            permissions=["Files.ReadWrite.All"],
            path="/users/{user_id}/drive/items/{item_id}/permissions/{permission_id}",
            method="DELETE",
            docs=f"{base}/permission-delete",
            params={
                "user_id": prop("string", "User object ID or UPN.", minLength=1, maxLength=320),
                "item_id": prop("string", "Drive item ID.", minLength=1, maxLength=1024),
                "permission_id": prop("string", "Permission ID.", minLength=1, maxLength=512),
            },
            required=["user_id", "item_id", "permission_id"],
            risk="high",
            keywords=["onedrive", "permission", "revoke"],
        ),
        read(
            name="onedrive_get_quota",
            domain=od,
            description="Get OneDrive storage quota and usage for a user.",
            permissions=["Files.Read.All"],
            path="/users/{user_id}/drive",
            docs=f"{base}/drive-get",
            params={
                "user_id": prop("string", "User object ID or UPN.", minLength=1, maxLength=320),
                "select": array("string", "Fields to return, commonly quota.", maxItems=20),
            },
            required=["user_id"],
            query={"$select": "select"},
            keywords=["onedrive", "quota", "storage", "usage"],
        ),
    ]
    return actions


def teams_actions() -> list[dict[str, Any]]:
    base = "https://learn.microsoft.com/en-us/graph/api"
    d = "Microsoft Teams"
    actions: list[dict[str, Any]] = [
        read(
            name="teams_list_teams",
            domain=d,
            description="List teams in the tenant visible to the application.",
            permissions=["Team.ReadBasic.All"],
            path="/teams",
            docs=f"{base}/team-list",
            params={},
            keywords=["teams", "team", "list"],
        ),
        read(
            name="teams_get_team",
            domain=d,
            description="Get one team by team ID.",
            permissions=["Team.ReadBasic.All"],
            path="/teams/{team_id}",
            docs=f"{base}/team-get",
            params={"team_id": prop("string", "Team ID.", minLength=1, maxLength=512)},
            required=["team_id"],
            keywords=["teams", "team", "detail"],
        ),
        read(
            name="teams_list_channels",
            domain=d,
            description="List channels in a team.",
            permissions=["Channel.ReadBasic.All"],
            path="/teams/{team_id}/channels",
            docs=f"{base}/channel-list",
            params={"team_id": prop("string", "Team ID.", minLength=1, maxLength=512)},
            required=["team_id"],
            keywords=["teams", "channel", "list"],
        ),
        read(
            name="teams_get_channel",
            domain=d,
            description="Get one channel in a team.",
            permissions=["Channel.ReadBasic.All"],
            path="/teams/{team_id}/channels/{channel_id}",
            docs=f"{base}/channel-get",
            params={
                "team_id": prop("string", "Team ID.", minLength=1, maxLength=512),
                "channel_id": prop("string", "Channel ID.", minLength=1, maxLength=512),
            },
            required=["team_id", "channel_id"],
            keywords=["teams", "channel", "detail"],
        ),
        write(
            name="teams_create_channel",
            domain=d,
            description="Create a standard, private or shared channel from a Graph channel payload.",
            permissions=["Channel.Create.Group.All"],
            path="/teams/{team_id}/channels",
            method="POST",
            docs=f"{base}/channel-post",
            params={
                "team_id": prop("string", "Team ID.", minLength=1, maxLength=512),
                **payload_props(
                    "Channel payload with displayName, membershipType and optional members."
                ),
            },
            required=["team_id", "payload"],
            body={"payload": "payload"},
            risk="medium",
            keywords=["teams", "channel", "create"],
        ),
        write(
            name="teams_update_channel",
            domain=d,
            description="Update the display name or description of a channel.",
            permissions=["ChannelSettings.ReadWrite.All"],
            path="/teams/{team_id}/channels/{channel_id}",
            method="PATCH",
            docs=f"{base}/channel-update",
            params={
                "team_id": prop("string", "Team ID.", minLength=1, maxLength=512),
                "channel_id": prop("string", "Channel ID.", minLength=1, maxLength=512),
                **payload_props("Channel update payload."),
            },
            required=["team_id", "channel_id", "payload"],
            body={"payload": "payload"},
            risk="medium",
            keywords=["teams", "channel", "update"],
        ),
        write(
            name="teams_delete_channel",
            domain=d,
            description="Delete a channel and its conversation content.",
            permissions=["Channel.Delete.All"],
            path="/teams/{team_id}/channels/{channel_id}",
            method="DELETE",
            docs=f"{base}/channel-delete",
            params={
                "team_id": prop("string", "Team ID.", minLength=1, maxLength=512),
                "channel_id": prop("string", "Channel ID.", minLength=1, maxLength=512),
            },
            required=["team_id", "channel_id"],
            risk="critical",
            keywords=["teams", "channel", "delete"],
        ),
        read(
            name="teams_list_members",
            domain=d,
            description="List direct members of a team.",
            permissions=["TeamMember.Read.All"],
            path="/teams/{team_id}/members",
            docs=f"{base}/team-list-members",
            params={"team_id": prop("string", "Team ID.", minLength=1, maxLength=512)},
            required=["team_id"],
            keywords=["teams", "member", "membership"],
        ),
        write(
            name="teams_add_member",
            domain=d,
            description="Add a user to a team with member or owner role.",
            permissions=["TeamMember.ReadWrite.All"],
            path="/teams/{team_id}/members",
            method="POST",
            docs=f"{base}/team-post-members",
            params={
                "team_id": prop("string", "Team ID.", minLength=1, maxLength=512),
                "user_id": prop("string", "User object ID.", minLength=1, maxLength=512),
                "role": prop("string", "Conversation role.", enum=["member", "owner"]),
            },
            required=["team_id", "user_id", "role"],
            body={"roles": "role"},
            body_const={
                "@odata.type": "#microsoft.graph.aadUserConversationMember",
                "user@odata.bind": "https://graph.microsoft.com/v1.0/users('{user_id}')",
            },
            risk="high",
            keywords=["teams", "member", "add"],
        ),
        write(
            name="teams_remove_member",
            domain=d,
            description="Remove a member from a team.",
            permissions=["TeamMember.ReadWrite.All"],
            path="/teams/{team_id}/members/{membership_id}",
            method="DELETE",
            docs=f"{base}/team-delete-members",
            params={
                "team_id": prop("string", "Team ID.", minLength=1, maxLength=512),
                "membership_id": prop(
                    "string", "Conversation membership ID.", minLength=1, maxLength=512
                ),
            },
            required=["team_id", "membership_id"],
            risk="high",
            keywords=["teams", "member", "remove"],
        ),
        read(
            name="teams_list_installed_apps",
            domain=d,
            description="List apps installed in a team.",
            permissions=["TeamsAppInstallation.Read.Group"],
            path="/teams/{team_id}/installedApps",
            docs=f"{base}/team-list-installedapps",
            params={"team_id": prop("string", "Team ID.", minLength=1, maxLength=512)},
            required=["team_id"],
            keywords=["teams", "app", "installed"],
        ),
        read(
            name="teams_list_online_meetings",
            domain=d,
            description="List online meetings organized by a user.",
            permissions=["OnlineMeetings.Read.All"],
            path="/users/{user_id}/onlineMeetings",
            docs=f"{base}/onlinemeeting-list",
            params={
                "user_id": prop("string", "User object ID.", minLength=1, maxLength=512),
                "limit": prop("integer", "Maximum meetings per page.", minimum=1, maximum=100),
            },
            required=["user_id"],
            query={"$top": "limit"},
            keywords=["teams", "meeting", "online"],
        ),
        read(
            name="teams_get_online_meeting",
            domain=d,
            description="Get one online meeting by meeting ID.",
            permissions=["OnlineMeetings.Read.All"],
            path="/users/{user_id}/onlineMeetings/{meeting_id}",
            docs=f"{base}/onlinemeeting-get",
            params={
                "user_id": prop("string", "User object ID.", minLength=1, maxLength=512),
                "meeting_id": prop("string", "Online meeting ID.", minLength=1, maxLength=512),
            },
            required=["user_id", "meeting_id"],
            keywords=["teams", "meeting", "online"],
        ),
        write(
            name="teams_create_online_meeting",
            domain=d,
            description="Create an online meeting for a user from a Graph onlineMeeting payload.",
            permissions=["OnlineMeetings.ReadWrite.All"],
            path="/users/{user_id}/onlineMeetings",
            method="POST",
            docs=f"{base}/onlinemeeting-createorget",
            params={
                "user_id": prop("string", "User object ID.", minLength=1, maxLength=512),
                **payload_props(
                    "Online meeting payload with subject, startDateTime and endDateTime."
                ),
            },
            required=["user_id", "payload"],
            body={"payload": "payload"},
            risk="medium",
            keywords=["teams", "meeting", "create"],
        ),
        write(
            name="teams_delete_online_meeting",
            domain=d,
            description="Delete an online meeting for a user.",
            permissions=["OnlineMeetings.ReadWrite.All"],
            path="/users/{user_id}/onlineMeetings/{meeting_id}",
            method="DELETE",
            docs=f"{base}/onlinemeeting-delete",
            params={
                "user_id": prop("string", "User object ID.", minLength=1, maxLength=512),
                "meeting_id": prop("string", "Online meeting ID.", minLength=1, maxLength=512),
            },
            required=["user_id", "meeting_id"],
            risk="high",
            keywords=["teams", "meeting", "delete"],
        ),
    ]
    return actions


def m365_actions() -> list[dict[str, Any]]:
    base = "https://learn.microsoft.com/en-us/graph/api"
    d = "Microsoft 365"
    actions: list[dict[str, Any]] = [
        read(
            name="m365_list_subscribed_skus",
            domain=d,
            description="List tenant license SKUs, consumed units and service plans.",
            permissions=["Organization.Read.All"],
            path="/subscribedSkus",
            docs=f"{base}/subscribedsku-list",
            params={},
            keywords=["license", "sku", "subscription", "service plan"],
        ),
        read(
            name="m365_get_user_license_details",
            domain=d,
            description="List license details and service plans assigned to one user.",
            permissions=["User.Read.All"],
            path="/users/{user_id}/licenseDetails",
            docs=f"{base}/user-list-licensedetails",
            params={"user_id": prop("string", "User object ID.", minLength=1, maxLength=512)},
            required=["user_id"],
            keywords=["license", "user", "service plan"],
        ),
        read(
            name="m365_office_active_user_detail",
            domain=d,
            description="Download the Office 365 active user detail report as CSV.",
            permissions=["Reports.Read.All"],
            path="/reports/getOffice365ActiveUserDetail(period='{period}')",
            docs=f"{base}/reportroot-getoffice365activeuserdetail",
            params={"period": _period_prop()},
            required=["period"],
            response="text",
            keywords=["report", "usage", "active user"],
        ),
        read(
            name="m365_office_active_user_counts",
            domain=d,
            description="Download aggregate Office 365 active user counts as CSV.",
            permissions=["Reports.Read.All"],
            path="/reports/getOffice365ActiveUserCounts(period='{period}')",
            docs=f"{base}/reportroot-getoffice365activeusercounts",
            params={"period": _period_prop()},
            required=["period"],
            response="text",
            keywords=["report", "usage", "active user"],
        ),
        read(
            name="m365_mailbox_usage_detail",
            domain=d,
            description="Download mailbox storage and activity detail as CSV.",
            permissions=["Reports.Read.All"],
            path="/reports/getMailboxUsageDetail(period='{period}')",
            docs=f"{base}/reportroot-getmailboxusagedetail",
            params={"period": _period_prop()},
            required=["period"],
            response="text",
            keywords=["report", "mailbox", "storage", "usage"],
        ),
        read(
            name="m365_mailbox_usage_counts",
            domain=d,
            description="Download aggregate mailbox usage counts as CSV.",
            permissions=["Reports.Read.All"],
            path="/reports/getMailboxUsageMailboxCounts(period='{period}')",
            docs=f"{base}/reportroot-getmailboxusagemailboxcounts",
            params={"period": _period_prop()},
            required=["period"],
            response="text",
            keywords=["report", "mailbox", "usage"],
        ),
        read(
            name="m365_sharepoint_site_usage_detail",
            domain=d,
            description="Download SharePoint site usage detail as CSV.",
            permissions=["Reports.Read.All"],
            path="/reports/getSharePointSiteUsageDetail(period='{period}')",
            docs=f"{base}/reportroot-getsharepointsiteusagedetail",
            params={"period": _period_prop()},
            required=["period"],
            response="text",
            keywords=["report", "sharepoint", "site", "usage"],
        ),
        read(
            name="m365_sharepoint_site_usage_storage",
            domain=d,
            description="Download SharePoint site storage usage as CSV.",
            permissions=["Reports.Read.All"],
            path="/reports/getSharePointSiteUsageStorage(period='{period}')",
            docs=f"{base}/reportroot-getsharepointsiteusagestorage",
            params={"period": _period_prop()},
            required=["period"],
            response="text",
            keywords=["report", "sharepoint", "storage"],
        ),
        read(
            name="m365_onedrive_usage_account_detail",
            domain=d,
            description="Download OneDrive account usage detail as CSV.",
            permissions=["Reports.Read.All"],
            path="/reports/getOneDriveUsageAccountDetail(period='{period}')",
            docs=f"{base}/reportroot-getonedriveusageaccountdetail",
            params={"period": _period_prop()},
            required=["period"],
            response="text",
            keywords=["report", "onedrive", "storage", "usage"],
        ),
        read(
            name="m365_onedrive_activity_user_detail",
            domain=d,
            description="Download OneDrive user activity detail as CSV.",
            permissions=["Reports.Read.All"],
            path="/reports/getOneDriveActivityUserDetail(period='{period}')",
            docs=f"{base}/reportroot-getonedriveactivityuserdetail",
            params={"period": _period_prop()},
            required=["period"],
            response="text",
            keywords=["report", "onedrive", "activity"],
        ),
        read(
            name="m365_teams_user_activity_detail",
            domain=d,
            description="Download Microsoft Teams user activity detail as CSV.",
            permissions=["Reports.Read.All"],
            path="/reports/getTeamsUserActivityUserDetail(period='{period}')",
            docs=f"{base}/reportroot-getteamsuseractivityuserdetail",
            params={"period": _period_prop()},
            required=["period"],
            response="text",
            keywords=["report", "teams", "activity", "user"],
        ),
        read(
            name="m365_teams_device_usage_distribution",
            domain=d,
            description="Download Microsoft Teams device usage distribution as CSV.",
            permissions=["Reports.Read.All"],
            path="/reports/getTeamsDeviceUsageDistributionUserCounts(period='{period}')",
            docs=f"{base}/reportroot-getteamsdeviceusagedistributionusercounts",
            params={"period": _period_prop()},
            required=["period"],
            response="text",
            keywords=["report", "teams", "device"],
        ),
        read(
            name="m365_email_activity_user_detail",
            domain=d,
            description="Download email activity per user as CSV.",
            permissions=["Reports.Read.All"],
            path="/reports/getEmailActivityUserDetail(period='{period}')",
            docs=f"{base}/reportroot-getemailactivityuserdetail",
            params={"period": _period_prop()},
            required=["period"],
            response="text",
            keywords=["report", "email", "activity"],
        ),
        read(
            name="m365_list_service_health",
            domain=d,
            description="List health overviews for subscribed Microsoft 365 services.",
            permissions=["ServiceHealth.Read.All"],
            path="/admin/serviceAnnouncement/healthOverviews",
            docs=f"{base}/serviceannouncement-list-healthoverviews",
            params={},
            risk="medium",
            keywords=["service health", "microsoft 365", "status"],
        ),
        read(
            name="m365_get_service_health",
            domain=d,
            description="Get current health for one Microsoft 365 service.",
            permissions=["ServiceHealth.Read.All"],
            path="/admin/serviceAnnouncement/healthOverviews/{service_name}",
            docs=f"{base}/servicehealth-get",
            params={
                "service_name": prop(
                    "string",
                    "Service name such as exchange or sharePoint.",
                    minLength=1,
                    maxLength=128,
                )
            },
            required=["service_name"],
            risk="medium",
            keywords=["service health", "status"],
        ),
        read(
            name="m365_list_service_issues",
            domain=d,
            description="List active and historical Microsoft 365 service issues.",
            permissions=["ServiceHealth.Read.All"],
            path="/admin/serviceAnnouncement/issues",
            docs=f"{base}/serviceannouncement-list-issues",
            params=page_props(filter_=True, select_=False),
            query={"$top": "limit", "$filter": "filter"},
            risk="medium",
            keywords=["service health", "incident", "issue"],
        ),
        read(
            name="m365_get_service_issue",
            domain=d,
            description="Get one Microsoft 365 service issue.",
            permissions=["ServiceHealth.Read.All"],
            path="/admin/serviceAnnouncement/issues/{issue_id}",
            docs=f"{base}/servicehealthissue-get",
            params={"issue_id": prop("string", "Service issue ID.", minLength=1, maxLength=512)},
            required=["issue_id"],
            risk="medium",
            keywords=["service health", "incident"],
        ),
        read(
            name="m365_list_message_center",
            domain=d,
            description="List Microsoft 365 Message Center posts.",
            permissions=["ServiceMessage.Read.All"],
            path="/admin/serviceAnnouncement/messages",
            docs=f"{base}/serviceannouncement-list-messages",
            params=page_props(filter_=True, select_=False),
            query={"$top": "limit", "$filter": "filter"},
            risk="medium",
            keywords=["message center", "change", "announcement"],
        ),
        read(
            name="m365_get_message_center_post",
            domain=d,
            description="Get one Microsoft 365 Message Center post.",
            permissions=["ServiceMessage.Read.All"],
            path="/admin/serviceAnnouncement/messages/{message_id}",
            docs=f"{base}/serviceupdatemessage-get",
            params={
                "message_id": prop("string", "Message Center post ID.", minLength=1, maxLength=512)
            },
            required=["message_id"],
            risk="medium",
            keywords=["message center", "announcement"],
        ),
    ]
    return actions


def _period_prop() -> dict[str, Any]:
    return prop(
        "string",
        "Reporting period accepted by Microsoft Graph.",
        enum=["D7", "D30", "D90", "D180"],
    )


def security_actions() -> list[dict[str, Any]]:
    base = "https://learn.microsoft.com/en-us/graph/api"
    d = "Security & Compliance"
    actions: list[dict[str, Any]] = [
        read(
            name="security_list_alerts_v2",
            domain=d,
            description="List Microsoft Defender XDR and security alerts using the alerts_v2 API.",
            permissions=["SecurityAlert.Read.All"],
            path="/security/alerts_v2",
            docs=f"{base}/security-alert-list",
            params=page_props(filter_=True, select_=False),
            query={"$top": "limit", "$filter": "filter"},
            risk="medium",
            keywords=["security", "alert", "defender", "incident"],
        ),
        read(
            name="security_get_alert_v2",
            domain=d,
            description="Get one security alert by alert ID.",
            permissions=["SecurityAlert.Read.All"],
            path="/security/alerts_v2/{alert_id}",
            docs=f"{base}/security-alert-get",
            params={"alert_id": prop("string", "Security alert ID.", minLength=1, maxLength=512)},
            required=["alert_id"],
            risk="medium",
            keywords=["security", "alert", "detail"],
        ),
        write(
            name="security_update_alert_v2",
            domain=d,
            description="Update status, classification or determination for a security alert.",
            permissions=["SecurityAlert.ReadWrite.All"],
            path="/security/alerts_v2/{alert_id}",
            method="PATCH",
            docs=f"{base}/security-alert-update",
            params={
                "alert_id": prop("string", "Security alert ID.", minLength=1, maxLength=512),
                **payload_props(
                    "Alert update payload with status, classification and determination."
                ),
            },
            required=["alert_id", "payload"],
            body={"payload": "payload"},
            risk="high",
            keywords=["security", "alert", "triage"],
        ),
        read(
            name="security_list_incidents",
            domain=d,
            description="List security incidents.",
            permissions=["SecurityIncident.Read.All"],
            path="/security/incidents",
            docs=f"{base}/security-incident-list",
            params=page_props(filter_=True, select_=False),
            query={"$top": "limit", "$filter": "filter"},
            risk="medium",
            keywords=["security", "incident", "defender"],
        ),
        read(
            name="security_get_incident",
            domain=d,
            description="Get one security incident and its alerts.",
            permissions=["SecurityIncident.Read.All"],
            path="/security/incidents/{incident_id}",
            docs=f"{base}/security-incident-get",
            params={
                "incident_id": prop("string", "Security incident ID.", minLength=1, maxLength=512)
            },
            required=["incident_id"],
            risk="medium",
            keywords=["security", "incident", "detail"],
        ),
        write(
            name="security_update_incident",
            domain=d,
            description="Update a security incident status or classification.",
            permissions=["SecurityIncident.ReadWrite.All"],
            path="/security/incidents/{incident_id}",
            method="PATCH",
            docs=f"{base}/security-incident-update",
            params={
                "incident_id": prop("string", "Security incident ID.", minLength=1, maxLength=512),
                **payload_props("Incident update payload."),
            },
            required=["incident_id", "payload"],
            body={"payload": "payload"},
            risk="high",
            keywords=["security", "incident", "triage"],
        ),
        read(
            name="security_list_secure_scores",
            domain=d,
            description="List Microsoft Secure Score snapshots for the tenant.",
            permissions=["SecurityEvents.Read.All"],
            path="/security/secureScores",
            docs=f"{base}/security-securescore-list",
            params=page_props(filter_=False, select_=False),
            query={"$top": "limit"},
            risk="medium",
            keywords=["secure score", "security", "posture"],
        ),
        read(
            name="security_list_secure_score_controls",
            domain=d,
            description="List secure score control profiles and current scores.",
            permissions=["SecurityEvents.Read.All"],
            path="/security/secureScoreControlProfiles",
            docs=f"{base}/security-securescorecontrolprofile-list",
            params={},
            risk="medium",
            keywords=["secure score", "control", "security"],
        ),
        read(
            name="security_list_risky_users",
            domain=d,
            description="List users flagged by Entra ID Protection.",
            permissions=["IdentityRiskyUser.Read.All"],
            path="/identityProtection/riskyUsers",
            docs=f"{base}/riskyuser-list",
            params=page_props(filter_=True, select_=False),
            query={"$top": "limit", "$filter": "filter"},
            risk="medium",
            keywords=["identity protection", "risky user", "security"],
        ),
        read(
            name="security_get_risky_user",
            domain=d,
            description="Get one risky user by ID.",
            permissions=["IdentityRiskyUser.Read.All"],
            path="/identityProtection/riskyUsers/{user_id}",
            docs=f"{base}/riskyuser-get",
            params={"user_id": prop("string", "User object ID.", minLength=1, maxLength=512)},
            required=["user_id"],
            risk="medium",
            keywords=["identity protection", "risky user"],
        ),
        write(
            name="security_confirm_user_compromised",
            domain=d,
            description="Confirm one or more risky users as compromised.",
            permissions=["IdentityRiskyUser.ReadWrite.All"],
            path="/identityProtection/riskyUsers/confirmCompromised",
            method="POST",
            docs=f"{base}/riskyuser-confirmcompromised",
            params={
                "user_ids": array(
                    "string", "User object IDs to confirm compromised.", minItems=1, maxItems=50
                )
            },
            required=["user_ids"],
            body={"userIds": "user_ids"},
            risk="critical",
            keywords=["identity protection", "compromised", "risk"],
        ),
        write(
            name="security_dismiss_risky_user",
            domain=d,
            description="Dismiss risk for one or more risky users.",
            permissions=["IdentityRiskyUser.ReadWrite.All"],
            path="/identityProtection/riskyUsers/dismiss",
            method="POST",
            docs=f"{base}/riskyuser-dismiss",
            params={
                "user_ids": array(
                    "string", "User object IDs whose risk is dismissed.", minItems=1, maxItems=50
                )
            },
            required=["user_ids"],
            body={"userIds": "user_ids"},
            risk="high",
            keywords=["identity protection", "dismiss", "risk"],
        ),
        read(
            name="security_list_risk_detections",
            domain=d,
            description="List Entra ID Protection risk detections.",
            permissions=["IdentityRiskEvent.Read.All"],
            path="/identityProtection/riskDetections",
            docs=f"{base}/riskdetection-list",
            params=page_props(filter_=True, select_=False),
            query={"$top": "limit", "$filter": "filter"},
            risk="medium",
            keywords=["identity protection", "risk detection"],
        ),
    ]
    return actions


def main() -> None:
    actions = [
        *entra_actions(),
        *intune_actions(),
        *sharepoint_onedrive_actions(),
        *teams_actions(),
        *m365_actions(),
        *security_actions(),
    ]
    # Fail before writing rather than shipping a malformed generated catalog.
    from m365_mcp.models import ActionDefinition

    invalid: list[str] = []
    for index, item in enumerate(actions):
        try:
            ActionDefinition.model_validate(item)
        except Exception as exc:
            invalid.append(f"{index} {item.get('name')}: {exc}")
    if invalid:
        raise ValueError("Invalid actions:\n" + "\n".join(invalid))
    names = [item["name"] for item in actions]
    if len(names) != len(set(names)):
        duplicates = sorted({name for name in names if names.count(name) > 1})
        raise ValueError(f"Duplicate action names: {duplicates}")
    OUT.write_text(json.dumps(actions, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(actions)} Graph actions to {OUT}")


if __name__ == "__main__":
    main()
