# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — recipient-liveness store: reaction tracking and staleness

from __future__ import annotations

from synapse_channel.core.agent_liveness import (
    DEFAULT_RECIPIENT_LIVENESS_WINDOW,
    WAITER_SUFFIX,
    RecipientLiveness,
)


class TestConstruction:
    def test_default_window_is_the_module_default(self) -> None:
        assert RecipientLiveness().window_seconds == DEFAULT_RECIPIENT_LIVENESS_WINDOW

    def test_window_is_kept_as_given(self) -> None:
        assert RecipientLiveness(window_seconds=42.0).window_seconds == 42.0

    def test_a_negative_window_is_clamped_to_zero(self) -> None:
        assert RecipientLiveness(window_seconds=-5.0).window_seconds == 0.0

    def test_waiter_suffix_is_the_rx_convention(self) -> None:
        assert WAITER_SUFFIX == "-rx"


class TestTouchAndRecall:
    def test_touch_records_the_reaction_instant(self) -> None:
        live = RecipientLiveness(window_seconds=10.0)
        live.touch("A", 7.0)

        assert live.last_reaction_at("A") == 7.0

    def test_touch_overwrites_an_earlier_reaction(self) -> None:
        live = RecipientLiveness(window_seconds=10.0)
        live.touch("A", 1.0)
        live.touch("A", 9.0)

        assert live.last_reaction_at("A") == 9.0

    def test_an_untouched_name_has_no_record(self) -> None:
        assert RecipientLiveness().last_reaction_at("ghost") is None

    def test_forget_drops_the_record(self) -> None:
        live = RecipientLiveness(window_seconds=10.0)
        live.touch("A", 3.0)
        live.forget("A")

        assert live.last_reaction_at("A") is None

    def test_forget_of_an_unknown_name_is_a_no_op(self) -> None:
        live = RecipientLiveness()
        live.forget("never-seen")  # must not raise

        assert live.last_reaction_at("never-seen") is None


class TestIsStale:
    def test_a_name_with_no_record_is_stale(self) -> None:
        assert RecipientLiveness(window_seconds=10.0).is_stale("ghost", now=1.0) is True

    def test_a_reaction_within_the_window_is_not_stale(self) -> None:
        live = RecipientLiveness(window_seconds=10.0)
        live.touch("A", 0.0)

        assert live.is_stale("A", now=5.0) is False

    def test_a_reaction_exactly_at_the_window_edge_is_not_stale(self) -> None:
        # is_stale uses a strict '>' so the boundary instant is still live.
        live = RecipientLiveness(window_seconds=10.0)
        live.touch("A", 0.0)

        assert live.is_stale("A", now=10.0) is False

    def test_a_reaction_older_than_the_window_is_stale(self) -> None:
        live = RecipientLiveness(window_seconds=10.0)
        live.touch("A", 0.0)

        assert live.is_stale("A", now=10.1) is True

    def test_a_clock_that_moved_backwards_is_never_stale(self) -> None:
        # A negative age can never exceed a non-negative window.
        live = RecipientLiveness(window_seconds=10.0)
        live.touch("A", 100.0)

        assert live.is_stale("A", now=90.0) is False

    def test_a_zero_window_makes_any_later_instant_stale(self) -> None:
        live = RecipientLiveness(window_seconds=0.0)
        live.touch("A", 0.0)

        assert live.is_stale("A", now=0.0) is False
        assert live.is_stale("A", now=0.001) is True
