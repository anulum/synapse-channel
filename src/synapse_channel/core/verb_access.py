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

from synapse_channel.core.acl import (
    CLAIM,
    FEED_READ,
    MAILBOX,
    MESSAGE,
    OWNED_WORK,
    RECALL,
    RELEASE,
    Target,
)
from synapse_channel.core.hub_address import HUB_ADDRESS_SEPARATOR
from synapse_channel.core.protocol import GLOB_CHARACTERS, target_parts
from synapse_channel.core.scoping import MAX_DECLARED_PATHS, normalize_paths
from synapse_channel.core.verb_registry import AccessMapper

HUB_AGENT = "hub_agent"
"""The ACL target kind of a seat on a peer hub, ``<seat>@<hub>``.

No ``agent`` rule grants it: a grant for local seats never authorises forwarding
to the same names on a peer hub. An operator grants forwarding with a rule of this
kind, for example ``message hub_agent PROJ/x@PEER``.
"""


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
    """Authorize the stripped channel, otherwise every part of the direct or all-agent target.

    The target is split by :func:`~synapse_channel.core.protocol.target_parts`, the
    splitter delivery uses, and every part needs its own grant, so one granted part
    cannot carry a chat to another. A glob part is marked as a glob, which a rule
    grants only by containment. A part naming a seat on a peer hub is a
    :data:`HUB_AGENT` target.
    """
    channel = str(data.get("channel") or "").strip()
    if channel:
        return [(MESSAGE, Target("channel", channel))]
    return [
        (MESSAGE, _message_target(part)) for part in target_parts(str(data.get("target") or "all"))
    ]


def _message_target(part: str) -> Target:
    """Return the ACL target of one message target part."""
    if HUB_ADDRESS_SEPARATOR in part:
        return Target(HUB_AGENT, part)
    return Target("agent", part, glob=bool(GLOB_CHARACTERS.intersection(part)))


def claim_access(data: dict[str, Any]) -> list[tuple[str, Target]]:
    """Authorize payload-fallback task identity and each normalized claim path."""
    task_id = str(data.get("task_id") or data.get("payload") or "").strip()
    return _claim_targets(data, task_id)


def task_update_access(data: dict[str, Any]) -> list[tuple[str, Target]]:
    """Authorize the task_id/id target the task-update handler actually mutates."""
    task_id = str(data.get("task_id") or data.get("id") or "").strip()
    return _claim_targets(data, task_id)


def handoff_access(data: dict[str, object]) -> list[tuple[str, Target]]:
    """Authorise only the stripped task_id consumed by the handoff handler."""
    task_id = str(data.get("task_id") or "").strip()
    return _claim_targets(data, task_id)


def release_access(data: dict[str, Any]) -> list[tuple[str, Target]]:
    """Authorize the task_id/payload target the release handler actually releases."""
    task_id = str(data.get("task_id") or data.get("payload") or "").strip()
    return [(RELEASE, Target("claim", task_id))]


def _claim_targets(data: dict[str, Any], task_id: str) -> list[tuple[str, Target]]:
    """Preserve declared path checks after resolving the handler's actual task key."""
    accesses = [(CLAIM, Target("claim", task_id))]
    value = data.get("paths")
    paths = [str(item) for item in value] if isinstance(value, list) else []
    for path in normalize_paths(paths, MAX_DECLARED_PATHS):
        accesses.append((CLAIM, Target("path", path)))
    return accesses


def history_access(data: dict[str, Any]) -> list[tuple[str, Target]]:
    """Authorize an exact mailbox query, a feed query or the ordinary global history read.

    The selectors are checked in the order the history handler serves them, so a
    frame is authorised for the query it will be answered with. A feed query needs
    ``feed-read`` on every kind it asks for; a malformed kind list maps to one empty
    target, which no pattern but ``*`` grants, and the handler refuses it.
    """
    if "owned_work_query" in data:
        query = data.get("owned_work_query")
        identity = query.get("identity") if isinstance(query, dict) else None
        return [(OWNED_WORK, Target("agent", identity if isinstance(identity, str) else ""))]
    if any(key in data for key in ("pending_query", "activity_query", "inbox_query")):
        selector = next(
            key for key in ("pending_query", "activity_query", "inbox_query") if key in data
        )
        query = data.get(selector)
        identity = query.get("identity") if isinstance(query, dict) else None
        return [(MAILBOX, Target("agent", identity if isinstance(identity, str) else ""))]
    if "feed_query" in data:
        query = data.get("feed_query")
        kinds = query.get("kinds") if isinstance(query, dict) else None
        names = [kind for kind in kinds if isinstance(kind, str)] if isinstance(kinds, list) else []
        return [(FEED_READ, Target("feed", kind)) for kind in names] or [
            (FEED_READ, Target("feed", ""))
        ]
    return [(RECALL, Target("history", "global"))]


def feed_ack_access(data: dict[str, Any]) -> list[tuple[str, Target]]:
    """Authorize a feed acknowledgement: ``feed-read`` on the ``ack`` feed target."""
    del data  # every acknowledgement needs the same grant
    return [(FEED_READ, Target("feed", "ack"))]


def feed_bind_access(data: dict[str, Any]) -> list[tuple[str, Target]]:
    """Authorize a legacy feed binding: ``feed-read`` on the proof row's feed kind.

    A binding confirms whether a row of that kind exists with a given digest, so it
    needs the same grant as reading that kind. A proof without a text kind maps to
    the empty target, which only ``*`` grants, and the handler refuses it.
    """
    proof = data.get("proof")
    kind = proof.get("kind") if isinstance(proof, dict) else None
    return [(FEED_READ, Target("feed", kind if isinstance(kind, str) else ""))]
