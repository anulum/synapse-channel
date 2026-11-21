# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — compaction held by feed acknowledgements, and the floor a page reports
"""Compaction never deletes past a registered feed consumer's ack unless told to, and says so."""

from __future__ import annotations

import sqlite3
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from hub_e2e_helpers import AgentHandle, close_agents, connect_agent, running_hub
from synapse_channel import cli
from synapse_channel.core.compaction import FeedConsumerUnknown, RetentionPolicy, compact
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import (
    EventKind,
    record_compaction_floor,
    record_feed_ack,
    record_finding,
)
from synapse_channel.core.persistence import EventStore
from synapse_channel.hub_inbox import INBOX_READY_HANG_GUARD_SECONDS

_WAIT = INBOX_READY_HANG_GUARD_SECONDS
_HUB = "retention-hub"
_AGED = RetentionPolicy(finding_grace_seconds=0.0)


def _expired(store: EventStore, text: str) -> int:
    """Record a finding whose validity window closed long ago and return its sequence."""
    record_finding(store, {"statement": text, "validity": {"valid_from": 0.0, "valid_to": 1.0}})
    return store.max_seq()


def _instance(store: EventStore) -> str:
    instance = store.journal_instance()
    assert instance is not None
    return instance.instance


def _registered(store: EventStore) -> list[int]:
    """Journal four expired findings and the acks of two consumers, plus rows that never count."""
    findings = [_expired(store, f"f{index}") for index in range(4)]
    instance = _instance(store)
    for consumer, acked in (
        ("R/slow", findings[1]),
        ("R/fast", findings[2]),
        ("R/fast", findings[3]),
    ):
        record_feed_ack(
            store, consumer=consumer, hub_id=_HUB, journal_instance=instance, acked_seq=acked
        )
    record_feed_ack(store, consumer="R/gone", hub_id=_HUB, journal_instance="0" * 32, acked_seq=0)
    store.append(
        EventKind.FEED_ACK, {"consumer": "R/odd", "journal_instance": instance, "acked_seq": "1"}
    )
    store.append(EventKind.FEED_ACK, {"consumer": 7, "journal_instance": instance, "acked_seq": 1})
    return findings


def _floors(store: EventStore) -> list[dict[str, Any]]:
    return [event.payload for event in store.read_all() if event.kind == EventKind.COMPACTION_FLOOR]


def _statements(store: EventStore) -> list[str]:
    return [event.payload["statement"] for event in store.read_all() if event.kind == "finding"]


def test_the_lowest_registered_ack_holds_the_floor_and_the_run_is_journalled(
    tmp_path: Path,
) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        findings = _registered(store)
        requested = store.max_seq()
        result = compact(store, _AGED, floor_seq=requested, now=1000.0)
        floors, statements, instance = _floors(store), _statements(store), _instance(store)
    finally:
        store.close()

    held = (("R/fast", findings[3]), ("R/slow", findings[1]))
    assert (result.floor_seq, result.requested_floor_seq) == (findings[1], requested)
    assert (result.held_by_feed_acks, result.passed_feed_acks) == (held, ())
    assert result.findings_removed == 2
    assert statements == ["f2", "f3"]  # the rows above the slowest ack survive
    assert floors == [
        {
            "journal_instance": instance,
            "requested_floor": requested,
            "applied_floor": findings[1],
            "past_feed_acks": False,
            "held_by": [["R/fast", findings[3]], ["R/slow", findings[1]]],
            "passed_consumers": [],
            "released": [],
            "removed": 2,
        }
    ]


def test_a_consumer_released_by_name_no_longer_holds_the_floor(tmp_path: Path) -> None:
    gone, behind = tmp_path / "gone", tmp_path / "behind"
    gone.mkdir()
    behind.mkdir()
    store = EventStore(gone / "hub.db")
    try:
        findings = _registered(store)
        requested = store.max_seq()
        result = compact(
            store, _AGED, floor_seq=requested, now=1000.0, release_feed_consumers=["R/slow"]
        )
        floors, statements = _floors(store), _statements(store)
    finally:
        store.close()
    other = EventStore(behind / "hub.db")
    try:
        other_findings = _registered(other)
        ahead = compact(
            other, _AGED, floor_seq=other.max_seq(), now=1000.0, release_feed_consumers=["R/fast"]
        )
        other_floors = _floors(other)
    finally:
        other.close()

    # The slow consumer is gone: the fast one alone holds the floor, and the slow one is passed.
    assert (result.floor_seq, result.requested_floor_seq) == (findings[3], requested)
    assert result.held_by_feed_acks == (("R/fast", findings[3]),)
    assert result.passed_feed_acks == (("R/slow", findings[1]),)
    assert (result.findings_removed, statements) == (4, [])
    assert [
        (f["applied_floor"], f["past_feed_acks"], f["released"], f["passed_consumers"])
        for f in floors
    ] == [(findings[3], False, ["R/slow"], [["R/slow", findings[1]]])]
    # A released consumer at or above the floor the others hold loses nothing, so none is passed.
    assert (ahead.floor_seq, ahead.held_by_feed_acks) == (
        other_findings[1],
        (("R/slow", other_findings[1]),),
    )
    assert (ahead.passed_feed_acks, ahead.findings_removed) == ((), 2)
    assert [(f["released"], f["passed_consumers"]) for f in other_floors] == [(["R/fast"], [])]


