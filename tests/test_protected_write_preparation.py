# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — parent-bound recovery admission
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

from synapse_channel.core.atomic_operations import canonical_request_digest
from synapse_channel.core.journal import record_claim
from synapse_channel.core.message_auth import MessageAuthKey, sign_frame
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protected_write_admission import (
    ProtectedWriteAdmission,
    bind_protected_write_admission,
    finalize_protected_write_admission,
)
from synapse_channel.core.protected_write_admission_journal import (
    ADMISSION_EVENT_KIND,
    protected_admission_event_payload,
)
from synapse_channel.core.protected_write_inspection import (
    ProtectedFileInspection,
    verify_protected_parent_bindings,
)
from synapse_channel.core.protected_write_lifecycle import ProtectedWriteReservation
from synapse_channel.core.protected_write_preparation import (
    PreparedProtectedWrite,
    ProtectedPreparationPolicy,
    prepare_protected_writer_input,
    verify_prepared_begin,
)
from synapse_channel.core.protected_write_proposal import parse_protected_write_proposal
from synapse_channel.core.state import SynapseState
from test_protected_write_admission import CONTEXT, _claim
from test_protected_write_content import CONTENT
from test_protected_write_effects import _plan
from test_protected_write_inspection import _directory_inspection, _inspect
from test_protected_write_proposal import LIMITS
from test_protected_write_result import _result
from test_protected_write_transition_journal import _begin, _pending

pytestmark = pytest.mark.skipif(os.name != "posix", reason="real POSIX preparation observations")


def _begun(tmp_path: Path) -> tuple[EventStore, PreparedProtectedWrite, SynapseState]:
    admission, proposal, policy, observations = _inputs(tmp_path)
    store = EventStore(tmp_path / "prepared-begin.db")
    claim = _claim()
    record_claim(store, claim)
    state = SynapseState()
    state.claims[claim.task_id] = claim
    request = json.loads(admission.request_bytes)
    response = json.loads(admission.result_bytes)
    context = CONTEXT
    store.commit_operation(
        operation_key=admission.operation_key,
        request_digest=canonical_request_digest(request),
        response=response,
        events=(
            (
                ADMISSION_EVENT_KIND,
                protected_admission_event_payload(
                    admission.request_bytes, context=context, limits=policy.limits
                ),
            ),
        ),
        intent={"family": "prepared-admission"},
        finalize_response=lambda draft, sequences: finalize_protected_write_admission(
            draft,
            sequences,
            candidate=state,
            request=admission.request_bytes,
            context=context,
            limits=policy.limits,
            reason_codes=frozenset(),
            max_reservations=10,
            sign_response=lambda frame: sign_frame(
                frame,
                key=MessageAuthKey("authority", b"prepared-begin-test-only"),
                nonce="prepared-admission",
                sequence=2,
                timestamp=CONTEXT.now,
            ),
        ),
    )
    prepared = prepare_protected_writer_input(
        state.protected_write_admissions["reservation"],
        json.dumps(proposal),
        policy=policy,
        authenticated_writer=(CONTEXT.writer_principal, CONTEXT.writer_incarnation),
        artifacts={("text", "source-content"): CONTENT},
        inspections=observations,
    )
    _begin(store, state)
    return store, prepared, state


