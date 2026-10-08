# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — reusable ACL target builders for handler declarations
"""Build ACL targets without maintaining a second table of wire verbs."""

from __future__ import annotations

from typing import Any

from synapse_channel.core.acl import CLAIM, MAILBOX, MESSAGE, RECALL, RELEASE, Target
from synapse_channel.core.scoping import MAX_DECLARED_PATHS, normalize_paths
from synapse_channel.core.verb_registry import AccessMapper


def field_access(
    permission: str, kind: str, field: str, *, fallback: str = "", strip: bool = False
) -> AccessMapper:
    """Build one access from a frame field with its existing coercion policy."""

    def accesses(data: dict[str, Any]) -> list[tuple[str, Target]]:
        """Resolve the declared frame field to one target."""
        value = str(data.get(field) or fallback)
        return [(permission, Target(kind, value.strip() if strip else value))]

    return accesses


def fixed_access(permission: str, kind: str, value: str) -> AccessMapper:
    """Build an access to a fixed family-specific evidence or history target."""

    def accesses(data: dict[str, Any]) -> list[tuple[str, Target]]:
        """Return the declared target independently of caller-supplied fields."""
        del data
        return [(permission, Target(kind, value))]

    return accesses


def nested_access(permission: str, kind: str, field: str, member: str) -> AccessMapper:
    """Build one access from a mapping member, refusing malformed target shapes."""

    def accesses(data: dict[str, Any]) -> list[tuple[str, Target]]:
        """Resolve a nested target with the existing empty-string fallback."""
        value = data.get(field)
        target = value.get(member) if isinstance(value, dict) else None
        return [(permission, Target(kind, str(target or "")))]

    return accesses


def message_access(data: dict[str, Any]) -> list[tuple[str, Target]]:
    """Authorize the stripped channel, otherwise the direct or all-agent target."""
    channel = str(data.get("channel") or "").strip()
    if channel:
        return [(MESSAGE, Target("channel", channel))]
    return [(MESSAGE, Target("agent", str(data.get("target") or "all")))]


def claim_access(data: dict[str, Any]) -> list[tuple[str, Target]]:
    """Authorize payload-fallback task identity and each normalized claim path."""
    task_id = str(data.get("task_id") or data.get("payload") or "").strip()
    return _claim_targets(data, task_id)


def task_update_access(data: dict[str, Any]) -> list[tuple[str, Target]]:
    """Authorize the task_id/id target the task-update handler actually mutates."""
    task_id = str(data.get("task_id") or data.get("id") or "").strip()
    return _claim_targets(data, task_id)


def release_access(data: dict[str, Any]) -> list[tuple[str, Target]]:
    """Authorize the task_id/payload target the release handler actually releases."""
    task_id = str(data.get("task_id") or data.get("payload") or "").strip()
    return [(RELEASE, Target("claim", task_id))]


def _claim_targets(data: dict[str, Any], task_id: str) -> list[tuple[str, Target]]:
    """Preserve claim and task-update path checks after resolving their task key."""
    accesses = [(CLAIM, Target("claim", task_id))]
    value = data.get("paths")
    paths = [str(item) for item in value if str(item).strip()] if isinstance(value, list) else []
    for path in normalize_paths(paths, MAX_DECLARED_PATHS):
        accesses.append((CLAIM, Target("path", path)))
    return accesses


def history_access(data: dict[str, Any]) -> list[tuple[str, Target]]:
    """Authorize an exact mailbox query or the ordinary global history read."""
    if "inbox_query" in data:
        query = data.get("inbox_query")
        identity = query.get("identity") if isinstance(query, dict) else None
        return [(MAILBOX, Target("agent", identity if isinstance(identity, str) else ""))]
    return [(RECALL, Target("history", "global"))]
