# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — forward a dead-letter blackhole signal to the peer hub that owns the target
"""Forward a dead-letter blackhole signal to the peer hub whose domain owns the target.

The dead-letter escalation (:mod:`synapse_channel.core.dead_letter_escalation`) tells *this*
hub's operators that a directed target is a growing blackhole. When that target's namespace is
owned by a **peer** hub — resolved through the same namespace-ownership and relay-route roster the
operator relay already uses — the peer is the side that can actually reach the missing reader, and
it has no way to learn of the gap on its own. This module builds the signal that tells it.

The signal is a **pointer, never a payload**. The dead-letter ledger holds only counts and names
(the message bodies live in the durable feed and are never carried cross-domain), so
:func:`forwarding_notice` can only ever name the blackholed target, how many of its directed
messages went undelivered here, and which hubs the origin and owner are. Re-delivery is therefore
impossible by construction, exactly as it is for the local escalation: forwarding points the owning
hub at a gap it owns, and never moves a message across a trust boundary.

The actual transmission is a seam (:class:`DeadLetterForwarder`) an origin hub calls, so a
deployment wires it to a real cross-hub transport while the resolution, the honesty bound, and the
durable origin-side audit are exercised without a live peer.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from synapse_channel.core.errors import SynapseError

FORWARDING_FIELD = "forwarding"
"""The wire envelope field the pointer is nested under.

