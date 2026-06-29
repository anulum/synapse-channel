# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — multi-hub event-log union regressions

from __future__ import annotations

from synapse_channel.core.multihub_merge import (
    HubEvent,
    hub_cursors,
    merge_event_logs,
    tag_events,
)
from synapse_channel.core.persistence import StoredEvent


def _ev(hub: str, seq: int, ts: float, kind: str = "claim", **payload: object) -> HubEvent:
    return HubEvent(hub_id=hub, seq=seq, ts=ts, kind=kind, payload=payload)


def test_from_stored_tags_an_event_with_its_hub() -> None:
    stored = StoredEvent(seq=7, ts=12.5, kind="ledger_task", payload={"task_id": "a"})
    event = HubEvent.from_stored("hub-east", stored)
    assert event.hub_id == "hub-east"
    assert event.seq == 7
    assert event.ts == 12.5
    assert event.kind == "ledger_task"
    assert event.payload == {"task_id": "a"}
    assert event.identity == ("hub-east", 7)
    assert event.order_key == (12.5, "hub-east", 7)


def test_tag_events_tags_a_whole_log() -> None:
    stored = [
        StoredEvent(seq=1, ts=1.0, kind="claim", payload={}),
        StoredEvent(seq=2, ts=2.0, kind="claim", payload={}),
    ]
    tagged = tag_events("hub-a", stored)
    assert [event.identity for event in tagged] == [("hub-a", 1), ("hub-a", 2)]


def test_merge_unions_dedupes_and_orders_deterministically() -> None:
    a = [_ev("hub-a", 1, 1.0), _ev("hub-a", 2, 3.0)]
    b = [_ev("hub-b", 1, 2.0), _ev("hub-a", 2, 3.0)]  # last duplicates a's (hub-a, 2)
    merged = merge_event_logs(a, b)
    # the duplicate collapses to one; total order is (ts, hub_id, seq)
    assert [event.identity for event in merged] == [
        ("hub-a", 1),
        ("hub-b", 1),
        ("hub-a", 2),
    ]
    # merging is order-independent when identities do not conflict in content
    assert merge_event_logs(b, a) == merged


def test_merge_keeps_the_first_on_a_conflicting_identity() -> None:
    first = _ev("hub-a", 5, 1.0, payload_marker="first")
    second = _ev("hub-a", 5, 9.0, payload_marker="second")  # same (hub_id, seq), different content
    merged = merge_event_logs([first], [second])
    assert merged == (first,)
    assert merged[0].payload["payload_marker"] == "first"


def test_ties_in_timestamp_break_by_hub_then_seq() -> None:
    merged = merge_event_logs(
        [_ev("hub-b", 1, 5.0), _ev("hub-a", 2, 5.0), _ev("hub-a", 1, 5.0)],
    )
    assert [event.identity for event in merged] == [
        ("hub-a", 1),
        ("hub-a", 2),
        ("hub-b", 1),
    ]


def test_hub_cursors_report_the_high_water_seq_per_hub() -> None:
    events = [
        _ev("hub-a", 1, 1.0),
        _ev("hub-a", 4, 2.0),
        _ev("hub-a", 2, 3.0),  # out of order, lower than the running high-water
        _ev("hub-b", 7, 4.0),
    ]
    assert hub_cursors(events) == {"hub-a": 4, "hub-b": 7}


def test_hub_cursors_is_empty_for_no_events() -> None:
    assert hub_cursors([]) == {}
