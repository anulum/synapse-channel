# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — retained recovery input admission binding tests
"""Exercise pure input checks inside public recovery binding without granting custody."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from fixtures.protected_write_recovery_candidate_linux import _candidate
from fixtures.protected_write_recovery_snapshot_linux import (
    RecoveryFixture,
)
from fixtures.protected_write_recovery_snapshot_linux import (
    recovery_fixture as recovery_fixture,
)
from synapse_channel.core.atomic_operations import canonical_request_digest
from synapse_channel.core.message_auth import MessageAuthKey, sign_frame
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protected_write_admission import ProtectedWriteAdmission
from synapse_channel.core.protected_write_lifecycle import ProtectedWriteReservation
from synapse_channel.core.protected_write_recovery import bind_protected_recovery
from synapse_channel.core.protected_write_recovery_admission_inputs import (
    verify_protected_recovery_admission_inputs,
)
from synapse_channel.core.protected_write_recovery_candidate import (
    prepare_protected_recovery_candidate,
)
from synapse_channel.core.protected_write_request import protected_write_operation_key
from synapse_channel.core.protected_write_transition_journal import (
    TRANSITION_EVENT_KIND,
    finalize_protected_transition,
    protected_transition_event_payload,
)
from test_protected_write_admission import CONTEXT, _claim
from test_protected_write_admission_journal import POLICY
from test_protected_write_content import CONTENT
from test_protected_write_lifecycle import _frames
from test_protected_write_transition_journal import _pending, _state


@pytest.mark.parametrize(
    "case", ["exact", "advanced", "stale", "phase", "proposal", "domain", "parent", "verb"]
)
def test_public_recovery_binds_candidate_but_refuses_without_physical_proof(
    recovery_fixture: RecoveryFixture, tmp_path: Path, case: str
) -> None:
    """Real retained packets and authority transitions feed a fail-closed recovery gate."""
    prepared, begun, execution_store, _evidence, _root = recovery_fixture
    proposal, frozen, policy = _candidate(recovery_fixture)
    with EventStore(tmp_path / "prepared-begin.db") as authority:
        state = _state(authority)
        assert state.protected_write_reservations["reservation"] == begun
        sequence = authority.read_all()[-1].seq + 1
        settle, settled, transition_context = _frames(
            begun,
            "settle",
            sequence,
            outcome="unknown",
            disposition="accepted",
            operation_phase="recovery_required",
            settlement_sequence=sequence,
        )
        settle["body"].update(
            outcome="unknown",
            operation_results=[
                {
                    "operation_id": operation["operation_id"],
                    "status": "not_started",
                    "evidence_reference": None,
                }
                for operation in json.loads(prepared.admission.proposal_bytes)[
                    "auxiliary_operations"
                ]
            ],
        )
        settled["body"]["request_digest"] = canonical_request_digest(settle)
        settled["body"]["evidence_reference"] = settle["body"]["quiescence_reference"]
        transition_policy = replace(POLICY, limits=prepared.policy.limits)
        updated = deepcopy(state)
        raw = json.dumps(settle)
        authority.commit_operation(
            operation_key=protected_write_operation_key(
                raw,
                limits=transition_policy.limits,
                authenticated_principal=transition_context.principal,
                authority_id=CONTEXT.authority_id,
                authority_continuity=CONTEXT.authority_continuity,
            ),
            request_digest=canonical_request_digest(settle),
            response=settled,
            events=(
                (
                    TRANSITION_EVENT_KIND,
                    protected_transition_event_payload(
                        begun,
                        raw,
                        context=transition_context,
                        policy=transition_policy,
                    ),
                ),
            ),
            intent={"family": "recovery-input-transition"},
            finalize_response=lambda draft, sequences: finalize_protected_transition(
                draft,
                sequences,
                candidate=updated,
                previous=begun,
                request=raw,
                context=transition_context,
                policy=transition_policy,
                sign_response=lambda frame: sign_frame(
                    frame,
                    key=MessageAuthKey("input-transition", b"input-transition-test-only"),
                    nonce="input-transition",
                    sequence=1,
                    timestamp=CONTEXT.now,
                ),
            ),
        )
        parent = updated.protected_write_reservations["reservation"]
        candidate = prepare_protected_recovery_candidate(
            json.dumps(proposal),
            parent=parent,
            original=prepared,
            evidence=frozen,
            policy=policy,
            artifacts={("text", "source-content"): CONTENT},
        )
        if case == "advanced":
            _pending(authority, updated)
            parent = updated.protected_write_reservations["reservation"]
            assert parent != candidate.parent
        claim = replace(_claim(), task_id="recovery-claim", owner="EXAMPLE/recovery")
        request = json.loads(prepared.admission.request_bytes)
        request.update(
            type="protected_write_recover",
            sender=claim.owner,
            transaction_id="recovery-transaction",
            session_id="recovery-session",
            body={"parent_reservation_id": parent.admission.reservation_id, "proposal": proposal},
            proposal_sha256=candidate.content.proposal_sha256,
        )
        request = dict(
            sign_frame(
                {key: value for key, value in request.items() if key not in {"auth", "signature"}},
                key=MessageAuthKey("recovery-input-service", b"input-service-test-only"),
                nonce="recovery-input",
                sequence=1,
                timestamp=CONTEXT.now,
            )
        )
        context = replace(
            CONTEXT,
            author_principal=claim.owner,
            writer_incarnation="recovery-incarnation",
            admission_sequence=authority.read_all()[-1].seq + 1,
        )
        result = json.loads(prepared.admission.result_bytes)
        for field in (
            "session_id",
            "transaction_id",
            "proposal_sha256",
            "enrollment_revision",
        ):
            result[field] = request[field]
        result["target"] = request["sender"]
        result["body"].update(
            request_type=request["type"],
            request_digest=canonical_request_digest(request),
            reservation_id="recovery-reservation",
            admission_sequence=context.admission_sequence,
            writer_incarnation=context.writer_incarnation,
        )
        result = dict(
            sign_frame(
                {key: value for key, value in result.items() if key not in {"auth", "signature"}},
                key=MessageAuthKey("recovery-input-authority", b"input-authority-test-only"),
                nonce="recovery-input-result",
                sequence=1,
                timestamp=CONTEXT.now,
            )
        )
        seen = []
        before_authority, before_execution = authority.read_all(), execution_store.read_all()

        def gate(current: ProtectedWriteReservation, child: ProtectedWriteAdmission) -> bool:
            """Check actual pure inputs but never invent the missing physical receipt."""
            checked_parent, checked_child = current, child
            if case == "stale":
                checked_parent = begun
            elif case == "phase":
                checked_parent = begun
            elif case == "proposal":
                checked_child = replace(child, proposal_bytes=prepared.admission.proposal_bytes)
            elif case in {"domain", "parent", "verb"}:
                changed = json.loads(child.request_bytes)
                if case == "domain":
                    changed["authority_continuity"] = "other"
                elif case == "parent":
                    changed["body"]["parent_reservation_id"] = "other"
                else:
                    changed["type"] = "protected_write_admit"
                checked_child = replace(child, request_bytes=json.dumps(changed).encode())
            checked_candidate = replace(candidate, parent=begun) if case == "phase" else candidate
            verify_protected_recovery_admission_inputs(
                checked_candidate,
                parent=checked_parent,
                admission=checked_child,
            )
            seen.append(child)
            return False

        expected = {
            "exact": "verified quiescence",
            "stale": "no longer actor-current",
            "advanced": "no longer actor-current",
            "phase": "current blocked custody",
            "proposal": "retained candidate proposal",
            "domain": "candidate authority domain",
            "parent": "candidate authority domain",
            "verb": "candidate authority domain",
        }
        with pytest.raises(ValueError, match=expected[case]):
            bind_protected_recovery(
                parent,
                json.dumps(request),
                json.dumps(result),
                context=context,
                claims={claim.task_id: claim},
                limits=policy.limits,
                reason_codes=frozenset(),
                verify_recovery=gate,
            )
        assert bool(seen) == (case == "exact")
        assert authority.read_all() == before_authority
        assert execution_store.read_all() == before_execution
        assert parent.holds_custody
