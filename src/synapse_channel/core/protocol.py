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
import json
import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

SENDER_HUB = "SynapseHub"
"""Reserved sender name stamped on every hub-originated message."""

WIRE_PROTOCOL_VERSION = 2
"""The version of the hub's wire protocol.

Advertised in the ``WELCOME`` handshake so a client — including an out-of-tree
consumer that syncs across possibly version-skewed hubs during a rolling upgrade
— can read the peer's wire version on connect rather than infer it from a
separate query. It is **decoupled from the package version on purpose**: a patch
or feature release that leaves the wire shapes unchanged does not bump it, and it
bumps only on a wire vocabulary change, so it is a stable compatibility signal
rather than a release counter. Version ``2`` adds the client→hub ``ACK`` verb and
the deferred delivery receipt it drives; a client gates emitting an ``ACK`` on the
peer advertising version ``2`` or newer (:data:`MIN_ACK_PROTOCOL_VERSION`), so it
never sends the verb to a hub that predates it — and an older hub, never taught the
verb, is never sent it, which is what keeps the addition backward-compatible.

It is **advertise-only for now**: a client captures the peer's version and gates
its own ``ACK`` emission on it, but the hub enforces no cross-version policy,
because the mismatch behaviour (reject, warn, or negotiate down) is a contract
shared with the downstream consumers the wire serves and is decided with them
before it is enforced.
"""

MIN_ACK_PROTOCOL_VERSION = 2
"""Lowest advertised wire version at which a client may emit an ``ACK``.

The ``ACK`` verb and its deferred delivery receipt arrived at wire version ``2``, so
a client only sends an ``ACK`` when the hub advertises this version or newer — a
fixed capability floor, not the moving :data:`WIRE_PROTOCOL_VERSION`, so a future
hub at a higher version still qualifies while a pre-``2`` hub that never learnt the
verb is never sent it.
"""


@dataclass(frozen=True)
class ProtocolNegotiation:
    """Compatibility result after comparing local and peer wire versions."""

    local_version: int
    """Wire version spoken by this process."""

    peer_version: int | None
    """Wire version advertised by the peer, or ``None`` when absent or malformed."""

    effective_version: int
    """Lowest common version to use for optional capabilities."""

    warning: str | None
    """Operator-visible warning when the peer is version-skewed or did not advertise a version."""


def read_protocol_version(value: object) -> int | None:
    """Return a wire protocol version read from a frame, or ``None`` if absent or malformed.

    A hub that predates the advertised version omits the field, and a boolean is
    not a version even though ``bool`` is an ``int`` subclass, so both degrade to
    ``None`` rather than a spurious version a compatibility check might act on.

    Parameters
    ----------
    value : object
        The raw ``protocol_version`` field from a decoded frame, or ``None``.

    Returns
    -------
    int or None
        The version when ``value`` is a non-boolean integer, else ``None``.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def negotiate_protocol_version(
    peer_version: int | None,
    *,
    local_version: int = WIRE_PROTOCOL_VERSION,
    fallback_version: int = 1,
) -> ProtocolNegotiation:
    """Return the negotiated wire version and any operator-visible warning.

    Parameters
    ----------
    peer_version : int or None
        Peer wire version read from the handshake. ``None`` means the peer did not
        advertise a usable version, as older hubs did.
    local_version : int, optional
        Local wire version. Defaults to :data:`WIRE_PROTOCOL_VERSION`.
    fallback_version : int, optional
        Compatibility version assumed for peers that omit the field. Defaults to
        ``1``, the pre-advertisement wire.

    Returns
    -------
    ProtocolNegotiation
        Lowest-common effective version plus a warning when the peer is skewed.
    """
    warning: str | None
    if peer_version is None:
        warning = (
            "peer did not advertise a wire protocol version; "
            f"using compatibility version {fallback_version}"
        )
        return ProtocolNegotiation(
            local_version=local_version,
            peer_version=None,
            effective_version=min(local_version, fallback_version),
            warning=warning,
        )
    effective = min(local_version, peer_version)
    if peer_version == local_version:
        warning = None
    elif peer_version < local_version:
        warning = (
            f"peer wire protocol version {peer_version} is older than local "
            f"version {local_version}; using compatibility version {effective}"
        )
    else:
        warning = (
            f"peer wire protocol version {peer_version} is newer than local "
            f"version {local_version}; using compatibility version {effective}"
        )
    return ProtocolNegotiation(
        local_version=local_version,
        peer_version=peer_version,
        effective_version=effective,
        warning=warning,
    )


MAX_JSON_DEPTH = 64
"""Deepest array/object nesting accepted in an inbound wire frame.

