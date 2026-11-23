# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — protected-write state keeps its tables and refusals
"""The protected-write tables, their refusals, and publication by reference."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.core.journal_replay import replay
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protected_write_enrollment_journal import (
    ISSUE_EVENT_KIND,
    REVOKE_EVENT_KIND,
    enrollment_issue_payload,
)
from synapse_channel.core.state import SynapseState
from test_protected_write_admission import _admission, _claim
from test_protected_write_enrollment_journal import _enrollment
from test_protected_write_recovery import _transfer_inputs


def _ready() -> SynapseState:
    state = SynapseState()
    state.claims["task-1"] = _claim()
    return state


def test_a_refused_install_leaves_the_tables_unchanged() -> None:
    state = _ready()
    admission = _admission()
    cases = (
        (0, admission, "invalid protected reservation budget"),
        (2, replace(admission, claim_custody=()), "protected admission requires claim custody"),
    )
    for budget, record, message in cases:
        before = deepcopy(state)
        with pytest.raises(ValueError, match=message):
            state.install_protected_write_admission(record, max_reservations=budget)
        assert state.protected_write_admissions == before.protected_write_admissions
        assert state.protected_claim_custody == before.protected_claim_custody
        assert state.protected_write_reservations == before.protected_write_reservations
    state.claims["task-1"].version += 1
    before = deepcopy(state)
    with pytest.raises(ValueError, match="protected admission witness changed before installation"):
        state.install_protected_write_admission(admission, max_reservations=2)
    assert state.protected_claim_custody == before.protected_claim_custody
    state.claims["task-1"].version -= 1
    state.protected_claim_custody["other"] = admission.claim_custody
    before = deepcopy(state)
    with pytest.raises(ValueError, match="protected admission overlaps outstanding custody"):
        state.install_protected_write_admission(admission, max_reservations=2)
    assert state.protected_write_admissions == before.protected_write_admissions


def test_a_second_admission_exhausts_a_budget_of_one() -> None:
    state = _ready()
    state.install_protected_write_admission(_admission(), max_reservations=1)
    before = (
        deepcopy(state.protected_write_admissions),
        deepcopy(state.protected_claim_custody),
        deepcopy(state.protected_write_reservations),
    )
    with pytest.raises(ValueError, match="protected reservation budget exhausted"):
        state.install_protected_write_admission(
            replace(_admission(), reservation_id="reservation-2"),
            max_reservations=1,
        )
    assert (
        state.protected_write_admissions,
        state.protected_claim_custody,
        state.protected_write_reservations,
    ) == before


def test_install_records_the_admission_and_holds_exactly_its_scope() -> None:
    state = _ready()
    candidate = deepcopy(state)
    admission = _admission()
    candidate.install_protected_write_admission(admission, max_reservations=2)
    assert state.protected_write_admissions == {}
    state.publish_from(candidate)
    assert state.protected_write_admissions is candidate.protected_write_admissions
    assert state.protected_claim_custody is candidate.protected_claim_custody
    assert state.protected_write_reservations is candidate.protected_write_reservations
    assert state.protected_write_admissions == {"reservation": admission}
    assert state.protected_write_reservations["reservation"].holds_custody
    claim = state.claims["task-1"]
    assert state.protected_task_held("task-1")
    assert not state.protected_task_held("other")
    assert state.protected_scope_held("task-1", claim.worktree, claim.paths, claim.path_identity)
    assert not state.protected_scope_held("other", "/other", ("elsewhere",), None)


def test_transfer_inherits_custody_and_a_changed_witness_refuses() -> None:
    state, recovery = _transfer_inputs()
    state.protected_write_admissions[recovery.admission.reservation_id] = recovery.admission
    before = deepcopy(state)
    with pytest.raises(
        ValueError, match="recovery child identity or reservation budget unavailable"
    ):
        state.transfer_protected_write_custody(recovery, max_reservations=2)
    assert state.protected_claim_custody == before.protected_claim_custody
    del state.protected_write_admissions[recovery.admission.reservation_id]
    state.claims[next(iter(recovery.admission.claim_custody)).task_id].version += 1
    before = deepcopy(state)
    with pytest.raises(ValueError, match="recovery witness changed before transfer"):
        state.transfer_protected_write_custody(recovery, max_reservations=2)
    assert state.protected_write_recoveries == before.protected_write_recoveries
    state.claims[next(iter(recovery.admission.claim_custody)).task_id].version -= 1
    candidate = deepcopy(state)
    candidate.transfer_protected_write_custody(recovery, max_reservations=2)
    state.publish_from(candidate)
    child_id = recovery.admission.reservation_id
    parent_id = recovery.parent.admission.reservation_id
    child = state.protected_write_reservations[child_id]
    assert child.inherited_custody == recovery.parent.custody
    assert state.protected_claim_custody == {child_id: child.custody}
    assert parent_id not in state.protected_claim_custody
    assert state.protected_write_recoveries == {parent_id: child_id}


def test_a_transition_replaces_the_reservation_and_a_stale_one_does_not() -> None:
    state = _ready()
    admission = _admission()
    state.install_protected_write_admission(admission, max_reservations=2)
    previous = state.protected_write_reservations["reservation"]
    snapshot = (
        dict(state.protected_write_admissions),
        dict(state.protected_write_reservations),
        dict(state.protected_claim_custody),
    )
    stale = replace(previous, transition_sequence=previous.transition_sequence)
    with pytest.raises(ValueError, match="protected transition has a stale or changed predecessor"):
        state.apply_protected_write_transition(previous, stale)
    assert (
        state.protected_write_admissions,
        state.protected_write_reservations,
        state.protected_claim_custody,
    ) == snapshot
    updated = replace(previous, transition_sequence=previous.transition_sequence + 1)
    state.apply_protected_write_transition(previous, updated)
    assert state.protected_write_reservations["reservation"] == updated
    assert state.protected_claim_custody["reservation"] == previous.custody
    assert state.protected_write_admissions["reservation"] == admission


def test_the_tables_are_the_component_dictionaries_and_have_no_setter() -> None:
    state = SynapseState()
    assert state.protected_claim_custody is state.protected.protected_claim_custody
    assert state.protected_write_admissions is state.protected.protected_write_admissions
    assert state.protected_write_reservations is state.protected.protected_write_reservations
    assert state.protected_write_recoveries is state.protected.protected_write_recoveries
    assert state.protected_enrollments is state.protected.protected_enrollments
    assert state.protected_enrollment_key_ids is state.protected.protected_enrollment_key_ids
    assert (
        state.protected_enrollment_fingerprints is state.protected.protected_enrollment_fingerprints
    )
    state.protected_write_recoveries["parent"] = "child"
    assert state.protected.protected_write_recoveries["parent"] == "child"
    holder: Any = state
    with pytest.raises(AttributeError):
        holder.protected_claim_custody = {}


def test_enrollment_replay_records_and_revokes(tmp_path: Path) -> None:
    first = _enrollment()
    payload = enrollment_issue_payload(first)
    path = tmp_path / "enrollment.db"
    with EventStore(path) as store:
        store.append(ISSUE_EVENT_KIND, payload, durable=True)
        store.append(
            REVOKE_EVENT_KIND,
            {"schema_version": payload["schema_version"], "session_id": first.session_id},
            durable=True,
        )
    state = replay(EventStore(path), protected_write_policies={}).state
    record = state.protected_enrollments[first.session_id]
    assert record.revoked
    assert record.session_id == first.session_id
    assert record.key_id == payload["key_id"]
