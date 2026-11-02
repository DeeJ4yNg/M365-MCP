"""English-first query normalization and Microsoft 365 concept lexicon."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

_WORD_RE = re.compile(r"[a-z0-9]+")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
_SPACE_RE = re.compile(r"\s+")
LEXICON_VERSION = "2026-11-01"

_STOPWORDS = frozenset(
    {
        "a",
        "after",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "been",
        "before",
        "by",
        "can",
        "cannot",
        "cant",
        "could",
        "did",
        "do",
        "does",
        "during",
        "experiencing",
        "facing",
        "for",
        "from",
        "getting",
        "had",
        "has",
        "have",
        "he",
        "help",
        "her",
        "his",
        "if",
        "in",
        "is",
        "issue",
        "it",
        "its",
        "me",
        "my",
        "not",
        "of",
        "on",
        "or",
        "our",
        "please",
        "problem",
        "she",
        "should",
        "since",
        "that",
        "the",
        "their",
        "them",
        "they",
        "this",
        "those",
        "to",
        "trying",
        "unable",
        "was",
        "we",
        "were",
        "when",
        "while",
        "will",
        "with",
        "would",
        "you",
        "your",
        "id",
    }
)

# Phrases are normalized forms, not display strings.
_PHRASE_CANONICALIZATION: tuple[tuple[str, str], ...] = (
    ("onedrive for business", "onedrive"),
    ("multi factor authentication", "mfa"),
    ("out of office", "out of office"),
    ("non delivery report", "non delivery report"),
    ("microsoft 365", "microsoft 365"),
    ("microsoft teams", "microsoft teams"),
    ("exchange online", "exchange online"),
    ("sharepoint online", "sharepoint online"),
    ("conditional access", "conditional access"),
    ("one drive", "onedrive"),
    ("share point", "sharepoint"),
    ("ms teams", "microsoft teams"),
    ("office 365", "microsoft 365"),
    ("azure ad", "entra id"),
    ("sign in", "sign in"),
    ("log in", "sign in"),
    ("login", "sign in"),
    ("signin", "sign in"),
    ("m365", "microsoft 365"),
    ("o365", "microsoft 365"),
    ("odfb", "onedrive"),
    ("exo", "exchange online"),
    ("spo", "sharepoint online"),
    ("ooo", "out of office"),
    ("ndr", "non delivery report"),
    ("2fa", "mfa"),
    ("rbac", "role assignment"),
    ("sku", "license sku"),
    ("esp", "autopilot enrollment"),
    ("upn", "user principal name"),
    ("owa", "outlook web"),
    ("aad", "entra id"),
    ("dl", "distribution group"),
)


@dataclass(frozen=True, slots=True)
class Concept:
    key: str
    terms: tuple[str, ...]
    aliases: tuple[str, ...]
    action_hints: tuple[str, ...]
    boost: float = 6.0


CONCEPTS: tuple[Concept, ...] = (
    Concept(
        "identity.user",
        ("user", "account", "identity", "user principal name"),
        ("user", "users", "account", "identity", "employee", "staff", "用户", "账号"),
        ("user", "users"),
        boost=2.0,
    ),
    Concept(
        "identity.user.list",
        ("list users", "find users", "search users", "user list"),
        ("list users", "find users", "search users", "查找用户", "查找", "列出用户", "用户列表"),
        ("list users", "list user"),
        boost=6.0,
    ),
    Concept(
        "identity.group",
        ("group", "security group", "microsoft 365 group"),
        ("group", "security group", "m365 group", "mail enabled group", "组", "群组"),
        ("groups", "group"),
    ),
    Concept(
        "identity.group.membership",
        ("group member", "group membership", "add member", "remove member", "group owner"),
        ("add to group", "remove from group", "group membership", "dl membership", "成员"),
        ("group members", "group member", "members", "owners"),
    ),
    Concept(
        "identity.auth.signin",
        ("sign in", "sign in log", "login", "authentication", "sso"),
        (
            "cannot sign in",
            "cannot login",
            "unable to sign in",
            "login failure",
            "login issue",
            "locked out",
            "signin failure",
            "aadsts",
            "authentication failure",
            "登录失败",
            "无法登录",
        ),
        ("sign in", "auth methods", "auth method", "authentication", "mfa"),
        boost=10.0,
    ),
    Concept(
        "identity.auth.mfa",
        ("mfa", "authentication method", "authenticator", "multi factor authentication"),
        (
            "mfa",
            "mfa reset",
            "mfa loop",
            "mfa prompt",
            "mfa method",
            "2fa",
            "authenticator",
            "authentication method",
            "多因素",
            "认证方式",
        ),
        ("auth methods", "authentication methods", "mfa", "authentication"),
        boost=9.0,
    ),
    Concept(
        "identity.conditional_access",
        ("conditional access", "conditional access policy"),
        ("conditional access", "ca policy", "条件访问"),
        ("conditional access",),
    ),
    Concept(
        "identity.role",
        ("role", "role assignment", "directory role", "admin role", "rbac"),
        (
            "admin role",
            "role assignment",
            "role member",
            "directory role",
            "privileged role",
            "rbac",
            "角色",
        ),
        ("role assignments", "role definitions", "directory roles", "role assignment"),
    ),
    Concept(
        "identity.application",
        ("application", "app registration", "service principal", "enterprise app"),
        (
            "app registration",
            "application registration",
            "service principal",
            "enterprise application",
            "应用",
        ),
        ("applications", "application", "service principals", "service principal"),
    ),
    Concept(
        "identity.license",
        ("license", "license sku", "subscription", "seat", "service plan"),
        (
            "license",
            "licence",
            "license assignment",
            "assigned license",
            "subscription",
            "seat",
            "sku",
            "许可",
            "许可证",
        ),
        ("license", "subscribed skus"),
    ),
    Concept(
        "identity.offboarding",
        ("offboard", "disable user", "delete user", "remove license", "remove group"),
        (
            "offboard",
            "offboarding",
            "leaver",
            "terminate employee",
            "disable account",
            "remove access",
            "离职",
        ),
        ("disable", "delete user", "remove group member", "assign licenses"),
    ),
    Concept(
        "device.managed",
        ("device", "managed device", "intune device", "endpoint", "laptop", "workstation"),
        ("device", "laptop", "endpoint", "workstation", "managed device", "intune", "设备", "终端"),
        ("managed devices", "managed device", "device"),
    ),
    Concept(
        "device.wipe",
        ("wipe", "erase", "factory reset", "remote wipe"),
        (
            "wipe device",
            "erase device",
            "factory reset",
            "lost laptop",
            "stolen laptop",
            "remote wipe",
            "擦除",
        ),
        ("wipe", "retire"),
        boost=10.0,
    ),
    Concept(
        "device.remote_action",
        ("sync", "reboot", "restart", "remote lock", "reset passcode", "locate"),
        (
            "sync device",
            "reboot device",
            "restart device",
            "remote lock",
            "reset passcode",
            "device location",
            "locate device",
        ),
        ("sync device", "reboot", "shutdown", "remote lock", "reset passcode", "locate"),
    ),
    Concept(
        "device.autopilot",
        ("autopilot", "windows autopilot", "enrollment", "esp", "out of box experience"),
        (
            "autopilot",
            "autopilot failure",
            "autopilot enrollment",
            "esp stuck",
            "device enrollment",
            "enrollment failure",
            "0x8018",
            "自动部署",
        ),
        ("autopilot", "enrollment"),
        boost=8.0,
    ),
    Concept(
        "device.compliance",
        ("compliance", "compliance policy", "not compliant", "compliance state"),
        (
            "not compliant",
            "compliance failure",
            "device compliance",
            "compliance policy",
            "合规",
        ),
        ("compliance",),
    ),
    Concept(
        "device.configuration",
        ("configuration", "configuration profile", "configuration policy", "settings catalog"),
        (
            "configuration profile",
            "settings catalog",
            "device configuration",
            "config profile",
            "配置",
        ),
        ("configuration", "configurations", "settings catalog"),
    ),
    Concept(
        "device.app",
        ("intune app", "application deployment", "app install", "app assignment", "managed app"),
        (
            "app install failure",
            "application install",
            "app deployment",
            "required app",
            "win32 app",
            "应用部署",
        ),
        ("mobile apps", "apps", "app"),
    ),
    Concept(
        "exchange.mailbox",
        ("mailbox", "exchange", "exchange online", "email account"),
        (
            "mailbox",
            "email",
            "email account",
            "exchange online",
            "exo",
            "outlook",
            "owa",
            "邮箱",
            "邮件",
        ),
        ("mailbox", "exchange"),
    ),
    Concept(
        "exchange.forwarding",
        ("forwarding", "forward email", "mail forwarding", "redirect"),
        (
            "email forwarding",
            "forwarding address",
            "forward email",
            "auto forward",
            "redirect email",
            "转发",
        ),
        ("forwarding", "forward"),
    ),
    Concept(
        "exchange.autoreply",
        ("automatic replies", "out of office", "ooo", "auto reply"),
        ("out of office", "ooo", "automatic reply", "auto reply", "vacation reply", "外出"),
        ("autoreply", "automaticreplies", "auto reply"),
    ),
    Concept(
        "exchange.permission",
        ("mailbox permission", "full access", "send as", "folder permission", "delegate access"),
        (
            "full access",
            "send as",
            "send on behalf",
            "mailbox permission",
            "folder permission",
            "delegate access",
            "邮箱权限",
        ),
        ("mailbox permission", "permission", "recipient permission", "folder permission"),
    ),
    Concept(
        "exchange.quarantine",
        ("quarantine", "quarantined message", "spam", "junk", "release quarantine"),
        (
            "release quarantine",
            "quarantined email",
            "spam release",
            "blocked email",
            "junk email",
            "quarantine",
            "隔离邮件",
        ),
        ("quarantine",),
        boost=10.0,
    ),
    Concept(
        "exchange.mailflow",
        (
            "mail flow",
            "message trace",
            "transport rule",
            "connector",
            "delivery failure",
            "ndr",
            "non delivery report",
        ),
        (
            "email not delivered",
            "mail not delivered",
            "email not received",
            "not receiving email",
            "delivery delay",
            "undelivered",
            "message trace",
            "mail flow",
            "ndr",
            "bounce",
            "550 5",
            "transport rule",
            "connector",
            "邮件流",
            "退信",
        ),
        (
            "message trace",
            "message trace detail",
            "transport rule",
            "inbound connector",
            "outbound connector",
        ),
        boost=8.0,
    ),
    Concept(
        "exchange.distribution_group",
        ("distribution group", "distribution list", "mail enabled group"),
        ("distribution group", "distribution list", "dl", "mail enabled group", "通讯组"),
        ("distribution group",),
    ),
    Concept(
        "exchange.contact",
        ("mail contact", "contact", "mail user"),
        ("mail contact", "external contact", "mail user", "联系人"),
        ("mail contact", "mail user", "contact"),
    ),
    Concept(
        "exchange.mobile_device",
        ("activesync", "mobile device", "mobile partnership"),
        ("activesync", "mobile device", "mobile partnership", "phone partnership", "移动设备"),
        ("mobile device",),
    ),
    Concept(
        "sharepoint.site",
        ("sharepoint", "site", "site collection", "spo"),
        ("sharepoint site", "site collection", "spo", "sharepoint online", "网站"),
        ("sites", "site", "sharepoint"),
    ),
    Concept(
        "sharepoint.list",
        ("sharepoint list", "document library", "list item"),
        ("document library", "sharepoint list", "list item", "列表"),
        ("lists", "list", "list item"),
    ),
    Concept(
        "sharepoint.file",
        ("file", "drive item", "folder", "document"),
        ("sharepoint file", "drive item", "document library file", "folder", "文件"),
        ("drive", "item", "file"),
    ),
    Concept(
        "sharepoint.permission",
        ("sharing", "permission", "sharing link", "site permission"),
        (
            "sharing link",
            "site permission",
            "file permission",
            "permission denied",
            "access denied",
            "共享",
            "权限",
        ),
        ("permission", "sharing", "create link"),
        boost=7.0,
    ),
    Concept(
        "onedrive",
        ("onedrive", "odfb", "drive", "personal files"),
        ("onedrive", "odfb", "onedrive for business", "personal drive"),
        ("onedrive", "drive"),
    ),
    Concept(
        "teams.team",
        ("team", "microsoft teams", "team membership", "team owner"),
        ("microsoft teams", "ms teams", "team membership", "add to team", "team owner", "团队"),
        ("teams", "team"),
    ),
    Concept(
        "teams.channel",
        ("channel", "private channel", "shared channel"),
        ("team channel", "private channel", "shared channel", "频道"),
        ("channels", "channel"),
    ),
    Concept(
        "teams.meeting",
        ("meeting", "online meeting", "meeting policy", "lobby", "recording"),
        (
            "online meeting",
            "meeting policy",
            "meeting recording",
            "meeting lobby",
            "anonymous join",
            "会议",
        ),
        ("meeting", "online meeting", "online meetings"),
    ),
    Concept(
        "teams.policy",
        ("teams policy", "messaging policy", "calling policy", "app permission policy"),
        (
            "messaging policy",
            "calling policy",
            "app setup policy",
            "app permission policy",
            "meeting policy",
        ),
        (
            "meeting policy",
            "messaging policy",
            "calling policy",
            "app permission policy",
            "app setup policy",
        ),
    ),
    Concept(
        "m365.health",
        ("service health", "outage", "service incident", "service issue", "microsoft 365 health"),
        (
            "service health",
            "outage",
            "service issue",
            "m365 outage",
            "service degradation",
            "服务健康",
        ),
        ("service health", "service issues", "health overviews"),
    ),
    Concept(
        "m365.message_center",
        ("message center", "service announcement", "change announcement"),
        ("message center", "mc notice", "service announcement", "change announcement", "消息中心"),
        ("message center", "service announcement", "messages"),
    ),
    Concept(
        "m365.report",
        ("report", "usage report", "active user", "mailbox usage", "storage report"),
        ("usage report", "active user report", "mailbox usage", "storage report", "report", "报表"),
        ("report", "reports", "usage"),
    ),
    Concept(
        "security.alert",
        ("security alert", "defender alert", "alert"),
        ("security alert", "defender alert", "alert triage", "告警"),
        ("alerts", "alert"),
    ),
    Concept(
        "security.incident",
        ("incident", "security incident", "defender incident"),
        ("security incident", "incident response", "incident", "安全事件"),
        ("incidents", "incident"),
    ),
    Concept(
        "security.risky_user",
        ("risky user", "risk detection", "identity protection"),
        ("risky user", "risk detection", "compromised user", "identity protection", "风险用户"),
        ("risky users", "risky user", "risk detections"),
    ),
    Concept(
        "security.audit",
        ("audit log", "unified audit", "sign in log", "directory audit"),
        ("audit log", "unified audit", "sign in log", "directory audit", "audit trail", "审计"),
        ("audit", "sign in logs"),
    ),
    Concept(
        "compliance.retention",
        (
            "retention",
            "retention policy",
            "retention label",
            "dlp",
            "data loss prevention",
            "ediscovery",
        ),
        (
            "retention policy",
            "retention label",
            "dlp policy",
            "data loss prevention",
            "ediscovery",
            "保留",
        ),
        ("retention", "dlp", "compliance tags"),
    ),
    Concept(
        "intune.update",
        ("windows update", "quality update", "feature update", "update ring", "patch"),
        (
            "windows update",
            "quality update",
            "feature update",
            "update ring",
            "patch failure",
            "更新",
        ),
        ("quality update", "feature update", "update policy"),
    ),
)

_ERROR_PREFIXES = ("aadsts", "0x8018")


@dataclass(frozen=True, slots=True)
class ParsedQuery:
    normalized: str
    tokens: frozenset[str]
    aliases: frozenset[str]
    concepts: tuple[Concept, ...]


@lru_cache(maxsize=8192)
def normalize_text(value: str) -> str:
    """Normalize English and Chinese text into stable search space."""

    text = unicodedata.normalize("NFKC", value)
    text = text.replace("\u2019", "'").replace("\u2018", "'")
    text = re.sub(r"\bcan't\b", "cannot", text, flags=re.IGNORECASE)
    text = re.sub(r"\bcannot\b|\bcan not\b", "cannot", text, flags=re.IGNORECASE)
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    text = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", text)
    text = text.casefold().replace("&", " and ")
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", text)
    text = _SPACE_RE.sub(" ", text).strip()
    for source, target in _PHRASE_CANONICALIZATION:
        text = re.sub(
            rf"(?<![a-z0-9]){re.escape(source)}(?![a-z0-9])",
            target,
            text,
        )
    return _SPACE_RE.sub(" ", text).strip()


def contains_phrase(text: str, phrase: str) -> bool:
    """Match a normalized word or phrase without matching inside another word."""

    if _CJK_RE.search(phrase):
        return phrase in text
    if phrase.startswith(_ERROR_PREFIXES):
        return phrase in text
    return f" {phrase} " in f" {text} "


def canonical_domain(value: str) -> str:
    normalized = normalize_text(value)
    return _DOMAIN_ALIASES.get(normalized, normalized)


_DOMAIN_ALIASES: dict[str, str] = {
    "entra": "entra id",
    "entra id": "entra id",
    "azure ad": "entra id",
    "aad": "entra id",
    "身份": "entra id",
    "intune": "intune",
    "endpoint": "intune",
    "终端": "intune",
    "设备管理": "intune",
    "exchange": "exchange online",
    "exchange online": "exchange online",
    "exo": "exchange online",
    "邮箱": "exchange online",
    "sharepoint": "sharepoint online",
    "sharepoint online": "sharepoint online",
    "spo": "sharepoint online",
    "站点": "sharepoint online",
    "onedrive": "onedrive",
    "teams": "microsoft teams",
    "microsoft teams": "microsoft teams",
    "团队": "microsoft teams",
    "m365": "microsoft 365",
    "microsoft 365": "microsoft 365",
    "office 365": "microsoft 365",
    "o365": "microsoft 365",
    "security": "security and compliance",
    "compliance": "security and compliance",
    "security and compliance": "security and compliance",
    "security & compliance": "security and compliance",
    "purview": "security and compliance",
    "安全": "security and compliance",
    "合规": "security and compliance",
}


def analyze_query(value: str) -> ParsedQuery:
    normalized = normalize_text(value)
    tokens = _tokens(normalized)
    matched: list[Concept] = []
    aliases: set[str] = set()
    for concept in CONCEPTS:
        candidates = {normalize_text(term) for term in concept.terms + concept.aliases}
        if any(contains_phrase(normalized, term) for term in candidates if term):
            matched.append(concept)
            aliases.update(candidates)
    return ParsedQuery(
        normalized=normalized,
        tokens=frozenset(tokens),
        aliases=frozenset(
            alias
            for alias in (normalize_text(item) for item in aliases)
            if alias and alias not in _STOPWORDS
        ),
        concepts=tuple(matched),
    )


def concept_boost(concepts: tuple[Concept, ...], normalized_action_name: str) -> float:
    """Boost actions whose names clearly belong to a matched business concept."""

    matched = 0.0
    for concept in concepts:
        hints = (normalize_text(hint) for hint in concept.action_hints)
        if any(contains_phrase(normalized_action_name, hint) for hint in hints if hint):
            matched += concept.boost
    return min(matched, 24.0)


def _tokens(normalized: str) -> set[str]:
    tokens = set(_WORD_RE.findall(normalized))
    for chunk in _CJK_RE.findall(normalized):
        for size in (2, 3, 4):
            tokens.update(chunk[index : index + size] for index in range(len(chunk) - size + 1))
    expanded: set[str] = set()
    for token in tokens:
        if token in _STOPWORDS:
            continue
        expanded.add(token)
        expanded.update(_morphological_variants(token))
    return {token for token in expanded if token not in _STOPWORDS and len(token) >= 2}


def _morphological_variants(token: str) -> set[str]:
    """Conservative English variants; avoid aggressive stemming of product terms."""

    if not token.isalpha() or len(token) < 5:
        return set()
    variants: set[str] = set()
    if token.endswith("ies") and len(token) > 5:
        variants.add(token[:-3] + "y")
    elif token.endswith("s") and not token.endswith(("ss", "us", "is", "as")):
        variants.add(token[:-1])
    if token.endswith("ing") and len(token) > 6:
        base = token[:-3]
        if len(base) > 3 and base[-1] == base[-2]:
            base = base[:-1]
        variants.add(base)
    if token.endswith("ed") and len(token) > 5:
        base = token[:-2]
        if len(base) > 3 and base[-1] == base[-2]:
            base = base[:-1]
        variants.add(base)
    return {item for item in variants if len(item) >= 2}


__all__ = [
    "CONCEPTS",
    "LEXICON_VERSION",
    "Concept",
    "ParsedQuery",
    "analyze_query",
    "canonical_domain",
    "concept_boost",
    "contains_phrase",
    "normalize_text",
]
