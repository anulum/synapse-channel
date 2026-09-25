# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — parent-bound recovery admission
from __future__ import annotations

import json
from dataclasses import replace

import pytest

from synapse_channel.core.protected_write_lineage import read_protected_lineage
from synapse_channel.core.state import SynapseState
from test_protected_write_recovery import _transfer_inputs


def test_lineage_distinguishes_parent_outcome_from_current_custody() -> None:
    state, recovery = _transfer_inputs()
    before = read_protected_lineage(state, "reservation", max_reservations=2)
    assert before is not None
    assert before.custody_holder == "reservation"
    state.transfer_protected_write_custody(recovery, max_reservations=2)
    view = read_protected_lineage(state, "reservation", max_reservations=2)
    assert view is not None
    assert view.requested == recovery.parent
    assert view.current.admission == recovery.admission
    assert view.custody_holder == recovery.admission.reservation_id
    assert json.loads(view.requested.result_bytes)["body"]["outcome"] == "unknown"
    assert read_protected_lineage(state, "missing", max_reservations=2) is None


@pytest.mark.parametrize("budget", [True, 0, -1, 1.0])
def test_lineage_rejects_invalid_budget(budget: object) -> None:
    with pytest.raises(ValueError, match="budget"):
        read_protected_lineage(SynapseState(), "missing", max_reservations=budget)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "case",
    [
        "quota",
        "cycle",
        "dangling",
        "admission-only",
        "identity",
        "metadata",
        "inheritance",
        "terminal-custody",
        "double-custody",
        "settled-parent",
        "foreign-parent",
    ],
)
def test_lineage_refuses_inconsistent_state(case: str) -> None:
    state, recovery = _transfer_inputs()
    state.transfer_protected_write_custody(recovery, max_reservations=2)
    child_id = recovery.admission.reservation_id
    child = state.protected_write_reservations[child_id]
    budget = 2
    if case == "quota":
        budget = 1
    elif case == "cycle":
        state.protected_write_recoveries[child_id] = "reservation"
        del state.protected_claim_custody[child_id]
        budget = 3
    elif case == "dangling":
        del state.protected_write_reservations[child_id]
    elif case == "admission-only":
        del state.protected_write_reservations["reservation"]
    elif case == "identity":
        state.protected_write_reservations[child_id] = replace(
            child, admission=replace(child.admission, reservation_id="changed")
        )
    elif case == "metadata":
        del state.protected_write_admissions[child_id]
    elif case == "foreign-parent":
        source = json.loads(child.admission.request_bytes)
        source["body"]["parent_reservation_id"] = "foreign"
        admission = replace(child.admission, request_bytes=json.dumps(source).encode())
        state.protected_write_admissions[child_id] = admission
        state.protected_write_reservations[child_id] = replace(child, admission=admission)
    elif case == "inheritance":
        state.protected_write_reservations[child_id] = replace(child, inherited_custody=())
    elif case == "terminal-custody":
        del state.protected_claim_custody[child_id]
    elif case == "double-custody":
        state.protected_claim_custody["reservation"] = recovery.parent.custody
    elif case == "settled-parent":
        result = json.loads(recovery.parent.result_bytes)
        result["body"].update(operation_phase="settled", outcome="committed")
        state.protected_write_reservations["reservation"] = replace(
            recovery.parent, result_bytes=json.dumps(result).encode()
        )
    with pytest.raises(ValueError):
        read_protected_lineage(state, "reservation", max_reservations=budget)


def test_settled_descendant_does_not_rewrite_parent_or_imply_revocation() -> None:
    state, recovery = _transfer_inputs()
    state.transfer_protected_write_custody(recovery, max_reservations=2)
    child_id = recovery.admission.reservation_id
    child = state.protected_write_reservations[child_id]
    # Controlled trusted-state fixture. Full signed settlement/replay is exercised
    # by test_same_service_recovers_again_and_restarts_with_entire_ancestor_domain.
    result = json.loads(child.result_bytes)
    result["body"].update(operation_phase="settled", outcome="committed")
    state.protected_write_reservations[child_id] = replace(
        child, result_bytes=json.dumps(result).encode()
    )
    del state.protected_claim_custody[child_id]
    view = read_protected_lineage(state, "reservation", max_reservations=2)
    assert view is not None
    assert view.custody_holder is None
    assert json.loads(view.requested.result_bytes)["body"]["outcome"] == "unknown"
    assert json.loads(view.requested.result_bytes)["body"]["revocation_phase"] == "open"
    assert json.loads(view.current.result_bytes)["body"]["outcome"] == "committed"
