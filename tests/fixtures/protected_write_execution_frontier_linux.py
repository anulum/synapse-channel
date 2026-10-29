# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — interrupted execution frontier evidence
"""Read real durable execution prefixes without granting a restart."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import pytest

from synapse_channel.core.persistence import EventStore, StoredOperation
from synapse_channel.core.protected_write_evidence_store import ProtectedExecutionEvidenceStore
from synapse_channel.core.protected_write_execution_journal import (
    inspect_protected_execution_frontier,
    record_protected_execution_start,
    verify_protected_execution_records,
)
from synapse_channel.core.protected_write_execution_observations import (
    verify_protected_execution_observations,
)
from synapse_channel.core.protected_write_execution_steps import record_protected_execution_step
from synapse_channel.core.protected_write_lifecycle import ProtectedWriteReservation
from synapse_channel.core.protected_write_preparation import PreparedProtectedWrite
from test_protected_write_execution_journal import completed_execution as completed_execution
from test_protected_write_preparation import _begun


@pytest.mark.parametrize("phase", ["before_step", "started", "failed", "unknown"])
@pytest.mark.parametrize("planned", [False, True])
def test_real_journal_reports_exact_uncertain_frontier(
    tmp_path: Path, phase: Literal["before_step", "started", "failed", "unknown"], planned: bool
) -> None:
    """Each crash boundary retains custody and cannot pass complete verification."""
    authority, prepared, state = _begun(tmp_path, planned=planned)
    begun = state.protected_write_reservations["reservation"]
    with EventStore(tmp_path / "execution.db") as service:
        try:
            execution = record_protected_execution_start(
                prepared, begun, service_journal=service, max_journal_operations=32
            )
            started = None
            terminal = None
            if phase != "before_step":
                started = record_protected_execution_step(
                    prepared,
                    execution,
                    execution.operation,
                    step_index=0,
                    phase="started",
                    service_journal=service,
                    max_journal_operations=32,
                ).operation
            if phase in {"failed", "unknown"}:
                assert started is not None
                terminal = record_protected_execution_step(
                    prepared,
                    execution,
                    started,
                    step_index=0,
                    phase=phase,
                    service_journal=service,
                    max_journal_operations=32,
                ).operation
            events_before = service.read_all()
            frontier = inspect_protected_execution_frontier(
                prepared, begun, service_journal=service
            )
            assert frontier.phase == phase
            assert frontier.step_index == 0
            assert frontier.start == execution.operation
            assert frontier.completed == ()
            assert frontier.terminal == (started if phase == "started" else terminal)
            assert service.read_all() == events_before
            with pytest.raises(ValueError, match="incomplete or uncertain"):
                verify_protected_execution_records(prepared, begun, service_journal=service)
            evidence = ProtectedExecutionEvidenceStore(
                service, "text", prepared.policy.limits.operation_limits, 4096, 64
            )
            with pytest.raises(ValueError, match="incomplete or uncertain"):
                verify_protected_execution_observations(
                    prepared,
                    begun,
                    service_journal=service,
                    read_evidence=evidence.read,
                    max_total_evidence_bytes=65536,
                )
            assert service.read_all() == events_before
        finally:
            authority.close()


@pytest.mark.parametrize(
    "suffix",
    ["1/start", "2/start", "3/result", "999/result", "-1/start", "00/result", "0/start/extra"],
)
def test_real_journal_refuses_step_beyond_missing_frontier(tmp_path: Path, suffix: str) -> None:
    """A later step cannot make an absent first step look like a safe prefix."""
    authority, prepared, state = _begun(tmp_path)
    begun = state.protected_write_reservations["reservation"]
    with EventStore(tmp_path / "execution.db") as service:
        try:
            execution = record_protected_execution_start(
                prepared, begun, service_journal=service, max_journal_operations=32
            )
            service.commit_operation(
                operation_key=f"{execution.operation.operation_key}/step/{suffix}",
                request_digest="a" * 64,
                response={"rogue": True},
                events=(("protected_write_execution_step", {"rogue": True}),),
                intent={"family": "protected-execution-evidence"},
            )
            with pytest.raises(ValueError, match="beyond its durable frontier"):
                inspect_protected_execution_frontier(prepared, begun, service_journal=service)
        finally:
            authority.close()


@pytest.mark.parametrize(
    "suffix",
    [
        "step/999/result",
        "step/-1/start",
        "step/00/result",
        "step/0/start/extra",
        "step",
        "status/forged",
        "stepx/0/start",
    ],
)
def test_real_completed_journal_refuses_unenrolled_step_keys(
    completed_execution: tuple[
        PreparedProtectedWrite, ProtectedWriteReservation, EventStore, tuple[StoredOperation, ...]
    ],
    suffix: str,
) -> None:
    """An actual completed file sequence cannot hide extra execution metadata.

    The shared-UID fixture qualifies storage and byte ordering only. Neither
    a complete execution chain nor this corruption test proves writer isolation.
    """
    prepared, begun, service, records = completed_execution
    assert verify_protected_execution_records(prepared, begun, service_journal=service) == records
    service.commit_operation(
        operation_key=f"{records[0].operation_key}/{suffix}",
        request_digest="a" * 64,
        response={"rogue": True},
        events=(("protected_write_execution_step", {"rogue": True}),),
        intent={"family": "protected-execution-evidence"},
    )
    before = service.read_all()
    with pytest.raises(ValueError, match="beyond its durable frontier"):
        verify_protected_execution_records(prepared, begun, service_journal=service)
    assert service.read_all() == before


@pytest.mark.parametrize("phase", ["invented", {"invented": True}])
def test_real_journal_refuses_unsupported_terminal_phase(tmp_path: Path, phase: object) -> None:
    """A durable operation with an invented result cannot become recovery evidence."""
    authority, prepared, state = _begun(tmp_path)
    begun = state.protected_write_reservations["reservation"]
    with EventStore(tmp_path / "execution.db") as service:
        try:
            execution = record_protected_execution_start(
                prepared, begun, service_journal=service, max_journal_operations=32
            )
            record_protected_execution_step(
                prepared,
                execution,
                execution.operation,
                step_index=0,
                phase="started",
                service_journal=service,
                max_journal_operations=32,
            )
            service.commit_operation(
                operation_key=f"{execution.operation.operation_key}/step/0/result",
                request_digest="b" * 64,
                response={"phase": phase},
                events=(("protected_write_execution_step", {"phase": phase}),),
                intent={"family": "protected-execution-evidence"},
            )
            with pytest.raises(ValueError, match="result phase is unsupported"):
                inspect_protected_execution_frontier(prepared, begun, service_journal=service)
        finally:
            authority.close()
