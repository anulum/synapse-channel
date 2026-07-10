# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — pure consume-liveness delivery partition regressions

from __future__ import annotations

from synapse_channel.core.directed_delivery_liveness import (
    NO_LIVE_RECIPIENT,
    NO_ONLINE_RECIPIENT,
    classify_delivery_liveness,
)


def test_no_socket_match_reports_no_online_recipient() -> None:
    decision = classify_delivery_liveness((), ())

    assert decision.delivered is False
    assert decision.reason == NO_ONLINE_RECIPIENT
    assert decision.matched_recipients == ()


def test_only_stale_socket_matches_report_no_live_recipient() -> None:
    decision = classify_delivery_liveness(("B", "A"), ("A", "B"))

    assert decision.delivered is False
    assert decision.reason == NO_LIVE_RECIPIENT
    assert decision.live_recipients == ()
    assert decision.stale_recipients == ("B", "A")


def test_a_live_match_keeps_the_positive_partition_and_stable_order() -> None:
    decision = classify_delivery_liveness(("B", "A", "B", "C"), ("A", "unknown"))

    assert decision.delivered is True
    assert decision.reason == ""
    assert decision.matched_recipients == ("B", "A", "C")
    assert decision.live_recipients == ("B", "C")
    assert decision.stale_recipients == ("A",)
