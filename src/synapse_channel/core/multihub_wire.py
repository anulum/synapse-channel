# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — wire codec for the cross-host multi-hub event-log pull
"""Canonical wire codec for the cross-host multi-hub event-log pull.

The multi-hub read-side already unions, folds, and follows peer event logs
(:mod:`synapse_channel.core.multihub_merge`, :mod:`~synapse_channel.core.multihub_fold`,
:mod:`~synapse_channel.core.multihub_follower`), but only over a shared filesystem: the
follower's injected :data:`~synapse_channel.core.multihub_follower.EventFetcher` reads a
peer :class:`~synapse_channel.core.persistence.EventStore` directly. To follow a peer over
a real connection, one hub must *ask* another for the events past a cursor and receive a
batch back. This module is the one place that names the shapes of that exchange, so the
serving half (a hub message handler) and the fetching half (a network ``EventFetcher``)
agree on the format without importing each other.

Two shapes ride the exchange, mirroring the cursored ingest seam
(:meth:`~synapse_channel.core.persistence.EventStore.read_since`):

* a :class:`LogRequest` — an exclusive ``after_seq`` cursor and an optional batch ``limit``;
* a :class:`LogSnapshot` — the batch of :class:`~synapse_channel.core.persistence.StoredEvent`
  beyond the cursor plus a ``next_cursor`` high-water the caller resumes from.

The codec is **pure**: it has no network, no clock, and no hub dependency — it only converts
these shapes to and from the JSON-object bodies that ride the wire envelope
(:func:`synapse_channel.core.protocol.build_envelope`). Decoding is defensive because a
snapshot arrives from another host: a malformed body raises :class:`MultiHubWireError` rather
than yielding a half-built batch, so the fetching half can fail the poll and leave the peer's
cursor unadvanced — the fail-closed posture the follower already relies on.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from synapse_channel.core.errors import SynapseError
from synapse_channel.core.persistence import StoredEvent

AFTER_SEQ_FIELD = "after_seq"
"""Request field: the exclusive lower-bound cursor (events with ``seq`` above it are wanted)."""

LIMIT_FIELD = "limit"
"""Request field: the optional maximum batch size, or ``null`` for no cap."""

EVENTS_FIELD = "events"
"""Snapshot field: the ordered batch of encoded events beyond the request cursor."""

NEXT_CURSOR_FIELD = "next_cursor"
"""Snapshot field: the high-water ``seq`` the caller resumes the next request from."""

LOG_END_SEQ_FIELD = "log_end_seq"
"""Snapshot field: the peer log's current high-water, for lag diagnostics."""

SEQ_FIELD = "seq"
"""Event field: the monotonic sequence number."""

TS_FIELD = "ts"
"""Event field: the wall-clock append time, in seconds."""

KIND_FIELD = "kind"
"""Event field: the event-kind tag."""

PAYLOAD_FIELD = "payload"
"""Event field: the decoded JSON object body."""


class MultiHubWireError(SynapseError, ValueError):
    """Raised when a multi-hub wire body is malformed or out of range.

    Carries the fail-closed contract: a fetching follower that catches this leaves the
    peer's cursor unadvanced, so a corrupt or hostile snapshot can never advance the view.
    """

    code = "multihub_wire"


@dataclass(frozen=True, slots=True)
class LogRequest:
    """A request for a peer's events beyond a cursor.

    Parameters
    ----------
    after_seq : int
        Exclusive lower bound; only events with ``seq`` greater than this are wanted.
        Pass ``0`` for the whole log.
    limit : int or None, optional
        Maximum number of events to return, or ``None`` for no cap. A capped batch is
        walked forward by issuing the next request from the snapshot's ``next_cursor``.
    """

    after_seq: int
    limit: int | None = None


