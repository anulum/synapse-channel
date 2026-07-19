# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — durable state/journal transaction atomicity tests
"""Pin exact rollback when a durable claim-family append fails."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.core.handlers import leasing
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind, record_claim
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.state_models import TaskClaim


class _PostCommitResetFailure:
    """Connection proxy that raises only while restoring NORMAL after commit."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def execute(self, sql: str, *args: Any) -> Any:
        if sql == "PRAGMA synchronous=NORMAL":
            raise sqlite3.OperationalError("post-commit reset failed")
        return self._connection.execute(sql, *args)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)


def _journalled_hub(path: Path) -> tuple[SynapseHub, EventStore]:
    """Return a hub whose claim log uses the supplied temporary path."""
    store = EventStore(path)
    return SynapseHub(journal=store, anti_rollback_checkpoint=False), store


def _fail_record_claim(_store: EventStore, _claim: TaskClaim) -> None:
    """Model a durable append failure before any in-memory grant may occur."""
    raise OSError("claim journal unavailable")


def _fail_record_release(_store: EventStore, _task_id: str) -> None:
    """Model a durable release append failure."""
    raise OSError("release journal unavailable")


def test_new_claim_journal_failure_leaves_no_grant_wait_or_checkpoint_side_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed durable append cannot create a lease or consume resume state."""
    hub, store = _journalled_hub(tmp_path / "events.db")
    hub._waits["A"] = {"T1", "T2"}
    hub.state.expired_checkpoints["T1"] = "resume-token"
    epoch_before = hub.state._epoch_seq
    monkeypatch.setattr(leasing, "record_claim", _fail_record_claim)

    with pytest.raises(OSError, match="journal unavailable"):
        leasing.apply_claim(hub, "A", {"task_id": "T1", "paths": ["src/a.py"]})

    assert "T1" not in hub.state.claims
    assert hub.state._epoch_seq == epoch_before
    assert hub.state.expired_checkpoints["T1"] == "resume-token"
    assert hub._waits["A"] == {"T1", "T2"}
    granted, _message = hub.state.claim("B", "T1", paths=["src/a.py"])
    assert granted
    assert not [event for event in store.read_all() if event.kind == EventKind.CLAIM]
    store.close()


def test_renewal_journal_failure_preserves_the_live_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed renewal append leaves the prior epoch and fields authoritative."""
    hub, store = _journalled_hub(tmp_path / "events.db")
    granted, _message = hub.state.claim(
        "A", "T1", note="original", ttl_seconds=120.0, paths=["src/a.py"]
    )
    assert granted
    original = hub.state.claims["T1"]
    original_snapshot = original.as_persisted_dict()
    hub._waits["A"] = {"T2"}
    monkeypatch.setattr(leasing, "record_claim", _fail_record_claim)

    with pytest.raises(OSError, match="journal unavailable"):
        leasing.apply_claim(
            hub,
            "A",
            {
                "task_id": "T1",
                "note": "must-not-land",
                "ttl_seconds": 600,
                "paths": ["src/a.py"],
            },
        )

    assert hub.state.claims["T1"] is original
    assert hub.state.claims["T1"].as_persisted_dict() == original_snapshot
    assert hub._waits["A"] == {"T2"}
    assert not [event for event in store.read_all() if event.kind == EventKind.CLAIM]
    store.close()


def test_claim_journal_failure_keeps_independent_heartbeat_housekeeping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rollback preserves liveness housekeeping while undoing only the failed claim."""
    hub, store = _journalled_hub(tmp_path / "events.db")
    assert hub.state.claim("stale", "OLD", ttl_seconds=30.0, now=0.0)[0]
    assert hub.state.offer_resource("stale", kind="worker", name="local", now=0.0) is not None
    monkeypatch.setattr("synapse_channel.core.state.time.time", lambda: 400.0)
    monkeypatch.setattr(leasing, "record_claim", _fail_record_claim)

    with pytest.raises(OSError, match="journal unavailable"):
        leasing.apply_claim(hub, "A", {"task_id": "T1", "paths": ["src/a.py"]})

    assert "OLD" not in hub.state.claims
    assert "stale:worker:local" not in hub.state.resources
    assert hub.state.last_seen["A"] == 400.0
    assert "T1" not in hub.state.claims
    store.close()


def test_failed_takeover_does_not_resurrect_the_expired_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rollback keeps target expiry and its retained checkpoint authoritative."""
    hub, store = _journalled_hub(tmp_path / "events.db")
    assert hub.state.claim("OLD", "T1", ttl_seconds=30.0, now=0.0)[0]
    hub.state.claims["T1"].checkpoint = "resume-token"
    monkeypatch.setattr("synapse_channel.core.state.time.time", lambda: 400.0)
    monkeypatch.setattr(leasing, "record_claim", _fail_record_claim)

    with pytest.raises(OSError, match="journal unavailable"):
        leasing.apply_claim(hub, "A", {"task_id": "T1", "paths": ["src/a.py"]})

    assert "T1" not in hub.state.claims
    assert hub.state.expired_checkpoints["T1"] == "resume-token"
    granted, _message = hub.state.claim("B", "T1", paths=["src/a.py"])
    assert granted
    assert hub.state.claims["T1"].checkpoint == "resume-token"
    store.close()


