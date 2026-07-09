# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — replay debugger reconstruction + fork regressions

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore, StoredEvent
from synapse_channel.core.replay import (
    OVERRIDABLE_FIELDS,
    build_fork_plan,
    fork_plan_to_json,
    infer_task_at_seq,
    load_task_for_seq,
    reconstruct_claim,
    render_markdown,
    run_fork,
)
from synapse_channel.core.state import TaskClaim


def _claim(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "task_id": "T1",
        "owner": "alice",
        "note": "start",
        "claimed_at": 10.0,
        "lease_expires_at": 100.0,
        "status": "claimed",
        "data_ref": "",
        "worktree": "repo",
        "paths": ("src/auth.py",),
        "epoch": 1,
        "version": 0,
        "checkpoint": "",
    }
    base.update(overrides)
    return TaskClaim(**base).as_dict()  # type: ignore[arg-type]


def _events() -> tuple[StoredEvent, ...]:
    """A T1 lifecycle (claim -> update -> checkpoint -> release) beside an unrelated T2."""
    return (
        StoredEvent(seq=1, ts=10.0, kind=EventKind.CLAIM, payload=_claim()),
        StoredEvent(
            seq=2, ts=11.0, kind=EventKind.CLAIM, payload=_claim(task_id="T2", owner="bob")
        ),
        StoredEvent(
            seq=3,
            ts=12.0,
            kind=EventKind.TASK_UPDATE,
            payload=_claim(status="in_progress", note="working", checkpoint="step1", version=1),
        ),
        StoredEvent(
            seq=4,
            ts=13.0,
            kind=EventKind.CHECKPOINT,
            payload=_claim(status="in_progress", note="working", checkpoint="step2", version=2),
        ),
        StoredEvent(seq=5, ts=14.0, kind=EventKind.RELEASE, payload={"task_id": "T1"}),
    )


def _seed(path: Path) -> None:
    store = EventStore(path)
    for event in _events():
        store.append(event.kind, event.payload, ts=event.ts)
    store.close()


def test_reconstruct_as_of_checkpoint_returns_folded_state() -> None:
    claim = reconstruct_claim("T1", _events(), as_of_seq=4)

    assert claim is not None
    assert claim.status == "in_progress"
    assert claim.checkpoint == "step2"
    assert claim.version == 2
    assert claim.source_seq == 4
    assert claim.source_kind == EventKind.CHECKPOINT
    assert claim.paths == ("src/auth.py",)


def test_reconstruct_whole_log_after_release_is_none() -> None:
    assert reconstruct_claim("  T1  ", _events()) is None


def test_reconstruct_stops_at_bound_before_later_snapshot() -> None:
    claim = reconstruct_claim("T1", _events(), as_of_seq=1)

    assert claim is not None
    assert claim.status == "claimed"
    assert claim.source_seq == 1


def test_reconstruct_unknown_task_is_none() -> None:
    assert reconstruct_claim("ghost", _events()) is None


def test_fork_plan_held_applies_overrides_and_reports_divergence() -> None:
    plan = build_fork_plan("T1", _events(), fork_seq=4, overrides={"status": "blocked"})

    assert plan.held is True
    assert plan.base is not None
    assert plan.resume["status"] == "blocked"
    assert plan.resume["checkpoint"] == "step2"
    assert plan.overrides == (("status", "blocked"),)
    assert [event.kind for event in plan.diverged] == [EventKind.RELEASE]
    assert plan.generated_from_seq == 5


def test_fork_plan_after_release_is_not_held() -> None:
    plan = build_fork_plan("T1", _events(), fork_seq=5, overrides={})

    assert plan.held is False
    assert plan.base is None
    assert plan.resume == {}
    assert plan.diverged == ()


def test_fork_plan_rejects_negative_seq() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        build_fork_plan("T1", _events(), fork_seq=-1, overrides={})


def test_fork_plan_rejects_unoverridable_field() -> None:
    with pytest.raises(ValueError, match="cannot override epoch"):
        build_fork_plan("T1", _events(), fork_seq=4, overrides={"epoch": "9"})


def test_overridable_fields_are_scalar_intent_fields() -> None:
    assert OVERRIDABLE_FIELDS == {"owner", "note", "status", "data_ref", "worktree", "checkpoint"}


def test_fork_plan_empty_log_reports_zero_floor() -> None:
    plan = build_fork_plan("T1", (), fork_seq=0, overrides={})

    assert plan.generated_from_seq == 0
    assert plan.held is False


def test_infer_task_at_seq_returns_task() -> None:
    assert infer_task_at_seq(_events(), 4) == "T1"
    assert infer_task_at_seq(_events(), 2) == "T2"


