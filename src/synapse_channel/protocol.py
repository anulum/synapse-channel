# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — single source of truth for the on-wire message envelope
"""Wire protocol for Synapse messages.

Every message exchanged between an agent and the hub is a JSON object with a
small, fixed envelope: ``sender``, ``target``, ``type``, ``payload``, and a
``timestamp``. Hub-originated messages additionally carry ``hub_id``. This
module is the one place that constructs those envelopes and names the message
types, so the client, the hub, and any future transport agree on the format.

The builders are pure functions; pass ``now`` to make timestamps deterministic
in tests.
"""

from __future__ import annotations

import time
from typing import Any

SENDER_HUB = "SynapseHub"
"""Reserved sender name stamped on every hub-originated message."""


class MessageType:
    """String constants for every Synapse message ``type``.

    The upper group is sent by agents to the hub; the lower group is emitted by
    the hub back to agents. Values are the literal strings that travel on the
    wire — never rename a value without migrating every peer.
    """

    # Agent -> hub.
    CHAT = "chat"
    HEARTBEAT = "heartbeat"
    CLAIM = "claim"
    RELEASE = "release"
    STATE_REQUEST = "state_request"
    WHO_REQUEST = "who_request"
    HISTORY_REQUEST = "history_request"
    RESUME_REQUEST = "resume_request"
    WAIT_REQUEST = "wait_request"
    TASK_UPDATE = "task_update"
    HANDOFF = "handoff"
    RESOURCE = "resource"
    LEDGER_TASK = "ledger_task"
    LEDGER_TASK_UPDATE = "ledger_task_update"
    LEDGER_PROGRESS = "ledger_progress"
    BOARD_REQUEST = "board_request"

    # Hub -> agent.
    SYSTEM = "system"
    WELCOME = "welcome"
    PRESENCE_UPDATE = "presence_update"
    CLAIM_GRANTED = "claim_granted"
    CLAIM_DENIED = "claim_denied"
    RELEASE_GRANTED = "release_granted"
    RELEASE_DENIED = "release_denied"
    TASK_UPDATED = "task_updated"
    HANDOFF_GRANTED = "handoff_granted"
    HANDOFF_DENIED = "handoff_denied"
    RESOURCE_OFFERED = "resource_offered"
    STATE_SNAPSHOT = "state_snapshot"
    WHO_SNAPSHOT = "who_snapshot"
    HISTORY_SNAPSHOT = "history_snapshot"
    RESUME_SNAPSHOT = "resume_snapshot"
    WAIT_GRANTED = "wait_granted"
    WAIT_DENIED = "wait_denied"
    LEDGER_TASK_POSTED = "ledger_task_posted"
    LEDGER_TASK_UPDATED = "ledger_task_updated"
    LEDGER_PROGRESS_POSTED = "ledger_progress_posted"
    BOARD_SNAPSHOT = "board_snapshot"
    ERROR = "error"
    NAME_CONFLICT = "name_conflict"
    AUTH_DENIED = "auth_denied"


RESOURCE_TYPE_ALIASES = frozenset({"resource", "resource_offer", "offer_resource"})
"""Inbound ``type`` values the hub accepts as a resource offer."""


def _stamp(now: float | None) -> float:
    """Return ``now`` as a float, or the current wall-clock time when ``None``."""
    return time.time() if now is None else float(now)


def build_envelope(
    sender: str,
    msg_type: str,
    *,
    target: str = "all",
    payload: str = "",
    now: float | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Build the agent-side message envelope sent to the hub.

    Parameters
    ----------
    sender : str
        Name of the sending agent.
    msg_type : str
        One of the :class:`MessageType` constants.
    target : str, optional
        Recipient agent name, or ``"all"`` for a broadcast. Defaults to ``"all"``.
    payload : str, optional
        Free-form text body of the message.
    now : float or None, optional
        Override timestamp, in seconds. ``None`` uses the system clock.
    **extra : Any
        Additional protocol fields (e.g. ``task_id``, ``limit``) merged into
        the envelope after the base fields.

    Returns
    -------
    dict[str, Any]
        A JSON-serialisable envelope ready to hand to ``json.dumps``.
    """
    msg: dict[str, Any] = {
        "sender": sender,
        "target": target,
        "type": msg_type,
        "payload": payload,
        "timestamp": _stamp(now),
    }
    msg.update(extra)
    return msg


def system_message(
    payload: str,
    *,
    hub_id: str,
    msg_type: str = MessageType.SYSTEM,
    target: str = "all",
    now: float | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Build a hub-originated system message.

    Parameters
    ----------
    payload : str
        Human-readable body of the system message.
    hub_id : str
        Identifier of the emitting hub, stamped into the envelope.
    msg_type : str, optional
        One of the hub-side :class:`MessageType` constants. Defaults to
        :attr:`MessageType.SYSTEM`.
    target : str, optional
        Recipient agent name, or ``"all"`` for a broadcast. Defaults to ``"all"``.
    now : float or None, optional
        Override timestamp, in seconds. ``None`` uses the system clock.
    **extra : Any
        Additional fields (e.g. ``task_id``, ``online_agents``, ``snapshot``)
        merged into the envelope after the base fields.

    Returns
    -------
    dict[str, Any]
        A JSON-serialisable envelope with ``sender`` set to :data:`SENDER_HUB`.
    """
    msg: dict[str, Any] = {
        "sender": SENDER_HUB,
        "target": target,
        "type": msg_type,
        "payload": payload,
        "timestamp": _stamp(now),
        "hub_id": hub_id,
    }
    msg.update(extra)
    return msg
