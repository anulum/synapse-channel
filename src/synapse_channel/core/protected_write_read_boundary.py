# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bounded read coherence across protected authority mutations
"""Reject captured snapshots crossing a protected mutation or unresolved custody."""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import cast

from synapse_channel.core.event_row_recovery import CORRUPT_EVENT_KIND
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.persistence import EventStore, StoredEvent
from synapse_channel.core.protected_write_lineage import (
    ProtectedReservationLineage,
    read_protected_lineage,
)
from synapse_channel.core.state import SynapseState


@dataclass(frozen=True)
class ProtectedReadLimits:
    """Mandatory operator-selected bounds for one local snapshot collection.

    Attributes
    ----------
    max_reservations:
        Maximum retained reservations examined for complete recovery lineage.
    max_files, max_bytes:
        Maximum captured entries and aggregate raw bytes, including metadata.
    max_intervening_events:
        Maximum journal events examined between the two ordered boundaries.
    timeout_seconds:
        Positive finite asynchronous deadline, at most 30 seconds. The trusted
        collector must honor cancellation; this is not a hard filesystem deadline.
    """

    max_reservations: int
    max_files: int
    max_bytes: int
    max_intervening_events: int
    timeout_seconds: float

    def __post_init__(self) -> None:
        """Refuse absent, boolean, excessive or nonfinite read budgets."""
        values = (
            (self.max_reservations, 100_000),
            (self.max_files, 10_000),
            (self.max_bytes, 256 * 1024 * 1024),
            (self.max_intervening_events, 100_000),
        )
        if any(type(value) is not int or not 0 < value <= maximum for value, maximum in values):
            raise ValueError("invalid protected read budget")
        if (
            type(self.timeout_seconds) is not float
            or not math.isfinite(self.timeout_seconds)
            or not 0 < self.timeout_seconds <= 30
        ):
            raise ValueError("invalid protected read deadline")


@dataclass(frozen=True)
class ProtectedReadSnapshot:
    """Detached content with observed journal positions, never a read or write grant.

    Attributes
    ----------
    content:
        Readonly copy of immutable bytes indexed by opaque collector-owned labels.
        Labels are not resolved as filesystem paths by this module.
    before_sequence, after_sequence:
        Actual retained journal positions bounding collection. Ordinary unrelated
        coordination may advance the latter without invalidating content.
    """

    content: Mapping[str, bytes] = field(repr=False)
    before_sequence: int
    after_sequence: int

    def __post_init__(self) -> None:
        """Detach byte entries and reject invalid sequence or mutable content values."""
        if (
            type(self.before_sequence) is not int
            or self.before_sequence < 0
            or type(self.after_sequence) is not int
            or self.after_sequence < self.before_sequence
        ):
            raise ValueError("invalid protected read sequence range")
        copied = dict(self.content)
        if any(type(name) is not str or type(raw) is not bytes for name, raw in copied.items()):
            raise ValueError("protected read content must contain immutable named bytes")
        object.__setattr__(self, "content", MappingProxyType(copied))


@dataclass(frozen=True)
class _ReadPosition:
    """Internal retained authority objects; never expose captured journal payloads."""

    state: SynapseState = field(repr=False)
    journal: EventStore = field(repr=False)
    actor: object = field(repr=False)
    sequence: int
    checkpoint: StoredEvent | None = field(repr=False)
    reservations: frozenset[str] = field(repr=False)
    admissions: frozenset[str] = field(repr=False)
    recoveries: frozenset[tuple[str, str]] = field(repr=False)


def _require_idle(state: SynapseState, limits: ProtectedReadLimits) -> None:
    """Require complete known terminal lineages with no outstanding custody."""
    reservations = state.protected_write_reservations
    if len(reservations) > limits.max_reservations:
        raise ValueError("protected read reservation budget exceeded")
    if state.protected_claim_custody:
        raise ValueError("protected read refuses outstanding write custody")
    names = set(reservations)
    children = set(state.protected_write_recoveries.values())
    if (
        names != set(state.protected_write_admissions)
        or not state.protected_write_recoveries.keys() <= names
        or not children <= names
    ):
        raise ValueError("protected read has incomplete reservation history")
    seen: set[str] = set()
    for name in names - children:
        # The actor pins existing names throughout this synchronous callback.
        # None is only the public lineage reader's unknown-reservation result;
        # known incomplete history or missing held custody already raises there.
        lineage = cast(
            ProtectedReservationLineage,
            read_protected_lineage(state, name, max_reservations=limits.max_reservations),
        )
        members = {item.admission.reservation_id for item in lineage.reservations}
        seen.update(members)
    if seen != names:
        raise ValueError("protected read has cyclic or unreachable recovery history")


def _position(hub: SynapseHub, state: SynapseState, limits: ProtectedReadLimits) -> _ReadPosition:
    """Read one actual journal/actor position after checking complete idle state."""
    journal = hub.journal
    if journal is None or hub.journal_corrupt_rows:
        raise ValueError("protected read requires a complete authority journal")
    _require_idle(state, limits)
    sequence = journal.max_seq()
    checkpoint = journal.latest_at_or_before(sequence)
    if (sequence and (checkpoint is None or checkpoint.seq != sequence)) or (
        checkpoint is not None and checkpoint.kind == CORRUPT_EVENT_KIND
    ):
        raise ValueError("protected read journal position is unavailable or corrupt")
    return _ReadPosition(
        state,
        journal,
        hub.state_mutations,
        sequence,
        checkpoint,
        frozenset(state.protected_write_reservations),
        frozenset(state.protected_write_admissions),
        frozenset(state.protected_write_recoveries.items()),
    )