The pointer is carried under its own field rather than spread across the envelope so its ``target``
key (the blackholed name) never collides with the envelope's reserved ``target`` (the recipient).
The transport nests the pointer here; :func:`decode_forwarding_notice` reads it back.
"""


class DeadLetterForwardError(SynapseError, RuntimeError):
    """Raised when forwarding a dead-letter signal to the owning peer hub fails.

    A single type for every transmission failure — connection, protocol, or timeout — so an
    origin hub catches one error and treats the forward as best-effort: the durable origin-side
    audit is already written, so a failed hand-off degrades to "recorded but not yet delivered"
    rather than losing the signal.
    """

    code = "dead_letter_forward"


def forwarding_notice(
    target: str,
    count: int,
    *,
    origin_hub_id: str,
    owner_hub_id: str,
) -> dict[str, Any]:
    """Return the honesty-bound cross-hub dead-letter signal for one blackholed target.

    The notice names only what the ledger holds — the target, its undelivered count, and the
    origin and owning hub ids — and carries **no message body**, so it points the owning hub at a
    gap without ever moving a message across the trust boundary.

    Parameters
    ----------
    target : str
        The directed-message target whose messages are dead-lettering on the origin hub.
    count : int
        How many of the target's directed messages went undelivered on the origin hub.
    origin_hub_id : str
        The id of the hub reporting the blackhole (where the messages arrived).
    owner_hub_id : str
        The id of the peer hub whose domain owns the target's namespace.

    Returns
    -------
    dict[str, Any]
        The JSON-compatible signal: ``target``, ``count``, ``origin_hub_id``, ``owner_hub_id``.
    """
    return {
        "target": target,
        "count": count,
        "origin_hub_id": origin_hub_id,
        "owner_hub_id": owner_hub_id,
    }


class DeadLetterForwarder(Protocol):
    """Transmits a dead-letter forwarding notice to the owning peer hub.

    The seam an origin hub calls to hand the signal to the peer that owns the target. A
    deployment supplies a real cross-hub transport; a test injects a stand-in so the resolution
    and audit wiring is exercised without a live peer. It is fire-and-forget — the origin does
    not await a verdict — and raises :class:`DeadLetterForwardError` on any transmission failure,
    which the caller treats as best-effort over the already-durable audit.
    """

    async def __call__(
        self,
        notice: dict[str, Any],
        *,
        uri: str,
        local_id: str,
        token: str | None = None,
    ) -> None:  # pragma: no cover - structural
        """Transmit ``notice`` to the owning hub at ``uri``."""
        ...


class DeadLetterForwardingWireError(SynapseError, ValueError):
    """Raised when an inbound dead-letter forwarding frame cannot be decoded to a pointer.

    The peer that sent the frame is a trust boundary, so the receiving hub validates every field
    before acting on it; a frame missing the pointer, or with a malformed target or count, raises
    this rather than yielding a half-built shape the receiver would journal or broadcast.
    """

    code = "dead_letter_forwarding_wire"


@dataclass(frozen=True, slots=True)
class ForwardingNotice:
    """The decoded, validated cross-hub dead-letter pointer an owning hub received from a peer.

    The structured counterpart of the dict :func:`forwarding_notice` builds — the same honesty-bound
    pointer (a blackholed target, its undelivered count, and the origin and owner hub ids), carrying
    no message body — after the receiving hub has validated it off the wire.

    Attributes
    ----------
    target : str
        The directed-message target the origin hub reports as blackholing.
    count : int
        How many of the target's directed messages went undelivered on the origin hub.
    origin_hub_id : str
        The id the frame claims as the reporting hub. It is the origin's self-asserted id; the
        receiving hub cross-checks it against the cryptographically verified sending peer.
    owner_hub_id : str
        The id the frame claims owns the target's namespace — this receiving hub.
    """

    target: str
    count: int
    origin_hub_id: str
    owner_hub_id: str


def decode_forwarding_notice(frame: Mapping[str, Any]) -> ForwardingNotice:
    """Decode and validate the pointer nested in an inbound forwarding ``frame``.

    Parameters
    ----------
    frame : Mapping[str, Any]
        The parsed wire frame, whose :data:`FORWARDING_FIELD` holds the nested pointer.

    Returns
    -------
    ForwardingNotice
        The validated pointer.

    Raises
    ------
    DeadLetterForwardingWireError
        When the pointer is absent or not a mapping, the target is missing or blank, the count is
        not a non-negative integer, or a hub id is not a string.
    """
    pointer = frame.get(FORWARDING_FIELD)
    if not isinstance(pointer, Mapping):
        msg = "dead-letter forwarding frame carries no pointer"
        raise DeadLetterForwardingWireError(msg)
    return ForwardingNotice(
        target=_require_nonempty(pointer.get("target"), "target"),
        count=_require_count(pointer.get("count")),
        origin_hub_id=_require_str(pointer.get("origin_hub_id"), "origin_hub_id"),
        owner_hub_id=_require_str(pointer.get("owner_hub_id"), "owner_hub_id"),
    )


def incoming_forwarding_notice(target: str, count: int, origin_hub_id: str) -> str:
    """Return the one-line operator message for a blackhole a peer reports for a target we own."""
    return (
        f"dead-letter forwarding: peer hub {origin_hub_id!r} reports {count} directed messages to "
        f"{target!r} — a name this domain owns — reached no live connection there; the reader is "
        f"not draining it on the peer"
    )


def _require_str(value: object, name: str) -> str:
    """Return ``value`` as a string or raise :class:`DeadLetterForwardingWireError`."""
    if not isinstance(value, str):
        msg = f"dead-letter forwarding pointer field {name!r} must be a string"
        raise DeadLetterForwardingWireError(msg)
    return value


def _require_nonempty(value: object, name: str) -> str:
    """Return ``value`` as a non-blank string or raise :class:`DeadLetterForwardingWireError`."""
    text = _require_str(value, name)
    if not text.strip():
        msg = f"dead-letter forwarding pointer field {name!r} must not be blank"
        raise DeadLetterForwardingWireError(msg)
    return text


def _require_count(value: object) -> int:
    """Return ``value`` as a non-negative integer or raise :class:`DeadLetterForwardingWireError`.

    ``bool`` is rejected though it subclasses ``int``: a count is a cardinality, never a flag.
    """
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        msg = "dead-letter forwarding pointer field 'count' must be a non-negative integer"
        raise DeadLetterForwardingWireError(msg)
    return value
