# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — what the hub knows about each seat
"""Say what is known about a seat, and nothing more.

A seat is ``listening`` when a socket that receives its directed messages is
attached now: the identity itself or its waiter sidecar. That says messages
arrive, not that the seat will answer. Whether it answers is the hub's own
judgement, carried as each row's ``reaction``: a connected seat with no live
waiter that has not reacted within the hub's window is *deaf*, the word
``synapse who`` uses and the hub's reason to warn a sender. A seat that is not
listening is ``active, not listening`` when the durable record shows it at
work: a claim whose lease has not expired, or a claim stamped or a progress note
authored within :data:`SEAT_ACTIVE_WINDOW_SECONDS`. Otherwise it is ``silent``:
in practice the owner of a listed claim whose lease has run out and whose stamp
is older than the window. A note older than the window names no seat. No seat is
called offline: a socket that is gone says nothing about the work.

``last_seen`` is not evidence: the hub stamps it on keepalive heartbeats, so it
proves a socket, not work.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from synapse_channel.core.agent_liveness import deaf_mark
from synapse_channel.core.waiter_identity import (
    is_waiter,
    pane_waiter_name,
    presence_holders,
    waiter_name,
    waiter_owner,
)

SEAT_ACTIVE_WINDOW_SECONDS = 900.0
"""How recent a claim stamp or an authored note must be to make a seat active."""

LISTENING = "listening"
ACTIVE_NOT_LISTENING = "active, not listening"
SILENT = "silent"
SEAT_STATES = (LISTENING, ACTIVE_NOT_LISTENING, SILENT)
"""Every seat state, in the order a roster ranks them."""


def _records(value: object) -> list[Mapping[str, Any]]:
    """Return the mapping items of a list; anything else is empty."""
    return [item for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []


def _time(value: object) -> float | None:
    """Return a finite number as a float, anything else as ``None``."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _evidence(
    state: Mapping[str, Any], board: Mapping[str, Any], now: float
) -> dict[str, tuple[str, float | None]]:
    """Return each identity's newest durable evidence of work: (kind, time or ``None``).

    An empty identity may be returned; the caller names no seat for it.
    """
    found: dict[str, tuple[str, float | None]] = {}

    def keep(identity: str, kind: str, at: float | None) -> None:
        older = found.get(identity)
        if older is None or (at is not None and (older[1] is None or at > older[1])):
            found[identity] = (kind, at)

    for claim in _records(state.get("active_claims")):
        owner, stamped = str(claim.get("owner") or ""), _time(claim.get("claimed_at"))
        expires = _time(claim.get("lease_expires_at"))
        if expires is not None and expires > now:
            # A held claim makes its seat active at any age; its stamp, if any, is the time.
            keep(owner, "claim", stamped)
        elif stamped is not None and now - stamped <= SEAT_ACTIVE_WINDOW_SECONDS:
            keep(owner, "claim", stamped)
    for note in _records(board.get("progress")):
        posted = _time(note.get("posted_at"))
        if posted is not None and now - posted <= SEAT_ACTIVE_WINDOW_SECONDS:
            keep(str(note.get("author") or ""), "note", posted)
    return found


def _flag(value: object) -> bool | None:
    """Return a boolean as itself, anything else as ``None``."""
    return value if isinstance(value, bool) else None


def _reaction(info: object) -> dict[str, Any]:
    """Return the hub's judgement on one seat, or ``None`` throughout when it made none.

    The hub judges only the agents connected under their own name, and only while
    it tracks reactions; a seat outside that has no judgement, and none is invented.
    """
    record: Mapping[str, Any] = info if isinstance(info, Mapping) else {}
    proven = _flag(record.get("proven_live"))
    age = _time(record.get("last_reaction_age"))
    return {
        "proven": proven,
        "live_waiter": _flag(record.get("has_live_waiter")),
        "last_reaction_age": age,
        "mark": deaf_mark({"last_reaction_age": age}) if proven is False else "",
    }


def build_seats(
    online_agents: Sequence[str],
    state: Mapping[str, Any],
    board: Mapping[str, Any],
    *,
    now: float,
    liveness: Mapping[str, Any] | None = None,
    wake_capabilities: Mapping[str, object] | None = None,
) -> list[dict[str, Any]]:
    """Return one row per seat the snapshot names, sorted by identity.

    Parameters
    ----------
    online_agents : Sequence[str]
        Every connected name, waiter sidecars included.
    state : Mapping[str, Any]
        The hub's state snapshot (its ``active_claims``).
    board : Mapping[str, Any]
        The whole board (its ``progress`` notes), before any bound.
    now : float
        The snapshot's own time, in epoch seconds.
    liveness : Mapping[str, Any] or None, optional
        The ``who`` snapshot's ``agent_liveness``; empty or ``None`` when the hub
        tracks no reactions.
    wake_capabilities : Mapping[str, object] or None, optional
        The ``who`` snapshot's ``wake_capabilities``. A connected presence holder
        (:func:`~synapse_channel.core.waiter_identity.presence_holders`) adds no
        seat; ``None`` or empty when the hub reports none.

    Returns
    -------
    list[dict[str, Any]]
        ``identity``, ``listening``, ``active``, ``evidence`` (``claim``,
        ``note`` or ``None``), ``evidence_at``, ``state`` and ``reaction``
        (``proven``, ``live_waiter``, ``last_reaction_age`` and the ``mark``
        ``synapse who`` prints, such as ``deaf ~4m``).
    """
    online = {str(name) for name in online_agents}
    judged = liveness or {}
    evidence = _evidence(state, board, now)
    # A seat is connected (a waiter stands for its owner), owns a listed claim, or has
    # evidence (a note inside the window). An older note names no seat: the board keeps
    # every author it ever had. A connected presence holder is not a seat by that
    # connection: it declared that it never acts on what it receives.
    owners = {str(claim.get("owner") or "") for claim in _records(state.get("active_claims"))}
    holders = presence_holders(online, wake_capabilities)
    seats = {waiter_owner(name) if is_waiter(name) else name for name in online - holders}
    seats |= owners | set(evidence)
    rows: list[dict[str, Any]] = []
    for identity in sorted(seat for seat in seats if seat):
        listening = bool({identity, waiter_name(identity), pane_waiter_name(identity)} & online)
        kind, at = evidence.get(identity, (None, None))
        active = kind is not None
        rows.append(
            {
                "identity": identity,
                "listening": listening,
                "active": active,
                "evidence": kind,
                "evidence_at": at,
                "state": LISTENING if listening else ACTIVE_NOT_LISTENING if active else SILENT,
                "reaction": _reaction(judged.get(identity)),
            }
        )
    return rows
