# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — dead-letter escalation threshold policy regressions

from __future__ import annotations

import pytest

from synapse_channel.core.dead_letter_escalation import (
    DEFAULT_DEAD_LETTER_ESCALATION_THRESHOLD,
    crosses_escalation_threshold,
    escalation_notice,
)


def test_default_threshold_is_disabled() -> None:
    assert DEFAULT_DEAD_LETTER_ESCALATION_THRESHOLD == 0


@pytest.mark.parametrize("count", [0, 1, 2, 4, 9, 11])
def test_no_escalation_between_multiples(count: int) -> None:
    assert crosses_escalation_threshold(count, 5) is False


@pytest.mark.parametrize("count", [5, 10, 15, 100])
def test_escalates_on_each_multiple(count: int) -> None:
    assert crosses_escalation_threshold(count, 5) is True


def test_threshold_of_one_escalates_every_message() -> None:
    assert all(crosses_escalation_threshold(n, 1) for n in range(1, 6))


@pytest.mark.parametrize("threshold", [0, -1, -10])
def test_non_positive_threshold_never_escalates(threshold: int) -> None:
    # A disabled or nonsensical threshold silences escalation even at a large count.
    assert crosses_escalation_threshold(1_000, threshold) is False


def test_zero_count_never_escalates() -> None:
    # count == 0 is a multiple of every threshold arithmetically, but there is nothing to escalate.
    assert crosses_escalation_threshold(0, 5) is False


def test_notice_names_the_target_count_and_sender() -> None:
    notice = escalation_notice("MY-PROJECT/agent", 20, "ceo")

    assert "20" in notice
    assert "'MY-PROJECT/agent'" in notice
    assert "'ceo'" in notice
    assert "no live connection" in notice