def test_the_floor_is_journalled_before_any_row_is_deleted(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    store = EventStore(db)
    try:
        _registered(store)
        # A second connection records, at every deletion, how many floors are journalled.
        with closing(sqlite3.connect(db)) as observer:
            observer.execute("CREATE TABLE deletion_witness (floors INTEGER NOT NULL)")
            observer.execute(
                "CREATE TRIGGER witness BEFORE DELETE ON events BEGIN "
                "INSERT INTO deletion_witness SELECT count(*) FROM events "
                f"WHERE kind = '{EventKind.COMPACTION_FLOOR}'; END"
            )
            observer.commit()
        result = compact(store, _AGED, floor_seq=store.max_seq(), now=1000.0)
    finally:
        store.close()
    with closing(sqlite3.connect(db)) as observer:
        witnessed = [row[0] for row in observer.execute("SELECT floors FROM deletion_witness")]

    assert result.findings_removed == len(witnessed) == 2
    assert witnessed == [1, 1]  # the run's floor was durable before each row went


def test_a_release_holds_in_later_runs_until_the_consumer_acks_again(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        findings = _registered(store)
        released = compact(
            store, _AGED, floor_seq=store.max_seq(), now=1000.0, release_feed_consumers=["R/slow"]
        )
        later = [_expired(store, f"later {index}") for index in range(2)]
        instance = _instance(store)
        record_feed_ack(
            store, consumer="R/fast", hub_id=_HUB, journal_instance=instance, acked_seq=later[1]
        )
        ordinary = compact(store, _AGED, floor_seq=store.max_seq(), now=1000.0)
        _expired(store, "after the slow consumer's return")
        record_feed_ack(
            store, consumer="R/slow", hub_id=_HUB, journal_instance=instance, acked_seq=later[1]
        )
        returned = compact(store, _AGED, floor_seq=store.max_seq(), now=1000.0)
        statements = _statements(store)
    finally:
        store.close()

    assert released.passed_feed_acks == (("R/slow", findings[1]),)
    # The release holds: the next ordinary run is held by the fast consumer alone.
    assert ordinary.held_by_feed_acks == (("R/fast", later[1]),)
    assert (ordinary.floor_seq, ordinary.findings_removed) == (later[1], 2)
    # An ack after the release registers the slow consumer again, and it holds too.
    assert returned.held_by_feed_acks == (("R/fast", later[1]), ("R/slow", later[1]))
    assert (returned.floor_seq, returned.findings_removed) == (later[1], 0)
    assert statements == ["after the slow consumer's return"]


def test_an_unknown_release_or_one_with_the_override_is_refused_before_any_deletion(
    tmp_path: Path,
) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        _registered(store)
        before = store.count()
        requested = store.max_seq()
        with pytest.raises(
            FeedConsumerUnknown, match="not a registered feed consumer: R/gone, R/x"
        ):
            # R/gone holds an ack of another instance only, so it is not registered
            compact(
                store,
                _AGED,
                floor_seq=requested,
                now=1000.0,
                release_feed_consumers=["R/x", "R/gone"],
            )
        with pytest.raises(ValueError, match="not both"):
            compact(
                store,
                _AGED,
                floor_seq=requested,
                past_feed_acks=True,
                release_feed_consumers=["R/slow"],
            )
        counted, floors = store.count(), _floors(store)
    finally:
        store.close()
    bare = EventStore(tmp_path / "bare.db")
    try:
        _expired(bare, "f0")
        with pytest.raises(FeedConsumerUnknown, match="R/slow"):
            compact(bare, _AGED, floor_seq=bare.max_seq(), release_feed_consumers=["R/slow"])
        bare_count = bare.count()
    finally:
        bare.close()

    assert (counted, floors, bare_count) == (before, [], 1)


def test_an_explicit_override_compacts_past_the_acks_and_journals_whom_it_passed(
    tmp_path: Path,
) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        findings = _registered(store)
        requested = store.max_seq()
        result = compact(store, _AGED, floor_seq=requested, now=1000.0, past_feed_acks=True)
        floors, statements = _floors(store), _statements(store)
    finally:
        store.close()

    passed = (("R/fast", findings[3]), ("R/slow", findings[1]))
    assert (result.floor_seq, result.requested_floor_seq) == (requested, None)
    assert (result.held_by_feed_acks, result.passed_feed_acks) == ((), passed)
    assert (result.findings_removed, statements) == (4, [])
    assert [(f["applied_floor"], f["past_feed_acks"], f["removed"]) for f in floors] == [
        (requested, True, 4)
    ]
    assert floors[0]["passed_consumers"] == [["R/fast", findings[3]], ["R/slow", findings[1]]]


def test_a_floor_is_journalled_only_when_a_run_removes_a_row_or_passes_a_consumer(
    tmp_path: Path,
) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        store.append(EventKind.CHAT, {"payload": "before every finding"})
        first = _expired(store, "f0")
        record_feed_ack(
            store, consumer="R/slow", hub_id=_HUB, journal_instance=_instance(store), acked_seq=1
        )
        requested = store.max_seq()
        dry = compact(store, _AGED, floor_seq=requested, now=1000.0, dry_run=True)
        held = compact(store, _AGED, floor_seq=requested, now=1000.0)
        after_held = _floors(store)
        nothing = RetentionPolicy(max_checkpoints_per_task=1)
        passing = compact(store, nothing, floor_seq=requested, past_feed_acks=True)
        floors, statements = _floors(store), _statements(store)
    finally:
        store.close()

    assert (dry.floor_seq, dry.total_removed, dry.held_by_feed_acks) == (1, 0, (("R/slow", 1),))
    assert (held.floor_seq, held.total_removed) == (1, 0)  # nothing at or below the ack
    assert after_held == []  # neither the dry run nor the empty held run journalled a floor
    assert (passing.floor_seq, passing.total_removed) == (requested, 0)
    assert [(f["applied_floor"], f["removed"], f["passed_consumers"]) for f in floors] == [
        (requested, 0, [["R/slow", 1]])
    ]
    assert statements == ["f0"] and first == 2


def test_without_a_registered_consumer_the_requested_floor_applies(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    store = EventStore(db)
    try:
        _expired(store, "f0")
        record_feed_ack(
            store, consumer="R/gone", hub_id=_HUB, journal_instance="0" * 32, acked_seq=0
        )
        free = compact(store, _AGED, floor_seq=store.max_seq(), now=1000.0)
        _expired(store, "f1")
        record_feed_ack(
            store, consumer="R/kept", hub_id=_HUB, journal_instance=_instance(store), acked_seq=1
        )
        with closing(sqlite3.connect(db)) as connection:
            connection.execute("UPDATE store_format SET journal_instance = NULL")
            connection.commit()
        lost = compact(store, _AGED, floor_seq=store.max_seq(), now=1000.0)
        floors = _floors(store)
    finally:
        store.close()

    assert (free.requested_floor_seq, free.held_by_feed_acks, free.findings_removed) == (
        None,
        (),
        1,
    )
    # A record that lost its instance registers no consumer: its acks are void, as the feed's are.
    assert (lost.requested_floor_seq, lost.held_by_feed_acks, lost.findings_removed) == (
        None,
        (),
        1,
    )
    assert [f["held_by"] for f in floors] == [[], []]
    assert floors[0]["journal_instance"] != "" and floors[1]["journal_instance"] == ""


async def _page(handle: AgentHandle, after_seq: int, instance: str = "") -> dict[str, Any]:
    """Send one feed query for findings from ``after_seq`` and return its page.

    A query from a later cursor is bound to the hub and ``instance``; an unbound one starts at 0.
    """
    request_id = uuid.uuid4().hex
    query = {
        "version": 1,
        "consumer": handle.agent.name,
        "hub_id": _HUB if instance else "",
        "journal_instance": instance,
        "after_seq": after_seq,
        "kinds": ["finding"],
        "limit": 2,
    }
    await handle.agent.send_message(
        "history_request", target="System", request_id=request_id, feed_query=query
    )
    frame = await handle.recorder.wait_for(
        lambda m: m.get("type") == "history_snapshot" and m.get("request_id") == request_id,
        timeout=_WAIT,
    )
    page: dict[str, Any] = frame["feed_page"]
    return page


async def _ack(handle: AgentHandle, page: dict[str, Any]) -> dict[str, Any]:
    request_id = uuid.uuid4().hex
    await handle.agent.send_message(
        "feed_ack",
        target="System",
        request_id=request_id,
        consumer=handle.agent.name,
        hub_id=page["hub_id"],
        journal_instance=page["journal_instance"],
        acked_seq=page["next_after"],
    )
    frame = await handle.recorder.wait_for(
        lambda m: m.get("type") == "feed_ack_result" and m.get("request_id") == request_id,
        timeout=_WAIT,
    )
    result: dict[str, Any] = frame["feed_ack"]
    return result


async def test_a_page_reports_the_floor_and_the_gap_a_live_compaction_left(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    journal = EventStore(db)
    findings = [_expired(journal, f"f{index}") for index in range(4)]
    # floors of another instance, and a damaged one, never count
    record_compaction_floor(
        journal,
        journal_instance="0" * 32,
        requested_floor=99,
        applied_floor=99,
        past_feed_acks=True,
        held_by=(),
        passed_consumers=(),
        removed=1,
    )
    journal.append(
        EventKind.COMPACTION_FLOOR,
        {"journal_instance": _instance(journal), "applied_floor": str(findings[3])},
    )
    try:
        async with running_hub(SynapseHub(journal=journal, hub_id=_HUB)) as (_, uri):
            reader = await connect_agent("R/reader", uri)
            try:
                before = await _page(reader, 0)
                ack = await _ack(reader, before)
                # the offline command opens the same file while the hub serves it
                operator = EventStore(db)
                try:
                    result = compact(operator, _AGED, floor_seq=operator.max_seq(), now=1000.0)
                finally:
                    operator.close()
                bound = before["journal_instance"]
                at_ack = await _page(reader, before["next_after"], bound)
                one_below = await _page(reader, findings[0], bound)
                fresh = await _page(reader, 0)
            finally:
                await close_agents(reader)
    finally:
        journal.close()

    assert (before["compaction_floor"], "retention_gap" in before) == (0, False)
    assert (ack["recorded"], ack["acked_seq"]) == (True, findings[1])
    assert (result.floor_seq, result.held_by_feed_acks) == (
        findings[1],
        (("R/reader", findings[1]),),
    )
    assert (at_ack["compaction_floor"], "retention_gap" in at_ack) == (findings[1], False)
    assert [row["seq"] for row in at_ack["rows"]] == findings[2:]
    assert one_below["retention_gap"] == [findings[1], findings[1]]
    assert fresh["retention_gap"] == [1, findings[1]]
    assert [row["seq"] for row in fresh["rows"]] == findings[2:]  # f0 and f1 are gone


def test_the_command_names_the_consumers_that_held_or_were_passed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    store = EventStore(db)
    findings = _registered(store)
    requested = store.max_seq()
    store.close()

    held_rc = cli.main(["compact", str(db), "--all", "--finding-grace-seconds", "0"])
    held = capsys.readouterr().out
    passed_rc = cli.main(
        ["compact", str(db), "--all", "--finding-grace-seconds", "0", "--past-feed-acks"]
    )
    passed = capsys.readouterr().out

    assert (held_rc, passed_rc) == (0, 0)
    assert "removed 0 checkpoint(s), 2 finding(s)" in held
    assert (
        f"the floor was held at seq {findings[1]} (requested {requested}) by feed "
        f"acknowledgements: R/fast at {findings[3]}, R/slow at {findings[1]}"
    ) in held
    assert "compacted past" not in held
    assert "removed 0 checkpoint(s), 2 finding(s)" in passed
    assert (
        f"compacted past feed acknowledgements (journalled): R/fast at {findings[3]}, "
        f"R/slow at {findings[1]}"
    ) in passed
    assert "the floor was held" not in passed


def test_the_command_releases_a_consumer_by_name_and_refuses_an_unknown_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    store = EventStore(db)
    findings = _registered(store)
    store.close()
    report = tmp_path / "report.html"
    grace = ["--all", "--finding-grace-seconds", "0"]

    unknown_rc = cli.main(["compact", str(db), *grace, "--release-feed-consumer", "R/x"])
    unknown = capsys.readouterr().err
    archived_rc = cli.main(
        ["compact", str(db), "--all", "--drop-corrupt", "--archive-report", str(report)]
        + ["--release-feed-consumer", "R/x"]
    )
    archived = capsys.readouterr().err
    released_rc = cli.main(["compact", str(db), *grace, "--release-feed-consumer", "R/slow"])
    released = capsys.readouterr().out
    with pytest.raises(SystemExit) as both:
        cli.main(
            ["compact", str(db), *grace, "--past-feed-acks", "--release-feed-consumer", "R/slow"]
        )
    capsys.readouterr()

    assert (unknown_rc, archived_rc) == (2, 2)
    assert "compact: not a registered feed consumer: R/x" in unknown
    assert "compact: not a registered feed consumer: R/x" in archived
    assert not report.exists()  # the refusal comes before the planned archive
    assert released_rc == 0
    assert "removed 0 checkpoint(s), 4 finding(s)" in released
    assert f"by feed acknowledgements: R/fast at {findings[3]}" in released
    assert f"compacted past feed acknowledgements (journalled): R/slow at {findings[1]}" in released
    assert both.value.code == 2  # the two options are exclusive