@dataclass(frozen=True, slots=True)
class LogSnapshot:
    """A peer's batch of events beyond a request cursor, plus the resume high-water.

    Parameters
    ----------
    events : tuple of StoredEvent
        The events beyond the request cursor, ordered by ascending ``seq``.
    next_cursor : int
        The ``seq`` the caller resumes from: the last event's ``seq`` when the batch is
        non-empty, otherwise the request cursor itself (an empty batch does not move it).
    log_end_seq : int or None, optional
        Peer log maximum sequence when the serving hub can report it. Older peers omit it,
        so ``None`` means exact lag is unavailable.
    """

    events: tuple[StoredEvent, ...]
    next_cursor: int
    log_end_seq: int | None = None


def encode_stored_event(event: StoredEvent) -> dict[str, Any]:
    """Return the JSON-object body for one stored event.

    Parameters
    ----------
    event : StoredEvent
        The event to encode. Its payload is already a JSON object.

    Returns
    -------
    dict[str, Any]
        A JSON-serialisable mapping with ``seq``, ``ts``, ``kind``, and ``payload``.

    Raises
    ------
    MultiHubWireError
        If ``seq`` is negative.
    """
    return {
        SEQ_FIELD: _non_negative(event.seq, SEQ_FIELD),
        TS_FIELD: float(event.ts),
        KIND_FIELD: str(event.kind),
        PAYLOAD_FIELD: dict(event.payload),
    }


def decode_stored_event(raw: object) -> StoredEvent:
    """Reconstruct one stored event from a decoded JSON object.

    Parameters
    ----------
    raw : object
        The decoded event body; expected to be a mapping.

    Returns
    -------
    StoredEvent
        The reconstructed event.

    Raises
    ------
    MultiHubWireError
        If the body is not a mapping, a required field is missing or of the wrong type,
        or ``seq`` is negative.
    """
    body = _require_mapping(raw, "event")
    return StoredEvent(
        seq=_require_int(body.get(SEQ_FIELD), SEQ_FIELD),
        ts=_require_float(body.get(TS_FIELD), TS_FIELD),
        kind=_require_str(body.get(KIND_FIELD), KIND_FIELD),
        payload=dict(_require_mapping(body.get(PAYLOAD_FIELD), PAYLOAD_FIELD)),
    )


def encode_log_request(request: LogRequest) -> dict[str, Any]:
    """Return the JSON-object body for a log request.

    Parameters
    ----------
    request : LogRequest
        The cursor and optional batch cap to request.

    Returns
    -------
    dict[str, Any]
        A mapping with ``after_seq`` and ``limit`` (``limit`` is ``null`` when uncapped).

    Raises
    ------
    MultiHubWireError
        If ``after_seq`` is negative, or ``limit`` is present and negative.
    """
    limit = request.limit
    if limit is not None:
        limit = _non_negative(limit, LIMIT_FIELD)
    return {
        AFTER_SEQ_FIELD: _non_negative(request.after_seq, AFTER_SEQ_FIELD),
        LIMIT_FIELD: limit,
    }


def decode_log_request(raw: object) -> LogRequest:
    """Reconstruct a log request from a decoded JSON object.

    Parameters
    ----------
    raw : object
        The decoded request body; expected to be a mapping.

    Returns
    -------
    LogRequest
        The reconstructed request.

    Raises
    ------
    MultiHubWireError
        If the body is not a mapping, ``after_seq`` is missing/non-integer/negative, or a
        present ``limit`` is non-integer or negative.
    """
    body = _require_mapping(raw, "request")
    raw_limit = body.get(LIMIT_FIELD)
    limit = None if raw_limit is None else _require_int(raw_limit, LIMIT_FIELD)
    return LogRequest(
        after_seq=_require_int(body.get(AFTER_SEQ_FIELD), AFTER_SEQ_FIELD),
        limit=limit,
    )


