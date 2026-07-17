# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for bounded retention of the durable event log

from __future__ import annotations

from pathlib import Path

import pytest

from synapse_channel.core.compaction import CompactionResult, RetentionPolicy, compact
from synapse_channel.core.journal import EventKind, record_checkpoint, replay
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.state import TaskClaim


def _store(tmp_path: Path) -> EventStore:
    return EventStore(tmp_path / "events.db")


def _checkpoint(store: EventStore, task_id: str, *, ts: float) -> None:
    store.append(
        EventKind.CHECKPOINT,
        {"task_id": task_id, "checkpoint": f"cursor@{ts}"},
        ts=ts,
        durable=True,
    )


def _finding(store: EventStore, statement: str, *, valid_to: float | None, ts: float) -> None:
    store.append(
        EventKind.FINDING,
        {"statement": statement, "validity": {"valid_from": 0.0, "valid_to": valid_to}},
        ts=ts,
        durable=True,
    )


def _claim(task_id: str, *, checkpoint: str, epoch: int, claimed_at: float) -> TaskClaim:
    return TaskClaim(
        task_id=task_id,
        owner="A",
        note="n",
        claimed_at=claimed_at,
        lease_expires_at=claimed_at + 10_000.0,
        status="claimed",
        data_ref="",
        worktree="wt",
        paths=("src",),
        epoch=epoch,
        checkpoint=checkpoint,
    )


# -- RetentionPolicy ----------------------------------------------------------


def test_policy_is_noop_when_no_knob_is_set() -> None:
    assert RetentionPolicy().is_noop is True


def test_policy_is_not_noop_with_a_checkpoint_knob() -> None:
    assert RetentionPolicy(max_checkpoints_per_task=3).is_noop is False


def test_policy_is_not_noop_with_a_finding_knob() -> None:
    assert RetentionPolicy(finding_grace_seconds=0.0).is_noop is False


def test_policy_is_not_noop_with_explicit_corrupt_row_removal() -> None:
    assert RetentionPolicy(drop_corrupt_rows=True).is_noop is False


def test_policy_rejects_zero_checkpoints() -> None:
    # Keeping zero would drop the newest snapshot — a claim's only survivor.
    with pytest.raises(ValueError, match="at least 1"):
        RetentionPolicy(max_checkpoints_per_task=0)


def test_policy_rejects_negative_checkpoints() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        RetentionPolicy(max_checkpoints_per_task=-2)


def test_policy_rejects_negative_grace() -> None:
    with pytest.raises(ValueError, match="not be negative"):
        RetentionPolicy(finding_grace_seconds=-1.0)


def test_policy_accepts_the_minimum_boundaries() -> None:
    policy = RetentionPolicy(max_checkpoints_per_task=1, finding_grace_seconds=0.0)
    assert policy.is_noop is False


# -- CompactionResult ---------------------------------------------------------


def test_result_total_removed_is_the_sum() -> None:
    result = CompactionResult(
        checkpoints_removed=2,
        findings_removed=3,
        floor_seq=9,
        corrupt_rows_removed=4,
    )
    assert result.total_removed == 9


# -- compact: no-op -----------------------------------------------------------


