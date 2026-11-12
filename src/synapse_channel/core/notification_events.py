# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — exact-recipient assignment notification metadata
"""Keep assignment wake pointers in the same durable event as their native effect."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from synapse_channel.core.streaming import CHUNK, OPEN, STREAM_FRAME_KIND

NOTIFICATION_FIELD = "_notification"
"""Reserved event metadata, excluded from public task records."""

NotificationKind = Literal["tasks", "handoffs"]


def notification_metadata(
    kind: NotificationKind, actor: str, recipient: str, payload: Mapping[str, object]
) -> dict[str, object] | None:
    """Describe an external assignment within the recipient's project namespace.

    Parameters
    ----------
    kind : {"tasks", "handoffs"}
        Native assignment family.
    actor, recipient : str
        Authenticated actor and actual post-mutation recipient.
    payload : collections.abc.Mapping
        Native committed task or claim snapshot. No user-supplied descriptor is
        copied into this pointer.

    Returns
    -------
    dict[str, object] or None
        Bounded routing metadata, or no notification for self, unqualified or
        foreign-project work. The metadata contains no task text or checkpoint.
    """
    project, separator, suffix = recipient.partition("/")
    task = payload.get("task_id")
    if (
        not actor
        or len(actor) > 256
        or actor == recipient
        or len(recipient) > 256
        or recipient.strip() != recipient
        or not separator
        or not project
        or not suffix
        or not isinstance(task, str)
        or not task.startswith(project + "/")
        or payload.get("project", "") not in ("", project)
    ):
        return None
    return {"version": 1, "kind": kind, "actor": actor, "recipient": recipient}


def task_notification(
    payload: Mapping[str, object], actor: str, previous_owner: str
) -> dict[str, object] | None:
    """Notify only a new external suggested owner on a nonterminal plan task."""
    recipient = payload.get("suggested_owner")
    if (
        not isinstance(recipient, str)
        or recipient == previous_owner
        or payload.get("status") in ("done", "cancelled")
    ):
        return None
    return notification_metadata("tasks", actor, recipient, payload)


def _chat_activity(payload: Mapping[str, object], identity: str) -> str | None:
    """Wake only exact chat, waiting for a stream's terminal frame."""
    if (
        payload.get("target") != identity
        or payload.get("sender") == identity
        or payload.get("channel")
        or (payload.get("kind") == STREAM_FRAME_KIND and payload.get("frame_type") in (OPEN, CHUNK))
    ):
        return None
    return "messages"


def event_activity(kind: str, payload: Mapping[str, object], identity: str) -> str | None:
    """Select exact-directed committed activity without returning its content.

    Parameters
    ----------
    kind : str
        Native journal kind.
    payload : collections.abc.Mapping
        One committed native event.
    identity : str
        Connection-owned recipient, never a group, broadcast or caller alias.

    Returns
    -------
    str or None
        Messages, tasks or handoffs for an eligible event. Older task and
        handoff events without trusted routing metadata do not acquire inferred
        assignment semantics.
    """
    if kind == "chat":
        return _chat_activity(payload, identity)
    family: NotificationKind
    if kind == "ledger_task":
        family = "tasks"
    elif kind == "handoff":
        family = "handoffs"
    else:
        return None
    pointer = payload.get(NOTIFICATION_FIELD)
    if not isinstance(pointer, dict) or type(pointer.get("version")) is not int:
        return None
    actor, recipient = pointer.get("actor"), pointer.get("recipient")
    if not isinstance(actor, str) or recipient != identity:
        return None
    expected = notification_metadata(family, actor, identity, payload)
    return family if expected is not None and pointer == expected else None