def test_infer_task_at_seq_missing_or_taskless_is_none() -> None:
    assert infer_task_at_seq(_events(), 99) is None
    taskless = (StoredEvent(seq=7, ts=1.0, kind=EventKind.CHAT, payload={"from": "x"}),)
    assert infer_task_at_seq(taskless, 7) is None


def test_fork_plan_to_json_held_round_trips_fields() -> None:
    payload = fork_plan_to_json(
        build_fork_plan("T1", _events(), fork_seq=4, overrides={"note": "n"})
    )

    assert payload["held"] is True
    assert cast("dict[str, Any]", payload["base"])["source_seq"] == 4
    assert payload["overrides"] == [{"field": "note", "value": "n"}]
    assert cast("dict[str, Any]", payload["resume"])["note"] == "n"
    assert cast("list[dict[str, Any]]", payload["diverged"])[0]["kind"] == EventKind.RELEASE


def test_fork_plan_to_json_not_held_has_null_base() -> None:
    payload = fork_plan_to_json(build_fork_plan("T1", _events(), fork_seq=5, overrides={}))

    assert payload["held"] is False
    assert payload["base"] is None


def test_render_markdown_held_lists_manifest_overrides_and_divergence() -> None:
    text = render_markdown(
        build_fork_plan("T1", _events(), fork_seq=4, overrides={"status": "blocked"})
    )

    assert "# Fork: T1 @ seq 4" in text
    assert "- Held at fork point: yes" in text
    assert "- status: blocked" in text
    assert "overrides applied: status=blocked" in text
    assert "kind=release" in text


def test_render_markdown_held_without_overrides_or_divergence() -> None:
    events = _events()[:1]  # only the claim at seq 1
    text = render_markdown(build_fork_plan("T1", events, fork_seq=1, overrides={}))

    assert "overrides applied" not in text
    assert "## Diverged after fork" in text
    assert "- none" in text


def test_render_markdown_not_held_states_nothing_to_fork() -> None:
    text = render_markdown(build_fork_plan("T1", _events(), fork_seq=5, overrides={}))

    assert "- Held at fork point: no" in text
    assert "nothing to fork" in text


def test_render_divergence_omits_empty_actor_status_text() -> None:
    # The release diverged entry carries no actor/status/text fields.
    text = render_markdown(build_fork_plan("T1", _events(), fork_seq=4, overrides={}))
    release_line = next(line for line in text.splitlines() if "kind=release" in line)

    assert "status=" not in release_line
    assert "actor=" not in release_line
    assert "—" not in release_line


def test_diverged_projection_reads_actor_and_text_fields() -> None:
    events = (
        StoredEvent(seq=1, ts=1.0, kind=EventKind.CLAIM, payload=_claim()),
        StoredEvent(
            seq=2,
            ts=2.0,
            kind=EventKind.TASK_UPDATE,
            payload=_claim(status="in_progress", note="working"),
        ),
        StoredEvent(
            seq=3,
            ts=3.0,
            kind=EventKind.CHECKPOINT,
            payload=_claim(note="", data_ref="artefact://x"),
        ),
        StoredEvent(
            seq=4,
            ts=4.0,
            kind=EventKind.LEDGER_PROGRESS,
            payload={"task_id": "T1", "author": "scribe", "text": "noted"},
        ),
    )
    plan = build_fork_plan("T1", events, fork_seq=1, overrides={})
    by_seq = {event.seq: event for event in plan.diverged}

    assert by_seq[2].actor == "alice"  # owner field
    assert by_seq[2].text == "working"  # note field
    assert by_seq[3].text == "artefact://x"  # data_ref fallthrough
    assert by_seq[4].actor == "scribe"  # author fallthrough
    assert by_seq[4].text == "noted"  # text field first


def test_run_fork_loads_store(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    _seed(db)

    plan = run_fork(db, "T1", fork_seq=4, overrides={"owner": "carol"})

    assert plan.held is True
    assert plan.resume["owner"] == "carol"


def test_run_fork_missing_store_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="missing event store"):
        run_fork(tmp_path / "absent.db", "T1", fork_seq=1, overrides={})


def test_load_task_for_seq_reads_store(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    _seed(db)

    assert load_task_for_seq(db, 4) == "T1"
    assert load_task_for_seq(db, 999) is None


def test_reconstruct_claim_survives_hostile_epoch_and_version() -> None:
    """Poisoned epoch/version payload fields read as zero instead of crashing the fold."""
    payload = _claim()
    payload["epoch"] = {"bad": 1}
    payload["version"] = float("inf")
    events = (StoredEvent(seq=1, ts=10.0, kind=EventKind.CLAIM, payload=payload),)

    claim = reconstruct_claim("T1", events)

    assert claim is not None
    assert claim.epoch == 0
    assert claim.version == 0