def test_noop_policy_deletes_nothing(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _checkpoint(store, "T1", ts=1.0)
    _finding(store, "a", valid_to=1.0, ts=2.0)
    result = compact(store, RetentionPolicy(), floor_seq=store.max_seq())
    assert result.total_removed == 0
    assert store.count() == 2
    store.close()


def test_corrupt_rows_require_explicit_policy_and_honour_floor(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = store.append(EventKind.CLAIM, {"task_id": "T1"})
    second = store.append(EventKind.CLAIM, {"task_id": "T2"})
    store._conn.execute("UPDATE events SET payload = 'bad' WHERE seq IN (?, ?)", (first, second))
    store._conn.commit()

    unchanged = compact(store, RetentionPolicy(max_checkpoints_per_task=1), floor_seq=second)
    removed = compact(store, RetentionPolicy(drop_corrupt_rows=True), floor_seq=first)

    assert unchanged.corrupt_rows_removed == 0
    assert [row.seq for row in store.corrupt_rows()] == [second]
    assert removed.corrupt_rows_removed == 1
    store.close()


def test_explicit_corrupt_row_removal_restores_clean_reopen(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    store = EventStore(db)
    keep = store.append(EventKind.CHAT, {"payload": "keep"})
    corrupt = store.append(EventKind.CLAIM, {"task_id": "T1"})
    store._conn.execute("UPDATE events SET payload = 'not-json' WHERE seq = ?", (corrupt,))
    store._conn.commit()

    result = compact(
        store,
        RetentionPolicy(drop_corrupt_rows=True),
        floor_seq=store.max_seq(),
    )
    store.close()
    reopened = EventStore(db)

    assert result.corrupt_rows_removed == 1
    assert reopened.corrupt_rows() == ()
    assert [event.seq for event in reopened.read_all()] == [keep]
    reopened.close()


def test_dry_run_reports_exact_corrupt_count_without_deleting(tmp_path: Path) -> None:
    store = _store(tmp_path)
    seq = store.append(EventKind.CLAIM, {"task_id": "T1"})
    store._conn.execute("UPDATE events SET payload = 'bad' WHERE seq = ?", (seq,))
    store._conn.commit()

    result = compact(
        store,
        RetentionPolicy(drop_corrupt_rows=True),
        floor_seq=seq,
        dry_run=True,
    )

    assert result.corrupt_rows_removed == 1
    assert store.count() == 1
    assert store.corrupt_rows()[0].seq == seq
    store.close()


# -- compact: checkpoint retention --------------------------------------------


def test_keeps_only_the_latest_checkpoints_per_task(tmp_path: Path) -> None:
    store = _store(tmp_path)
    for ts in (1.0, 2.0, 3.0, 4.0):
        _checkpoint(store, "T1", ts=ts)
    result = compact(store, RetentionPolicy(max_checkpoints_per_task=2), floor_seq=store.max_seq())
    surviving = sorted(e.payload["checkpoint"] for e in store.read_all())
    store.close()
    assert result.checkpoints_removed == 2
    assert surviving == ["cursor@3.0", "cursor@4.0"]  # the two newest


def test_checkpoint_retention_is_independent_per_task(tmp_path: Path) -> None:
    store = _store(tmp_path)
    for ts in (1.0, 2.0, 3.0):
        _checkpoint(store, "T1", ts=ts)
    for ts in (4.0, 5.0, 6.0):
        _checkpoint(store, "T2", ts=ts)
    result = compact(store, RetentionPolicy(max_checkpoints_per_task=1), floor_seq=store.max_seq())
    survivors = {e.payload["task_id"]: e.payload["checkpoint"] for e in store.read_all()}
    store.close()
    assert result.checkpoints_removed == 4  # two oldest per task
    assert survivors == {"T1": "cursor@3.0", "T2": "cursor@6.0"}


def test_fewer_checkpoints_than_the_bound_removes_none(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _checkpoint(store, "T1", ts=1.0)
    _checkpoint(store, "T1", ts=2.0)
    result = compact(store, RetentionPolicy(max_checkpoints_per_task=5), floor_seq=store.max_seq())
    store.close()
    assert result.checkpoints_removed == 0


def test_checkpoints_above_the_floor_are_never_touched(tmp_path: Path) -> None:
    store = _store(tmp_path)
    for ts in (1.0, 2.0, 3.0):
        _checkpoint(store, "T1", ts=ts)
    events = store.read_all()
    floor = events[1].seq  # only the first two checkpoints are at or below the floor
    result = compact(store, RetentionPolicy(max_checkpoints_per_task=1), floor_seq=floor)
    remaining = sorted(e.seq for e in store.read_all())
    store.close()
    # The oldest below-floor checkpoint goes; the latest below-floor one and the
    # untouched above-floor one both survive.
    assert result.checkpoints_removed == 1
    assert remaining == [events[1].seq, events[2].seq]


def test_replay_reconstructs_a_claim_from_the_surviving_checkpoint(tmp_path: Path) -> None:
    # The correctness guarantee: keeping the latest checkpoint per task leaves
    # coordination replay reconstructing the claim exactly as before compaction.
    store = _store(tmp_path)
    record_checkpoint(store, _claim("T1", checkpoint="cursor=1", epoch=1, claimed_at=1000.0))
    record_checkpoint(store, _claim("T1", checkpoint="cursor=2", epoch=2, claimed_at=1001.0))
    record_checkpoint(store, _claim("T1", checkpoint="cursor=3", epoch=3, claimed_at=1002.0))
    result = compact(store, RetentionPolicy(max_checkpoints_per_task=1), floor_seq=store.max_seq())
    replayed = replay(store, now=2000.0)
    store.close()
    assert result.checkpoints_removed == 2
    claim = replayed.state.claims["T1"]
    assert claim.checkpoint == "cursor=3"  # the latest snapshot survived
    assert claim.epoch == 3


# -- compact: finding age-out -------------------------------------------------


def test_ages_out_a_finding_whose_window_closed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _finding(store, "expired", valid_to=10.0, ts=1.0)
    result = compact(
        store, RetentionPolicy(finding_grace_seconds=0.0), floor_seq=store.max_seq(), now=1000.0
    )
    store.close()
    assert result.findings_removed == 1


def test_keeps_a_finding_with_an_open_window(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _finding(store, "still-true", valid_to=None, ts=1.0)
    result = compact(
        store, RetentionPolicy(finding_grace_seconds=0.0), floor_seq=store.max_seq(), now=1e9
    )
    store.close()
    assert result.findings_removed == 0


def test_finding_without_a_validity_block_is_never_aged_out(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.append(EventKind.FINDING, {"statement": "no-window"}, ts=1.0, durable=True)
    result = compact(
        store, RetentionPolicy(finding_grace_seconds=0.0), floor_seq=store.max_seq(), now=1e9
    )
    store.close()
    assert result.findings_removed == 0


def test_grace_period_protects_a_recently_closed_finding(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _finding(store, "just-closed", valid_to=100.0, ts=1.0)
    # Closed at t=100, now t=120 → only 20s ago, inside the 50s grace → kept.
    result = compact(
        store, RetentionPolicy(finding_grace_seconds=50.0), floor_seq=store.max_seq(), now=120.0
    )
    store.close()
    assert result.findings_removed == 0


def test_grace_boundary_drops_a_finding_closed_exactly_at_the_cutoff(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _finding(store, "at-cutoff", valid_to=100.0, ts=1.0)
    # cutoff = now - grace = 100; valid_to == cutoff is removed (the bound is inclusive).
    result = compact(
        store, RetentionPolicy(finding_grace_seconds=20.0), floor_seq=store.max_seq(), now=120.0
    )
    store.close()
    assert result.findings_removed == 1


def test_finding_age_out_respects_the_floor(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _finding(store, "expired-below", valid_to=10.0, ts=1.0)
    _finding(store, "expired-above", valid_to=10.0, ts=2.0)
    floor = store.read_all()[0].seq  # only the first finding is at or below the floor
    result = compact(store, RetentionPolicy(finding_grace_seconds=0.0), floor_seq=floor, now=1000.0)
    survivors = [e.payload["statement"] for e in store.read_all()]
    store.close()
    assert result.findings_removed == 1
    assert survivors == ["expired-above"]  # the unconsumed tail is protected


def test_compact_uses_the_system_clock_when_now_is_none(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _finding(store, "long-expired", valid_to=10.0, ts=1.0)  # closed in 1970
    result = compact(store, RetentionPolicy(finding_grace_seconds=0.0), floor_seq=store.max_seq())
    store.close()
    assert result.findings_removed == 1  # real wall-clock is far past valid_to


# -- compact: both knobs + reporting ------------------------------------------


def test_applies_both_knobs_in_one_sweep(tmp_path: Path) -> None:
    store = _store(tmp_path)
    for ts in (1.0, 2.0, 3.0):
        _checkpoint(store, "T1", ts=ts)
    _finding(store, "expired", valid_to=10.0, ts=4.0)
    _finding(store, "open", valid_to=None, ts=5.0)
    result = compact(
        store,
        RetentionPolicy(max_checkpoints_per_task=1, finding_grace_seconds=0.0),
        floor_seq=store.max_seq(),
        now=1000.0,
    )
    store.close()
    assert result.checkpoints_removed == 2
    assert result.findings_removed == 1
    assert result.total_removed == 3


def test_result_reports_the_honoured_floor(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _checkpoint(store, "T1", ts=1.0)
    result = compact(store, RetentionPolicy(max_checkpoints_per_task=1), floor_seq=7)
    store.close()
    assert result.floor_seq == 7