def encode_log_snapshot(snapshot: LogSnapshot) -> dict[str, Any]:
    """Return the JSON-object body for a log snapshot.

    Parameters
    ----------
    snapshot : LogSnapshot
        The batch of events and the resume high-water.

    Returns
    -------
    dict[str, Any]
        A mapping with ``events`` (a list of encoded events), ``next_cursor``, and
        ``log_end_seq`` when known.

    Raises
    ------
    MultiHubWireError
        If ``next_cursor`` is negative, or any event's ``seq`` is negative.
    """
    return {
        EVENTS_FIELD: [encode_stored_event(event) for event in snapshot.events],
        NEXT_CURSOR_FIELD: _non_negative(snapshot.next_cursor, NEXT_CURSOR_FIELD),
        LOG_END_SEQ_FIELD: (
            None
            if snapshot.log_end_seq is None
            else _non_negative(snapshot.log_end_seq, LOG_END_SEQ_FIELD)
        ),
    }


def decode_log_snapshot(raw: object) -> LogSnapshot:
    """Reconstruct a log snapshot from a decoded JSON object.

    Parameters
    ----------
    raw : object
        The decoded snapshot body; expected to be a mapping.

    Returns
    -------
    LogSnapshot
        The reconstructed snapshot, with events ordered as received.

    Raises
    ------
    MultiHubWireError
        If the body is not a mapping, ``events`` is missing or not a list, any event is
        malformed, or ``next_cursor``/``log_end_seq`` is malformed.
    """
    body = _require_mapping(raw, "snapshot")
    raw_events = body.get(EVENTS_FIELD)
    if not isinstance(raw_events, Sequence) or isinstance(raw_events, str | bytes):
        msg = f"snapshot {EVENTS_FIELD!r} must be a list of events"
        raise MultiHubWireError(msg)
    events = tuple(decode_stored_event(item) for item in raw_events)
    raw_log_end = body.get(LOG_END_SEQ_FIELD)
    return LogSnapshot(
        events=events,
        next_cursor=_require_int(body.get(NEXT_CURSOR_FIELD), NEXT_CURSOR_FIELD),
        log_end_seq=None if raw_log_end is None else _require_int(raw_log_end, LOG_END_SEQ_FIELD),
    )


def _require_mapping(value: object, name: str) -> Mapping[str, Any]:
    """Return ``value`` as a mapping or raise :class:`MultiHubWireError`."""
    if not isinstance(value, Mapping):
        msg = f"{name} body must be a JSON object"
        raise MultiHubWireError(msg)
    return value


def _require_int(value: object, name: str) -> int:
    """Return ``value`` as a non-negative integer or raise :class:`MultiHubWireError`.

    A JSON boolean is rejected even though :class:`bool` is an :class:`int` subclass, so a
    ``true``/``false`` can never stand in for a sequence number.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        msg = f"{name} must be an integer"
        raise MultiHubWireError(msg)
    return _non_negative(value, name)


def _require_float(value: object, name: str) -> float:
    """Return ``value`` as a finite float or raise :class:`MultiHubWireError`.

    Accepts an integer (a whole-second timestamp) but rejects a boolean. A
    non-finite ``nan``/``inf`` is rejected too — ``json.loads`` accepts the
    ``NaN``/``Infinity`` tokens, and a ``nan`` timestamp would compare unequal to
    everything, breaking the deterministic ``(ts, hub_id, seq)`` merge order so
    two hubs folding the same events could diverge. The float conversion runs
    before the finiteness check because a JSON integer too large for a double
    raises ``OverflowError`` on conversion (and on ``math.isfinite`` of the raw int).
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        msg = f"{name} must be a number"
        raise MultiHubWireError(msg)
    try:
        number = float(value)
    except OverflowError as exc:  # a JSON integer too large for a double
        msg = f"{name} must be a finite number"
        raise MultiHubWireError(msg) from exc
    if not math.isfinite(number):
        msg = f"{name} must be a finite number"
        raise MultiHubWireError(msg)
    return number


def _require_str(value: object, name: str) -> str:
    """Return ``value`` as a string or raise :class:`MultiHubWireError`."""
    if not isinstance(value, str):
        msg = f"{name} must be a string"
        raise MultiHubWireError(msg)
    return value


def _non_negative(value: int, name: str) -> int:
    """Return ``value`` unchanged when non-negative, else raise :class:`MultiHubWireError`."""
    if value < 0:
        msg = f"{name} must not be negative"
        raise MultiHubWireError(msg)
    return value
