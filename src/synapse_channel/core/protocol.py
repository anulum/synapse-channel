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

import fnmatch
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
    CHECKPOINT = "checkpoint"
    RESOURCE = "resource"
    LEDGER_TASK = "ledger_task"
    LEDGER_TASK_UPDATE = "ledger_task_update"
    LEDGER_PROGRESS = "ledger_progress"
    BOARD_REQUEST = "board_request"
    ADVERTISE = "advertise"
    MANIFEST_REQUEST = "manifest_request"

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
    CHECKPOINT_SAVED = "checkpoint_saved"
    CHECKPOINT_DENIED = "checkpoint_denied"
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
    CAPABILITY_ADVERTISED = "capability_advertised"
    MANIFEST_SNAPSHOT = "manifest_snapshot"
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


def is_recipient(target: str, name: str) -> bool:
    """Return whether ``name`` is an addressee of a message sent to ``target``.

    The hub broadcasts every chat to every connected client and carries the
    intended recipient in ``target``; a reader uses this predicate to keep only
    the messages meant for it.

    Parameters
    ----------
    target : str
        The recipient field: the broadcast keyword ``"all"`` (or empty), a single
        name, a comma-separated list, or a glob such as ``"quantum/*"`` (every
        agent in the ``quantum`` project) or ``"quantum/claude-*"``.
    name : str
        The reader's own agent name, e.g. ``"quantum/claude-7f3a"``.

    Returns
    -------
    bool
        ``True`` for a broadcast or when ``name`` matches one of the target parts
        (each part is matched as a case-sensitive glob, so a plain name is exact).
        A bare project target also reaches that project's ``<project>/...`` agents,
        so a message to ``"quantum"`` addresses ``"quantum/claude-7f3a"`` — keeping
        this consistent with :func:`addresses_project`, so a sole agent armed under
        a ``<project>/<id>`` identity still receives project-addressed messages.
    """
    cleaned = (target or "all").strip()
    if cleaned in ("", "all"):
        return True
    # A ``<project>/<id>`` name is also addressed by its bare project, so a target
    # of the project reaches every agent in it (mirrors ``addresses_project``).
    project = name.split("/", 1)[0]
    return any(
        fnmatch.fnmatchcase(name, part) or fnmatch.fnmatchcase(project, part)
        for part in (raw.strip() for raw in cleaned.split(",") if raw.strip())
    )


def is_directed(target: str, name: str) -> bool:
    """Return whether ``target`` names ``name`` specifically rather than broadcasting.

    Like :func:`is_recipient` but ``"all"`` (and an empty target) is *not* a match,
    so a reader can wake only on messages addressed to it or a group it is in and
    treat broadcasts as read-when-convenient.

    Parameters
    ----------
    target : str
        The recipient field.
    name : str
        The reader's own agent name.

    Returns
    -------
    bool
        ``True`` only when ``target`` is a non-broadcast pattern that matches ``name``.
    """
    cleaned = (target or "all").strip()
    if cleaned in ("", "all"):
        return False
    return is_recipient(cleaned, name)


def addresses_project(target: str, project: str) -> bool:
    """Return whether a message to ``target`` reaches any agent in ``project``.

    Matches a broadcast, the project name itself, and any ``<project>/...`` identity
    or group glob — so a returning terminal catches up everything for its repo
    regardless of which instance id it now runs as.

    Parameters
    ----------
    target : str
        The recipient field of a message.
    project : str
        The project (repo) name, e.g. ``"quantum"``.

    Returns
    -------
    bool
        ``True`` for a broadcast, ``target == project``, or any ``project/...`` part.
    """
    cleaned = (target or "all").strip()
    if cleaned in ("", "all"):
        return True
    prefix = f"{project}/"
    return any(
        part.strip() == project or part.strip().startswith(prefix)
        for part in cleaned.split(",")
        if part.strip()
    )


PRIORITY_SENDERS = frozenset({"CEO"})
"""Senders whose message wakes a directed-only waiter even on a broadcast.

The CEO command session directs the fleet; a broadcast from it is never merely
routine peer chatter, so it must reach a quiet waiter promptly.
"""


def wakes(
    target: str,
    name: str,
    *,
    directed_only: bool,
    sender: str = "",
    priority: bool = False,
) -> bool:
    """Return whether a chat to ``target`` should wake a waiter listening for ``name``.

    In the default mode any recipient match wakes (:func:`is_recipient`). In
    directed-only mode only a directed match wakes (:func:`is_directed`) — *except*
    a priority-flagged message and a message from a :data:`PRIORITY_SENDERS` sender
    always wake. So an ``"all"`` broadcast that genuinely matters (a CEO directive, a
    flagged announcement) still reaches a quiet waiter promptly, while routine peer
    broadcasts stay suppressed. Directed-only means "no *routine* broadcast wakes me",
    not "no broadcast ever wakes me".

    Parameters
    ----------
    target : str
        The recipient field of the message.
    name : str
        The waiter's own identity.
    directed_only : bool
        When ``True``, suppress routine broadcasts (wake only on a directed, priority,
        or priority-sender message).
    sender : str, optional
        The message's sender, matched against :data:`PRIORITY_SENDERS`.
    priority : bool, optional
        Whether the message carries an explicit priority flag.

    Returns
    -------
    bool
        Whether the waiter should wake on this message.
    """
    if not directed_only:
        return is_recipient(target, name)
    return is_directed(target, name) or priority or sender in PRIORITY_SENDERS
