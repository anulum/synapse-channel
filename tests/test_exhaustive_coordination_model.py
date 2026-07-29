# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — bounded exhaustive coordination-state model tests
from __future__ import annotations

import json

import pytest
from tools import exhaustive_coordination_model as model

from synapse_channel.core.state import SynapseState
from synapse_channel.core.state_models import TaskClaim


def test_depth_four_exhausts_every_real_state_action_trace() -> None:
    report = model.explore(depth=4)
    action_count = len(model.ACTIONS)

    assert report["depth"] == 4
    assert report["action_count"] == action_count
    assert report["traces"] == sum(action_count**length for length in range(5))
    assert report["action_steps"] == sum(length * action_count**length for length in range(5))
    assert 0 < report["unique_states"] <= report["traces"]
    assert report["checked_invariants"] == list(model.CHECKED_INVARIANTS)


def test_trace_covers_scope_refusal_cross_worktree_fencing_and_expiry() -> None:
    run = model.replay_trace(
        (
            "claim-a-t1-src",
            "claim-b-t2-src-child",
            "claim-b-t2-side-src",
            "stale-update-t1",
            "stale-release-t1",
            "expire",
        )
    )

    assert run.state.claims == {}
    assert run.state._epoch_seq == 2


def test_checker_detects_an_overlapping_two_owner_state() -> None:
    state = SynapseState()
    ok, _ = state.claim("A", "T1", now=model.START_TIME, worktree="main", paths=("src",))
    assert ok
    state.claims["T2"] = TaskClaim(
        task_id="T2",
        owner="B",
        note="",
        claimed_at=model.START_TIME,
        lease_expires_at=model.START_TIME + 100.0,
        worktree="main",
        paths=("src/a.py",),
        epoch=state._epoch_seq,
    )

    with pytest.raises(model.ModelViolation, match="INV-ME-2/3/4"):
        model._assert_invariants(state, model.START_TIME)


@pytest.mark.parametrize("depth", [-1, model.MAX_DEPTH + 1])
def test_exploration_depth_is_bounded(depth: int) -> None:
    with pytest.raises(ValueError, match="depth must be between"):
        model.explore(depth)


def test_cli_emits_the_deterministic_report(capsys: pytest.CaptureFixture[str]) -> None:
    assert model.main(["--depth", "1"]) == 0
    report = json.loads(capsys.readouterr().out)

    assert report["depth"] == 1
    assert report["traces"] == 1 + len(model.ACTIONS)
    assert report["action_steps"] == len(model.ACTIONS)