def test_prepared_begin_requires_actual_matching_journal_operation(tmp_path: Path) -> None:
    store, prepared, state = _begun(tmp_path)
    try:
        checked = verify_prepared_begin(
            prepared,
            state=state,
            store=store,
            authenticated_writer=(CONTEXT.writer_principal, CONTEXT.writer_incarnation),
            # Unit-only positive policy callback; activation needs actor-consistent current begin
            # and trust lookup.
            authorize_current=lambda _p, _r: True,
        )
        assert checked.transition_sequence == 4
        assert checked is state.protected_write_reservations["reservation"]
        assert store.count() == 5
    finally:
        store.close()


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "custody",
        "lineage",
        "not-begun",
        "writer",
        "response",
        "policy",
        "truthy-policy",
        "revoked",
    ],
)
def test_prepared_begin_refuses_stale_or_unproven_execution(tmp_path: Path, case: str) -> None:
    store, prepared, state = _begun(tmp_path)
    current = state.protected_write_reservations["reservation"]
    writer = (CONTEXT.writer_principal, CONTEXT.writer_incarnation)
    decision = True
    if case == "missing":
        state.protected_write_reservations.clear()
    elif case == "custody":
        state.protected_claim_custody.clear()
    elif case == "lineage":
        state.protected_write_recoveries["reservation"] = "child"
    elif case == "not-begun":
        state.protected_write_reservations["reservation"] = ProtectedWriteReservation.admitted(
            current.admission
        )
    elif case == "writer":
        writer = (CONTEXT.writer_principal, "other-incarnation")
    elif case == "response":
        altered = json.loads(current.result_bytes)
        altered["timestamp"] += 1.0
        state.protected_write_reservations["reservation"] = replace(
            current,
            result_bytes=json.dumps(altered, sort_keys=True, separators=(",", ":")).encode(),
        )
    elif case == "revoked":
        _pending(store, state)
    elif case == "policy":
        decision = False
    else:
        decision = cast(bool, 1)
    try:
        with pytest.raises(ValueError):
            verify_prepared_begin(
                prepared,
                state=state,
                store=store,
                authenticated_writer=writer,
                authorize_current=lambda _p, _r: decision,
            )
    finally:
        store.close()


@pytest.mark.parametrize(
    "case", ["missing-binding", "wrong-directory", "missing-observation", "wrong-planned-parent"]
)
def test_physical_parent_cannot_be_replaced_by_a_textual_enrollment(
    tmp_path: Path, case: str
) -> None:
    planned = case == "wrong-planned-parent"
    _, proposal, policy, observations = _inputs(tmp_path, planned)
    parents = dict(policy.enrolled_parents)
    if case == "missing-binding":
        parents.clear()
    elif case == "wrong-directory":
        parents[("memory", "records/note.md")] = ("parent", "container")
        observations[("parent", "container")] = _directory_inspection(tmp_path, "container")
    elif case == "missing-observation":
        # Still provide the real parent's before-state through a separate alias
        # in the proposal, but not the alias selected by the parent map.
        parents[("memory", "records/note.md")] = ("parent", "unobserved")
    else:
        parents[("memory", "records/note.md")] = ("parent", "container")
    with pytest.raises(ValueError, match="parent"):
        verify_protected_parent_bindings(
            json.dumps(proposal),
            limits=policy.limits,
            inspections=observations,
            enrolled_roots=policy.enrolled_roots,
            enrolled_parents=parents,
        )


