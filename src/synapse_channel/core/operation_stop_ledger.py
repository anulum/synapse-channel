# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the stops one ledger records, and what it read back
"""Own the stop tables. Attempts are read through a port, never stored here."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from synapse_channel.core.operation_stop_readbacks import CLOSING_RESULTS, RESULT_UNSEALABLE
from synapse_channel.core.operation_stop_states import (
    ListedAttempt,
    StopState,
    compute_stop_state,
    profile_for,
)


class AttemptRead(Protocol):
    """What a stop may read about an attempt. It never sees the attempt table."""

    def reconciled(self, operation_id: str, attempt_id: str) -> bool:
        """Return whether the attempt is reconciled or its operation is closed."""

    def assurance(self, operation_id: str) -> str | None:
        """Return the operation's declared assurance, or ``None``."""


@dataclass
class _ScopeStops:
    """One scope's stops: the standing stop, its epoch, its listed operations and the line."""

    standing: int | None = None
    epoch: int = 0
    listed: tuple[str, ...] = ()
    line: int = 0


@dataclass
class _StopRow:
    """One recorded stop: its scope, the attempts it listed and what was read back for each."""

    scope: str
    epoch: int
    listed: tuple[tuple[str, str], ...]
    readbacks: dict[str, str]
    readback_seqs: dict[str, int] = field(default_factory=dict)
    resumed: StopState | None = None
    """The state computed when a resume lifted this stop; it stands from then on."""
    recorded_at: float = 0.0
    """The hub's wall-clock time of the stop; 0 when its row did not state one."""
    bound_triggered: bool = False
    """Whether the hub recorded the stop's ``stop_unsettled`` trigger."""