A frame nested deeper than this is rejected before parsing, so an adversarially
deep payload cannot drive the JSON decoder into a ``RecursionError``. The hub's
``--max-msg-kb`` cap already bounds a frame's *width*; this bounds its *depth*.
Real Synapse envelopes nest only a few levels, so the bound is generous headroom
well under the interpreter recursion limit.
"""


def _exceeds_json_depth(text: str, max_depth: int) -> bool:
    """Return whether ``text`` nests arrays/objects deeper than ``max_depth``.

    A single character scan that tracks ``[``/``{`` nesting while skipping bracket
    characters inside string literals (honouring backslash escapes), so a brace
    written inside a string is never miscounted. Returns at the first level over
    the bound rather than scanning the whole frame.

    Parameters
    ----------
    text : str
        The raw JSON text.
    max_depth : int
        The deepest array/object nesting permitted.

    Returns
    -------
    bool
        ``True`` as soon as the nesting exceeds ``max_depth``.
    """
    depth = 0
    in_string = False
    escaped = False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
            if depth > max_depth:
                return True
        elif char in "]}":
            depth = max(depth - 1, 0)
    return False


def loads_bounded(raw: str | bytes, max_depth: int = MAX_JSON_DEPTH) -> Any:
    """Parse one inbound JSON frame, rejecting one nested past ``max_depth``.

    The depth guard runs before :func:`json.loads`, so an adversarially deep frame
    raises a clean :class:`json.JSONDecodeError` instead of crashing the decoder
    with a ``RecursionError``.

    Parameters
    ----------
    raw : str or bytes
        The raw frame received from a socket.
    max_depth : int, optional
        Deepest array/object nesting accepted. Defaults to :data:`MAX_JSON_DEPTH`.

    Returns
    -------
    Any
        The decoded JSON value.

    Raises
    ------
    json.JSONDecodeError
        When the frame is malformed or nested deeper than ``max_depth``.
    """
    text = raw if isinstance(raw, str) else raw.decode("utf-8", "replace")
    if _exceeds_json_depth(text, max_depth):
        raise json.JSONDecodeError(f"JSON nested deeper than {max_depth} levels", text, 0)

    def _reject_non_finite(constant: str) -> float:
        # RFC 8259 has no NaN/Infinity, but json.loads accepts those tokens by
        # default. A non-finite float slips past a downstream type check and then
        # breaks ordering comparisons (nan compares unequal to everything) or
        # overflows an int()/float() conversion, so reject it at the single decode
        # boundary every inbound frame passes through — a defence in depth beneath
        # the per-field guards. The hub never emits a non-finite float, so no
        # legitimate frame carries one.
        msg = f"non-finite JSON constant {constant!r} is not permitted"
        raise json.JSONDecodeError(msg, text, 0)

    return json.loads(raw, parse_constant=_reject_non_finite)


class MessageType:
    """String constants for every Synapse message ``type``.

    The upper group is sent by agents to the hub; the lower group is emitted by
    the hub back to agents. Values are the literal strings that travel on the
    wire — never rename a value without migrating every peer.
    """

    # Agent -> hub.
    CHAT = "chat"
    ACK = "ack"
    DELIVERY_RECEIPT = "delivery_receipt"
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
    RECALL_LOG = "recall_log"
    FINDING = "finding"
    CHANNEL_CREATE = "channel_create"
    CHANNEL_JOIN = "channel_join"
    CHANNEL_LEAVE = "channel_leave"
    CHANNEL_LIST_REQUEST = "channel_list_request"
    CHANNEL_HISTORY_REQUEST = "channel_history_request"
    MULTIHUB_LOG_REQUEST = "multihub_log_request"
    MULTIHUB_CLAIM_REQUEST = "multihub_claim_request"
    OPERATOR_RELAY_REQUEST = "operator_relay_request"
    FEDERATION_OFFER_REQUEST = "federation_offer_request"

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
    LEASE_GRANTED = "lease_granted"
    LEDGER_TASK_POSTED = "ledger_task_posted"
    LEDGER_TASK_UPDATED = "ledger_task_updated"
    LEDGER_PROGRESS_POSTED = "ledger_progress_posted"
    BOARD_SNAPSHOT = "board_snapshot"
    CAPABILITY_ADVERTISED = "capability_advertised"
    RECALL_LOGGED = "recall_logged"
    FINDING_RECORDED = "finding_recorded"
    FINDING_REJECTED = "finding_rejected"
    MANIFEST_SNAPSHOT = "manifest_snapshot"
    CHANNEL_RESULT = "channel_result"
    CHANNEL_LIST = "channel_list"
    CHANNEL_HISTORY = "channel_history"
    MULTIHUB_LOG_SNAPSHOT = "multihub_log_snapshot"
    MULTIHUB_CLAIM_RESULT = "multihub_claim_result"
    OPERATOR_RELAY_RESULT = "operator_relay_result"
    FEDERATION_OFFER = "federation_offer"
    DEAD_LETTER_ESCALATION = "dead_letter_escalation"
    DEAD_LETTER_FORWARDING = "dead_letter_forwarding"
    RECIPIENT_LIVENESS_WARNING = "recipient_liveness_warning"
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


def is_recipient(target: str, name: str, roles: Iterable[str] = ()) -> bool:
    """Return whether ``name`` is an addressee of a message sent to ``target``.

    The hub broadcasts every chat to every connected client and carries the
    intended recipient in ``target``; a reader uses this predicate to keep only
    the messages meant for it.

    Beyond its own ``name``, an identity may also answer to one or more ``roles``
    — full ``<project>/<role>`` names it has bound (e.g. ``"quantum/coordinator"``)
    — so a message addressed to a role reaches whichever instance currently holds
    it. A role is matched exactly like a name (a case-sensitive glob), so a role
    target is exact and a group glob still covers it.

    Parameters
    ----------
    target : str
        The recipient field: the broadcast keyword ``"all"`` (or empty), a single
        name, a comma-separated list, or a glob such as ``"quantum/*"`` (every
        agent in the ``quantum`` project) or ``"quantum/claude-*"``.
    name : str
        The reader's own agent name, e.g. ``"quantum/claude-7f3a"``.
    roles : Iterable[str], optional
        Additional full ``<project>/<role>`` names this identity answers to. Empty
        by default, which preserves plain name/project matching.

    Returns
    -------
    bool
        ``True`` for a broadcast or when ``name``, its bare project, or one of its
        ``roles`` matches one of the target parts (each part is matched as a
        case-sensitive glob, so a plain name is exact). A bare project target also
        reaches that project's ``<project>/...`` agents, so a message to
        ``"quantum"`` addresses ``"quantum/claude-7f3a"`` — keeping this consistent
        with :func:`addresses_project`, so a sole agent armed under a
        ``<project>/<id>`` identity still receives project-addressed messages.
    """
    cleaned = (target or "all").strip()
    if cleaned in ("", "all"):
        return True
    # A ``<project>/<id>`` name is also addressed by its bare project, so a target
    # of the project reaches every agent in it (mirrors ``addresses_project``).
    project = name.split("/", 1)[0]
    role_names = tuple(roles)
    return any(
        fnmatch.fnmatchcase(name, part)
        or fnmatch.fnmatchcase(project, part)
        or any(fnmatch.fnmatchcase(role, part) for role in role_names)
        for part in (raw.strip() for raw in cleaned.split(",") if raw.strip())
    )


def is_directed(target: str, name: str, roles: Iterable[str] = ()) -> bool:
    """Return whether ``target`` names ``name`` specifically rather than broadcasting.

    Stricter than :func:`is_recipient` in two ways: ``"all"`` (and an empty target) is
    *not* a match, and a target part must match the **full** ``name`` (or one of its
    ``roles``) rather than its bare project. So a bare ``<project>`` directs the waiter
    armed as that project, but is a *routine broadcast* — not a wake — for a
    ``<project>/<seat>`` sub-seat. A reader uses this to wake only on messages addressed
    to it, a role it holds, or a group glob it is in, and to treat both ``all`` broadcasts
    and project-level traffic as read-when-convenient.

    This is the WAKE question, deliberately distinct from the INBOX question
    (:func:`is_recipient`): a sub-seat still *receives* a bare-project message, it just is
    not *woken* by one (which is why a multi-seat project's convene traffic no longer wakes
    every seat). A sole agent that wants project-addressed messages to wake it arms
    ``--for <project>`` (the bare project), not a ``<project>/<id>`` sub-identity. A message
    to a role the waiter holds *is* a directed wake, so addressing a role reaches its
    current holder promptly.

    Parameters
    ----------
    target : str
        The recipient field.
    name : str
        The reader's own agent name.
    roles : Iterable[str], optional
        Additional full ``<project>/<role>`` names this identity answers to. A directed
        target that matches one of them wakes the holder. Empty by default.

    Returns
    -------
    bool
        ``True`` only when a non-broadcast target part matches ``name`` or one of its
        ``roles`` directly (an exact name/role or a glob covering it); a bare project
        matches a waiter named exactly that project, not its sub-seats.
    """
    cleaned = (target or "all").strip()
    if cleaned in ("", "all"):
        return False
    role_names = tuple(roles)
    return any(
        fnmatch.fnmatchcase(name, part)
        or any(fnmatch.fnmatchcase(role, part) for role in role_names)
        for part in (raw.strip() for raw in cleaned.split(",") if raw.strip())
    )


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
    roles: Iterable[str] = (),
) -> bool:
    """Return whether a chat to ``target`` should wake a waiter listening for ``name``.

    In the default mode any recipient match wakes (:func:`is_recipient`). In
    directed-only mode only a directed match wakes (:func:`is_directed`) — *except*
    that a priority-flagged message, or one from a :data:`PRIORITY_SENDERS` sender,
    also wakes when it still reaches this waiter (a broadcast, or a message addressed
    to it). So an ``"all"`` broadcast that genuinely matters (a CEO directive, a
    flagged announcement) reaches a quiet waiter promptly, while routine peer
    broadcasts stay suppressed — and a priority or CEO message directed to a *different*
    agent does not wake one it was never addressed to. Directed-only means "no
    *routine* broadcast wakes me", not "every priority message anywhere wakes me".

    A message addressed to a ``role`` this waiter holds is a directed wake, so a
    directed-only waiter still wakes promptly when its role — not just its instance
    name — is addressed.

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
    roles : Iterable[str], optional
        Full ``<project>/<role>`` names this waiter answers to; a directed message to
        one of them wakes it. Empty by default.

    Returns
    -------
    bool
        Whether the waiter should wake on this message.
    """
    role_names = tuple(roles)
    if not directed_only:
        return is_recipient(target, name, role_names)
    # A directed match always wakes. A priority flag or a priority sender elevates a
    # message that still *reaches* this waiter — a broadcast, or one addressed to it —
    # so a flagged announcement or a CEO directive reaches a quiet waiter promptly. But
    # a priority or CEO message directed to a *different* agent must not wake this one:
    # gating on ``is_recipient`` keeps a targeted nudge from becoming a fleet-wide wake
    # storm across every directed-only waiter that the message was never addressed to.
    return is_directed(target, name, role_names) or (
        is_recipient(target, name, role_names) and (priority or sender in PRIORITY_SENDERS)
    )
