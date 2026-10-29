# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real recovery candidate input tests
"""Check recovery candidate plans against real persisted observations and artifacts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from typing import Any

import pytest

from fixtures.protected_write_recovery_evidence_linux import _collect
from fixtures.protected_write_recovery_snapshot_linux import RecoveryFixture, _create
from fixtures.protected_write_recovery_snapshot_linux import recovery_fixture as recovery_fixture
from synapse_channel.core.protected_write_execution_journal import record_protected_execution_start
from synapse_channel.core.protected_write_execution_steps import record_protected_execution_step
from synapse_channel.core.protected_write_preparation import ProtectedPreparationPolicy
from synapse_channel.core.protected_write_recovery_candidate import (
    prepare_protected_recovery_candidate,
)
from synapse_channel.core.protected_write_recovery_evidence import (
    FrozenProtectedRecoveryEvidence,
    read_protected_recovery_evidence,
)
from test_protected_write_content import CONTENT


def _candidate(
    fixture: RecoveryFixture,
) -> tuple[dict[str, Any], FrozenProtectedRecoveryEvidence, ProtectedPreparationPolicy]:
    """Collect actual initial observations and explicitly enroll the recovery claim path."""
    prepared, begun, service, evidence, _root = fixture
    record_protected_execution_start(
        prepared, begun, service_journal=service, max_journal_operations=64
    )
    ref = _collect(fixture)
    frozen = read_protected_recovery_evidence(
        ref,
        prepared=prepared,
        begun=begun,
        evidence_store=evidence,
        max_bytes=4096,
        max_total_evidence_bytes=16384,
    )
    proposal = json.loads(prepared.admission.proposal_bytes)
    for claim in proposal["claims"]:
        claim.update(task_id="recovery-claim", owner="EXAMPLE/recovery")
    for operation in proposal["operations"]:
        operation["claim_task_id"] = "recovery-claim"
    policy = replace(
        prepared.policy,
        primary_claims={
            ("memory", "records/note.md"): frozenset({"recovery-claim"}),
        },
    )
    return proposal, frozen, policy


def test_known_candidate_checks_real_packet_without_mutating_custody(
    recovery_fixture: RecoveryFixture,
) -> None:
    prepared, begun, service, _evidence, _root = recovery_fixture
    proposal, frozen, policy = _candidate(recovery_fixture)
    before = service.read_all()
    checked = prepare_protected_recovery_candidate(
        json.dumps(proposal),
        parent=begun,
        original=prepared,
        evidence=frozen,
        policy=policy,
        artifacts={("text", "source-content"): CONTENT},
    )
    assert checked.parent is begun and begun.holds_custody
    assert checked.content.operation_content == (("create-record", CONTENT),)
    assert checked.observations == frozen.observations
    assert not checked.deferred_parents
    assert service.read_all() == before
    assert json.loads(begun.result_bytes)["body"]["operation_phase"] == "executing"


@pytest.mark.parametrize(
    "case",
    [
        "parent",
        "revision",
        "roots",
        "project",
        "recovery_policy",
        "claim",
        "path",
        "permission",
        "before",
        "parent_binding",
        "content",
    ],
)
def test_candidate_refuses_unknown_or_unenrolled_recovery_inputs(
    recovery_fixture: RecoveryFixture,
    case: str,
) -> None:
    prepared, begun, service, _evidence, _root = recovery_fixture
    proposal, frozen, policy = _candidate(recovery_fixture)
    artifacts = {("text", "source-content"): CONTENT}
    if case == "parent":
        begun = replace(begun, admission=replace(begun.admission, reservation_id="foreign"))
    elif case == "revision":
        policy = replace(policy, enrollment_revision="foreign")
    elif case == "roots":
        policy = replace(policy, enrolled_roots={})
    elif case == "project":
        proposal["target_project"] = "FOREIGN"
    elif case == "recovery_policy":
        proposal["recovery_policy_revision"] = "foreign"
    elif case == "claim":
        proposal["claims"][0]["task_id"] = "task-1"
        proposal["operations"][0]["claim_task_id"] = "task-1"
    elif case == "path":
        proposal["operations"][0]["relative_path"] = "records/foreign"
        for operation in proposal["auxiliary_operations"]:
            for path in operation["paths"]:
                if path["relative_path"] == "records/note.md":
                    path["relative_path"] = "records/foreign"
    elif case == "permission":
        policy = replace(policy, primary_claims={})
    elif case == "before":
        state = proposal["auxiliary_operations"][0]["paths"][0]["after"]
        proposal["operations"][0].update(action="replace", before=state)
        proposal["auxiliary_operations"][0].update(opcode="write")
        proposal["auxiliary_operations"][0]["paths"][0]["before"] = state
        allowed = dict(policy.auxiliary_opcodes)
        allowed[("memory", "records/note.md")] |= {"write"}
        policy = replace(policy, auxiliary_opcodes=allowed)
    elif case == "parent_binding":
        policy = replace(policy, enrolled_parents={})
    else:
        artifacts[("text", "source-content")] = b"foreign"
    before = service.read_all()
    with pytest.raises(ValueError):
        prepare_protected_recovery_candidate(
            json.dumps(proposal),
            parent=begun,
            original=prepared,
            evidence=frozen,
            policy=policy,
            artifacts=artifacts,
        )
    assert service.read_all() == before


def test_real_uncertain_creation_can_prepare_a_known_state_recovery_write(
    recovery_fixture: RecoveryFixture,
) -> None:
    """An observed created inode may be rewritten only from its exact known bytes."""
    prepared, begun, service, evidence, _root = recovery_fixture
    execution = record_protected_execution_start(
        prepared,
        begun,
        service_journal=service,
        max_journal_operations=64,
    )
    proposal, _initial, policy = _candidate(recovery_fixture)
    record_protected_execution_step(
        prepared,
        execution,
        execution.operation,
        step_index=0,
        phase="started",
        service_journal=service,
        max_journal_operations=64,
    )
    _create(recovery_fixture)
    ref = _collect(recovery_fixture)
    frozen = read_protected_recovery_evidence(
        ref,
        prepared=prepared,
        begun=begun,
        evidence_store=evidence,
        max_bytes=4096,
        max_total_evidence_bytes=16384,
    )
    content = CONTENT + b"Recovery source: separately retained.\n"
    digest = hashlib.sha256(content).hexdigest()
    reference = {
        "domain": "text",
        "handle": "recovery-content",
        "sha256": digest,
        "size_bytes": len(content),
    }
    first = proposal["auxiliary_operations"][0]
    before = dict(first["paths"][0]["after"])
    after = {**before, "sha256": digest, "size_bytes": len(content)}
    first.update(opcode="write", content_reference=reference)
    first["paths"][0].update(before=before, after=after)
    proposal["operations"][0].update(
        action="replace", before=before, after_sha256=digest, after_size_bytes=len(content)
    )
    proposal["content_reference"] = reference
    proposal["auxiliary_operations"][1]["paths"][0].update(before=after, after=after)
    allowed = dict(policy.auxiliary_opcodes)
    allowed[("memory", "records/note.md")] |= {"write"}
    policy = replace(policy, auxiliary_opcodes=allowed, max_artifact_bytes=len(content))
    before_journal = service.read_all()
    checked = prepare_protected_recovery_candidate(
        json.dumps(proposal),
        parent=begun,
        original=prepared,
        evidence=frozen,
        policy=policy,
        artifacts={("text", "recovery-content"): content},
    )
    assert checked.content.operation_content == (("create-record", content),)
    observed = dict(checked.observations)[("memory", "records/note.md")]
    assert observed is not None and observed.file_identity is not None
    assert observed.sha256 == hashlib.sha256(CONTENT).hexdigest()
    assert service.read_all() == before_journal