class StopLedger:
    """The stops and the read-backs kept for them."""

    def __init__(self) -> None:
        self._stops: dict[str, _ScopeStops] = {}
        self._stop_rows: dict[int, _StopRow] = {}

    @property
    def stops(self) -> dict[str, _ScopeStops]:
        """Return the live stops of every scope."""
        return self._stops

    @property
    def stop_rows(self) -> dict[int, _StopRow]:
        """Return the live recorded stops by journal sequence."""
        return self._stop_rows

    def publish_from(self, stops: dict[str, _ScopeStops], stop_rows: dict[int, _StopRow]) -> None:
        """Install the candidate's stop tables by reference."""
        self._stops = stops
        self._stop_rows = stop_rows

    def note_stop(
        self,
        scope: str,
        seq: int,
        *,
        epoch: int,
        listed: tuple[tuple[str, str], ...],
        recorded_at: float = 0.0,
    ) -> None:
        """Record that ``scope`` is stopped by the row at ``seq``, listing (operation, attempt)."""
        stops = self._stops.setdefault(scope, _ScopeStops())
        names = tuple(dict.fromkeys(operation_id for operation_id, _attempt in listed))
        stops.standing, stops.epoch, stops.listed, stops.line = seq, epoch, names, seq
        self._stop_rows[seq] = _StopRow(scope, epoch, listed, {}, recorded_at=recorded_at)

    def lists(self, stop_seq: int, operation_id: str, attempt_id: str) -> bool:
        """Return whether the stop recorded at ``stop_seq`` listed this attempt."""
        row = self._stop_rows.get(stop_seq)
        return row is not None and (operation_id, attempt_id) in row.listed

    def record_readback(self, stop_seq: int, attempt_id: str, result: str, *, seq: int) -> None:
        """Keep a read-back without overwriting a closed or unsealable one.

        An ``unread`` read-back may be superseded only by a closing result.
        """
        row = self._stop_rows.get(stop_seq)
        if row is not None:
            current = row.readbacks.get(attempt_id)
            if current is None or (
                current not in (*CLOSING_RESULTS, RESULT_UNSEALABLE) and result in CLOSING_RESULTS
            ):
                row.readbacks[attempt_id] = result
                row.readback_seqs[attempt_id] = seq

    def readback_of(self, stop_seq: int, attempt_id: str) -> str | None:
        """Return the kept read-back result of an attempt for a stop, or ``None``."""
        row = self._stop_rows.get(stop_seq)
        return None if row is None else row.readbacks.get(attempt_id)

    def kept_readback(self, stop_seq: int, attempt_id: str) -> tuple[str, int] | None:
        """Return ``(result, seq)`` of the kept read-back for ``(stop_seq, attempt_id)``."""
        row = self._stop_rows.get(stop_seq)
        if row is None or attempt_id not in row.readbacks:
            return None
        return row.readbacks[attempt_id], row.readback_seqs.get(attempt_id, 0)

    def listed(self, stop_seq: int) -> tuple[tuple[str, str], ...]:
        """Return the pairs ``stop_seq`` listed, or none when it was not recorded."""
        row = self._stop_rows.get(stop_seq)
        return () if row is None else row.listed

    def mark_bound_triggered(self, stop_seq: object) -> None:
        """Record that the hub wrote the stop's bound trigger, when ``stop_seq`` is one."""
        if type(stop_seq) is not int:
            return
        row = self._stop_rows.get(stop_seq)
        if row is not None:
            row.bound_triggered = True

    def state(self, stop_seq: int, attempts: AttemptRead) -> StopState | None:
        """Return the computed state of the stop recorded at ``stop_seq``, or ``None``.

        The state reads only the kept read-backs and the attempts' reconciliation;
        an executor's report is never read. The profile is ``cooperative``: the
        hub keeps no evidence of the isolated profile. A resumed stop keeps the
        state computed at its resume.
        """
        row = self._stop_rows.get(stop_seq)
        if row is None:
            return None
        if row.resumed is not None:
            return row.resumed
        facts = [
            ListedAttempt(
                operation_id,
                attempt_id,
                row.readbacks.get(attempt_id),
                attempts.reconciled(operation_id, attempt_id),
            )
            for operation_id, attempt_id in row.listed
        ]
        assurances = [attempts.assurance(operation_id) for operation_id, _attempt in row.listed]
        profile = profile_for(assurances, isolation_evidence=False)
        return compute_stop_state(facts, closing=CLOSING_RESULTS, profile=profile)

    def resume(self, scope: str, seq: int, attempts: AttemptRead) -> None:
        """Record that the row at ``seq`` lifted the stop of ``scope``; its state stands as now."""
        stops = self._stops.setdefault(scope, _ScopeStops())
        row = None if stops.standing is None else self._stop_rows.get(stops.standing)
        if row is not None and stops.standing is not None:
            row.resumed = self.state(stops.standing, attempts)
        stops.standing, stops.line = None, seq

    def standing_stop(self, scope: str) -> tuple[int, int] | None:
        """Return the standing stop of ``scope`` as (journal sequence, epoch), or ``None``."""
        stops = self._stops.get(scope)
        if stops is None or stops.standing is None:
            return None
        return stops.standing, stops.epoch

    def epoch(self, scope: str) -> int:
        """Return the epoch of the latest stop of ``scope``; 0 when it was never stopped."""
        stops = self._stops.get(scope)
        return 0 if stops is None else stops.epoch

    def stop_identity(self, stop_seq: int) -> tuple[str, int] | None:
        """Return the scope and epoch of the stop recorded at ``stop_seq``, or ``None``."""
        row = self._stop_rows.get(stop_seq)
        return None if row is None else (row.scope, row.epoch)

    def stops_listing(self, operation_id: str) -> tuple[int, ...]:
        """Return the sequences of the recorded stops that listed an attempt of the operation."""
        return tuple(
            seq
            for seq, row in self._stop_rows.items()
            if any(listed_op == operation_id for listed_op, _attempt in row.listed)
        )

    def listed_pair_count(self, operation_id: str) -> int:
        """Return how many recorded stop listings name an attempt of ``operation_id``."""
        return sum(
            1
            for row in self._stop_rows.values()
            for listed_op, _attempt in row.listed
            if listed_op == operation_id
        )

    def stop_closed(self, stop_seq: int) -> bool:
        """Return whether every attempt the stop listed has a ``sealed`` or ``applied`` read-back.

        An executor's own report is not read: a stop trusts markers, not reports.
        A stop that listed nothing is closed; an unknown sequence is not.
        """
        row = self._stop_rows.get(stop_seq)
        if row is None:
            return False
        return all(row.readbacks.get(attempt) in CLOSING_RESULTS for _op, attempt in row.listed)