def _inputs(
    tmp_path: Path, planned: bool = False
) -> tuple[
    ProtectedWriteAdmission,
    dict[str, Any],
    ProtectedPreparationPolicy,
    dict[tuple[str, str], ProtectedFileInspection],
]:
    proposal = _plan()
    limits = replace(
        LIMITS,
        operation_limits=replace(LIMITS.operation_limits, root_ids=frozenset({"memory", "parent"})),
    )
    if planned:
        directory = {"kind": "directory", "mode": "0700"}
        proposal["auxiliary_operations"].insert(
            0,
            {
                "operation_id": "mkdir-records",
                "opcode": "mkdir",
                "content_reference": None,
                "paths": [
                    {
                        "root_id": "memory",
                        "relative_path": "records",
                        "before": {"kind": "absent"},
                        "after": directory,
                    }
                ],
            },
        )
        # The enclosing root is explicitly represented through another root ID.
        proposal["auxiliary_operations"].append(
            {
                "operation_id": "flush-container",
                "opcode": "fsync",
                "content_reference": None,
                "paths": [
                    {
                        "root_id": "parent",
                        "relative_path": "container",
                        "before": directory,
                        "after": directory,
                    }
                ],
            }
        )
    else:
        (tmp_path / "records").mkdir(mode=0o700)
    (tmp_path / "container").mkdir(mode=0o700)
    working_root = tmp_path / "container" if planned else tmp_path
    parent = _directory_inspection(working_root, "records")
    observations = {("memory", "records"): parent}
    if planned:
        observations[("parent", "container")] = _directory_inspection(tmp_path, "container")
    else:
        observations[("memory", "records/note.md")] = _inspect(tmp_path, "records/note.md")
    raw_request, raw_response = _result("admit")
    request, response = cast(dict[str, Any], raw_request), cast(dict[str, Any], raw_response)
    request["body"]["proposal"] = proposal
    request["proposal_sha256"] = parse_protected_write_proposal(
        json.dumps(proposal), limits=limits
    ).proposal_sha256
    response["proposal_sha256"] = request["proposal_sha256"]
    response["body"]["request_digest"] = canonical_request_digest(request)
    admission = bind_protected_write_admission(
        json.dumps(request),
        json.dumps(response),
        context=CONTEXT,
        claims={"task-1": _claim()},
        limits=limits,
        reason_codes=frozenset(),
    )
    permissions = {
        ("memory", "records/note.md"): frozenset({"create", "fsync"}),
        ("memory", "records"): frozenset({"mkdir", "fsync"}),
        ("parent", "container"): frozenset({"fsync"}),
    }
    policy = ProtectedPreparationPolicy(
        "enrollment",
        limits,
        {
            "memory": parent.directories[0],
            "parent": (tmp_path.stat().st_dev, tmp_path.stat().st_ino),
        },
        {("memory", "records/note.md"): frozenset({"task-1"})},
        permissions,
        {
            ("memory", "records/note.md"): ("memory", "records"),
            ("memory", "records"): ("parent", "container"),
        },
        {"text": lambda content, digest: hashlib.sha256(content).hexdigest() == digest},
        len(CONTENT),
    )
    permissions.clear()
    return admission, proposal, policy, observations


@pytest.mark.parametrize("planned", [False, True])
def test_preparation_requires_complete_bound_input(tmp_path: Path, planned: bool) -> None:
    admission, proposal, policy, observations = _inputs(tmp_path, planned)
    result = prepare_protected_writer_input(
        admission,
        json.dumps(proposal),
        policy=policy,
        authenticated_writer=(CONTEXT.writer_principal, CONTEXT.writer_incarnation),
        artifacts={("text", "source-content"): CONTENT},
        inspections=observations,
    )
    assert result.admission == admission
    assert result.content.operation_content == (("create-record", CONTENT),)
    assert policy.auxiliary_opcodes
    retained = dict(result.observations)
    assert (retained[("memory", "records/note.md")] is None) == planned
    assert result.deferred_parents == (
        ((("memory", "records/note.md"), ("memory", "records")),) if planned else ()
    )
    observations.clear()
    assert result.observations
    with pytest.raises(TypeError):
        cast(Any, policy.enrolled_roots)["other"] = (1, 2)


@pytest.mark.parametrize(
    "case",
    ["proposal", "revision", "writer", "content", "permission", "before", "budget", "domain"],
)
def test_preparation_cannot_skip_a_required_gate(tmp_path: Path, case: str) -> None:
    admission, proposal, policy, observations = _inputs(tmp_path)
    writer = (CONTEXT.writer_principal, CONTEXT.writer_incarnation)
    artifacts = {("text", "source-content"): CONTENT}
    if case == "proposal":
        proposal["recovery_policy_revision"] = "changed"
    elif case == "revision":
        policy = replace(policy, enrollment_revision="changed")
    elif case == "writer":
        writer = ("wrong-writer", CONTEXT.writer_incarnation)
    elif case == "content":
        artifacts.clear()
    elif case == "permission":
        policy = replace(policy, auxiliary_opcodes={})
    elif case == "before":
        (tmp_path / "records" / "note.md").write_bytes(b"unexpected")
        observations[("memory", "records/note.md")] = _inspect(tmp_path, "records/note.md")
    elif case == "budget":
        policy = replace(policy, max_artifact_bytes=1)
    elif case == "domain":
        policy = replace(policy, domain_verifiers={})
    with pytest.raises(ValueError):
        prepare_protected_writer_input(
            admission,
            json.dumps(proposal),
            policy=policy,
            authenticated_writer=writer,
            artifacts=artifacts,
            inspections=observations,
        )
