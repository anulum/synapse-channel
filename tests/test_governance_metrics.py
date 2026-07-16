# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — governance metrics (M1–M4) regressions

from __future__ import annotations

from synapse_channel.core.governance_metrics import (
    ClaimViolationEvent,
    EditEvent,
    MainMoveEvent,
    PushEvent,
    compute_governance_metrics,
)


class TestEmptyAndClean:
    def test_no_events_is_clean_with_zero_rates(self) -> None:
        metrics = compute_governance_metrics([])
        assert metrics.m1_unclaimed_edit_rate == 0.0
        assert metrics.m2_ungated_self_push_rate == 0.0
        assert metrics.m3_unattested_main_move_rate == 0.0
        assert metrics.m4_max_time_to_detect_seconds == 0.0
        assert metrics.clean is True

    def test_a_claim_violation_makes_the_posture_not_clean(self) -> None:
        metrics = compute_governance_metrics([ClaimViolationEvent(1.0)])
        assert metrics.clean is False


class TestM1UnclaimedEdits:
    def test_all_edits_claimed_is_zero(self) -> None:
        metrics = compute_governance_metrics([EditEvent("a.py", True), EditEvent("b.py", True)])
        assert metrics.m1_unclaimed_edit_rate == 0.0
        assert metrics.total_edits == 2

    def test_half_unclaimed_is_one_half(self) -> None:
        metrics = compute_governance_metrics([EditEvent("a.py", True), EditEvent("b.py", False)])
        assert metrics.m1_unclaimed_edit_rate == 0.5

    def test_all_unclaimed_is_one(self) -> None:
        metrics = compute_governance_metrics([EditEvent("a.py", False)])
        assert metrics.m1_unclaimed_edit_rate == 1.0
        assert metrics.clean is False


class TestM2UngatedSelfPush:
    def test_self_push_past_a_forbidding_gate_is_one(self) -> None:
        metrics = compute_governance_metrics(
            [PushEvent(was_self_push=True, owner_gate_forbade_self_push=True)]
        )
        assert metrics.m2_ungated_self_push_rate == 1.0
        assert metrics.total_forbidding_pushes == 1

    def test_non_author_land_under_a_forbidding_gate_is_zero(self) -> None:
        metrics = compute_governance_metrics(
            [PushEvent(was_self_push=False, owner_gate_forbade_self_push=True)]
        )
        assert metrics.m2_ungated_self_push_rate == 0.0
        assert metrics.total_forbidding_pushes == 1

    def test_self_push_when_no_gate_forbids_is_not_counted(self) -> None:
        metrics = compute_governance_metrics(
            [PushEvent(was_self_push=True, owner_gate_forbade_self_push=False)]
        )
        assert metrics.m2_ungated_self_push_rate == 0.0
        assert metrics.total_forbidding_pushes == 0


class TestM3UnattestedMainMoves:
    def test_all_attested_is_zero(self) -> None:
        metrics = compute_governance_metrics([MainMoveEvent(True), MainMoveEvent(True)])
        assert metrics.m3_unattested_main_move_rate == 0.0
        assert metrics.total_main_moves == 2

    def test_one_unattested_of_four_is_a_quarter(self) -> None:
        metrics = compute_governance_metrics(
            [MainMoveEvent(True), MainMoveEvent(True), MainMoveEvent(True), MainMoveEvent(False)]
        )
        assert metrics.m3_unattested_main_move_rate == 0.25


class TestM4TimeToDetect:
    def test_max_time_to_detect_is_the_worst_latency(self) -> None:
        metrics = compute_governance_metrics(
            [ClaimViolationEvent(2.0), ClaimViolationEvent(9.5), ClaimViolationEvent(1.0)]
        )
        assert metrics.m4_max_time_to_detect_seconds == 9.5
        assert metrics.total_claim_violations == 3

    def test_no_violations_is_zero_latency(self) -> None:
        metrics = compute_governance_metrics([EditEvent("a.py", True)])
        assert metrics.m4_max_time_to_detect_seconds == 0.0


class TestMixedAndRobustness:
    def test_mixed_events_tally_independently(self) -> None:
        metrics = compute_governance_metrics(
            [
                EditEvent("a.py", False),
                EditEvent("b.py", True),
                PushEvent(was_self_push=True, owner_gate_forbade_self_push=True),
                MainMoveEvent(False),
                ClaimViolationEvent(3.0),
            ]
        )
        assert metrics.m1_unclaimed_edit_rate == 0.5
        assert metrics.m2_ungated_self_push_rate == 1.0
        assert metrics.m3_unattested_main_move_rate == 1.0
        assert metrics.m4_max_time_to_detect_seconds == 3.0
        assert metrics.clean is False

    def test_unknown_event_types_are_ignored(self) -> None:
        # A richer collector may pass a superset; a type this computation does not
        # recognise must be skipped, not counted or crash.
        metrics = compute_governance_metrics([object(), EditEvent("a.py", True)])  # type: ignore[list-item]
        assert metrics.total_edits == 1
        assert metrics.clean is True