def test_successful_claim_is_persisted_in_the_same_synchronous_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A successful transaction leaves both the live claim and durable row."""
    hub, store = _journalled_hub(tmp_path / "events.db")
    observed_live_state: list[bool] = []

    def record_in_transaction(target: EventStore, claim: TaskClaim) -> None:
        observed_live_state.append("T1" in hub.state.claims)
        record_claim(target, claim)

    monkeypatch.setattr(leasing, "record_claim", record_in_transaction)
    result = leasing.apply_claim(hub, "A", {"task_id": "T1", "paths": ["src/a.py"]})

    assert result.ok
    # The state mutation and append are synchronous, with no event-loop yield;
    # any append exception restores the pre-mutation snapshot.
    assert observed_live_state == [True]
    assert hub.state.claims["T1"] is result.claim
    events = [event for event in store.read_all() if event.kind == EventKind.CLAIM]
    assert len(events) == 1
    assert events[0].payload["task_id"] == "T1"
    store.close()


def test_post_commit_cleanup_failure_keeps_durable_and_live_claim_aligned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cleanup error after COMMIT cannot make the handler undo durable truth."""
    hub, store = _journalled_hub(tmp_path / "events.db")
    monkeypatch.setattr(store, "_conn", _PostCommitResetFailure(store._conn))

    result = leasing.apply_claim(hub, "A", {"task_id": "T1", "paths": ["src/a.py"]})

    assert result.ok
    assert hub.state.claims["T1"] is result.claim
    events = [event for event in store.read_all() if event.kind == EventKind.CLAIM]
    assert len(events) == 1
    assert events[0].payload["task_id"] == "T1"
    store.close()


async def test_task_update_journal_failure_preserves_fields_and_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A task update stays provisional until its complete snapshot is durable."""
    hub, store = _journalled_hub(tmp_path / "events.db")
    assert hub.state.claim("A", "T1", note="before", paths=["src/a.py"])[0]
    before = hub.state.claims["T1"]
    before_snapshot = before.as_persisted_dict()
    monkeypatch.setattr(leasing, "record_task_update", _fail_record_claim)

    with pytest.raises(OSError, match="journal unavailable"):
        await leasing.handle_task_update(
            hub,
            "A",
            {"task_id": "T1", "note": "after", "status": "working"},
            object(),
        )

    assert hub.state.claims["T1"] is before
    assert hub.state.claims["T1"].as_persisted_dict() == before_snapshot
    store.close()


async def test_release_journal_failure_preserves_claim_checkpoint_and_waits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A release cannot free a scope or prune waits before persistence succeeds."""
    hub, store = _journalled_hub(tmp_path / "events.db")
    assert hub.state.claim("A", "T1", paths=["src/a.py"])[0]
    hub.state.claims["T1"].checkpoint = "resume-token"
    hub._waits["B"] = {"T1", "T2"}
    before = hub.state.claims["T1"]
    monkeypatch.setattr(leasing, "record_release", _fail_record_release)

    with pytest.raises(OSError, match="release journal unavailable"):
        await leasing.handle_release(hub, "A", {"task_id": "T1"}, object())

    assert hub.state.claims["T1"] is before
    assert hub.state.claims["T1"].checkpoint == "resume-token"
    assert hub._waits["B"] == {"T1", "T2"}
    assert not hub.state.claim("B", "T1", paths=["src/a.py"])[0]
    store.close()


async def test_handoff_journal_failure_preserves_owner_epoch_and_recipient_wait(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed handoff append cannot move ownership or satisfy a recipient wait."""
    hub, store = _journalled_hub(tmp_path / "events.db")
    assert hub.state.claim("A", "T1", note="before", paths=["src/a.py"])[0]
    before = hub.state.claims["T1"]
    hub.agent_sockets["B"] = object()
    hub._waits["B"] = {"T1", "T2"}
    monkeypatch.setattr(leasing, "record_handoff", _fail_record_claim)

    with pytest.raises(OSError, match="journal unavailable"):
        await leasing.handle_handoff(
            hub,
            "A",
            {"task_id": "T1", "to_agent": "B", "note": "after"},
            object(),
        )

    assert hub.state.claims["T1"] is before
    assert hub.state.claims["T1"].owner == "A"
    assert hub._waits["B"] == {"T1", "T2"}
    assert "B" not in hub.state.last_seen
    store.close()


async def test_checkpoint_journal_failure_preserves_resume_token_and_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A checkpoint and its version bump become live only after persistence."""
    hub, store = _journalled_hub(tmp_path / "events.db")
    assert hub.state.claim("A", "T1", paths=["src/a.py"])[0]
    before = hub.state.claims["T1"]
    before_snapshot = before.as_persisted_dict()
    monkeypatch.setattr(leasing, "record_checkpoint", _fail_record_claim)

    with pytest.raises(OSError, match="journal unavailable"):
        await leasing.handle_checkpoint(
            hub,
            "A",
            {"task_id": "T1", "checkpoint": "must-not-land"},
            object(),
        )

    assert hub.state.claims["T1"] is before
    assert hub.state.claims["T1"].as_persisted_dict() == before_snapshot
    store.close()