def _finish(
    hub: SynapseHub, state: SynapseState, before: _ReadPosition, limits: ProtectedReadLimits
) -> int:
    """Refuse authority replacement, lost checkpoints and any intervening protected event."""
    after = _position(hub, state, limits)
    if (
        after.state is not before.state
        or after.journal is not before.journal
        or after.actor is not before.actor
        or after.sequence < before.sequence
        or after.journal.latest_at_or_before(before.sequence) != before.checkpoint
    ):
        raise ValueError("protected read authority or retained checkpoint changed")
    if (
        after.reservations != before.reservations
        or after.admissions != before.admissions
        or after.recoveries != before.recoveries
    ):
        raise ValueError("protected read retained reservation history changed")
    previous_sequence = before.sequence
    for index, event in enumerate(
        after.journal.iter_events(after_seq=before.sequence, through_seq=after.sequence), start=1
    ):
        if index > limits.max_intervening_events:
            raise ValueError("protected read intervening event budget exceeded")
        if event.seq != previous_sequence + 1:
            raise ValueError("protected read journal interval has missing events")
        previous_sequence = event.seq
        if event.kind.startswith("protected_") or event.kind == CORRUPT_EVENT_KIND:
            raise ValueError("protected read crossed a protected mutation or corrupt event")
    return after.sequence


def _capture_content(content: Mapping[str, bytes], limits: ProtectedReadLimits) -> dict[str, bytes]:
    """Detach only bounded immutable bytes with bounded opaque printable labels."""
    result: dict[str, bytes] = {}
    total = 0
    for index, (name, raw) in enumerate(content.items(), start=1):
        if (
            type(name) is not str
            or not name
            or len(name) > 4096
            or len(name.encode("utf-8")) > 4096
            or any(ord(char) < 32 or ord(char) == 127 for char in name)
            or type(raw) is not bytes
        ):
            raise ValueError("protected read content requires bounded immutable named bytes")
        total += len(name.encode("utf-8")) + len(raw)
        if index > limits.max_files or name in result:
            raise ValueError("protected read content entry budget exceeded")
        if total > limits.max_bytes:
            raise ValueError("protected read aggregate byte budget exceeded")
        result[name] = raw
    if not result:
        raise ValueError("protected read content entry budget exceeded")
    return result


def _require_deadline(deadline: float) -> None:
    """Refuse late publication even when blocking collection delayed asyncio timers."""
    if time.monotonic() >= deadline:
        raise TimeoutError("protected read deadline expired")


async def read_at_protected_write_boundary(
    hub: SynapseHub,
    *,
    limits: ProtectedReadLimits,
    collect: Callable[[], Awaitable[Mapping[str, bytes]]],
) -> ProtectedReadSnapshot:
    """Collect content outside the actor and refuse inconsistent protected authority state.

    Parameters
    ----------
    hub:
        Trusted sole authority with complete protected history and mutation actor.
    limits:
        Explicit finite content, history, traffic and asynchronous time bounds.
    collect:
        Trusted cancellation-aware service collector. It must authorize the caller,
        read through retained descriptors and verify the complete domain snapshot,
        registry/consumer closure and authority settlement. It returns immutable
        bytes, never an authorization boolean or an untrusted path resolver.

    Returns
    -------
    ProtectedReadSnapshot
        Detached bytes after two actor-ordered idle checks and a journal interval
        containing no protected mutations. Ordinary coordination may continue.

    Raises
    ------
    ValueError
        On pending custody/recovery, corrupt/replaced authority, crossed protected
        mutation, inconsistent history or content/traffic budget overflow.
    TimeoutError
        When the asynchronous read deadline expires. No retry is performed.

    Notes
    -----
    This is a coherence component for the separately authorized read service.
    It grants no caller authority and proves neither OS fencing nor complete
    domain/content/settlement validity. Physical exclusion of bypass writers,
    immutable installation and the collector's full domain checks are mandatory
    prerequisites. Target collection runs outside the mutation actor; bounded
    local journal checks run inside it. Elapsed time is checked between phases
    even if a blocking collector prevented timely asynchronous cancellation.
    """
    deadline = time.monotonic() + limits.timeout_seconds

    async def read() -> ProtectedReadSnapshot:
        """Collect immutable content between two bounded coherent authority positions."""
        before = await hub.state_mutations.run(
            hub.state, lambda state: _position(hub, state, limits)
        )
        _require_deadline(deadline)
        captured = await collect()
        _require_deadline(deadline)
        content = _capture_content(captured, limits)
        _require_deadline(deadline)
        after = await hub.state_mutations.run(
            hub.state, lambda state: _finish(hub, state, before, limits)
        )
        _require_deadline(deadline)
        return ProtectedReadSnapshot(content, before.sequence, after)

    try:
        return await asyncio.wait_for(read(), limits.timeout_seconds)
    except TimeoutError as error:
        raise TimeoutError("protected read deadline expired") from error
