# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — runner cancellation preserves committed claim publication
"""Exercise public actor shutdown with actual SQLite contention in disposable processes."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Literal, NoReturn

import pytest

from fixtures.state_actor_shutdown import ShutdownClaimScenario
from synapse_channel.core.atomic_operations import (
    OperationDraft,
    OperationRecord,
    idempotency_conflict_response,
)
from synapse_channel.core.idempotency import IdempotencyCache
from synapse_channel.core.journal import record_checkpoint, record_claim, replay
from synapse_channel.core.persistence import EventStore, OperationCommitResult
from synapse_channel.core.state import MINIMUM_TTL_SECONDS, SynapseState
from synapse_channel.core.state_models import TaskClaim
from synapse_channel.core.state_transaction import (
    SerializedStateMutationActor,
    durable_state_transaction,
)


@pytest.mark.parametrize("mode", ["plain", "uncommitted", "atomic"])
def test_runner_shutdown_drains_native_commit_before_publishing_cancellation(
    tmp_path: Path, mode: Literal["plain", "uncommitted", "atomic"]
) -> None:
    """Pending public mutations retain committed state, context and replay response.

    Returning from the child's actual asyncio.run entry point cancels every
    pending task while a second real SQLite connection holds the writer lock.
    A native thread releases that lock; neither cancellation nor a synthetic
    callback result can stand in for the journal's authoritative outcome.
    """
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            str(root / "tests/fixtures/state_actor_shutdown.py"),
            str(tmp_path / "scenario"),
            mode,
        ],
        env={
            **os.environ,
            "PYTHONPATH": os.pathsep.join((str(root / "src"), os.environ.get("PYTHONPATH", ""))),
        },
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    observed = json.loads(result.stdout)
    assert observed["journal_claim_count"] == 1
    assert observed["restored_claim_present"] is True
    assert observed["live_claim_present"] is True
    assert observed["actor_cancelled"] is True
    assert observed["response_remembered"] is (mode == "atomic")
    assert observed["stored_operations"] == int(mode == "atomic")
    assert observed["worker_contexts"] == ["EXAMPLE/author"]
    assert len(observed["publications"]) == 1
    assert observed["publications"][0]["task_id"] == "shutdown-claim"


@pytest.mark.parametrize("enabled", [True, False])
def test_native_append_failure_preserves_enrolled_presence_rollback(
    tmp_path: Path, enabled: bool
) -> None:
    """A real closed-writer error obeys explicit rollback and presence custody."""
    state = SynapseState()
    assert state.claim("EXAMPLE/author", "rollback-claim", note="original")[0]
    original = state.claims["rollback-claim"].as_persisted_dict()
    presence = dict(state.last_seen)
    path = tmp_path / "journal.db"
    store = EventStore(path)
    record_claim(store, state.claims["rollback-claim"])
    store.close()

    with (
        pytest.raises(sqlite3.ProgrammingError),
        durable_state_transaction(
            state,
            "rollback-claim",
            enabled=enabled,
            restore_presence=("EXAMPLE/author", "EXAMPLE/new"),
        ),
    ):
        assert state.claim("EXAMPLE/author", "rollback-claim", note="renewed")[0]
        state.heartbeat("EXAMPLE/new")
        record_claim(store, state.claims["rollback-claim"])

    if enabled:
        assert state.claims["rollback-claim"].as_persisted_dict() == original
        assert state.last_seen == presence
    else:
        assert state.claims["rollback-claim"].note == "renewed"
        assert "EXAMPLE/new" in state.last_seen
    with EventStore(path) as retained:
        assert retained.count() == 1
        assert replay(retained).state.claims["rollback-claim"].as_persisted_dict() == original


@pytest.mark.parametrize("publish_updates", [True, False])
async def test_volatile_actor_publishes_only_after_the_actual_state_transition(
    tmp_path: Path,
    publish_updates: bool,
) -> None:
    """A volatile caller observes the exact live grant once without a journal."""
    state = SynapseState()
    actor = SerializedStateMutationActor()
    path = tmp_path / "observed-grant.json"

    def publish(result: tuple[bool, str]) -> None:
        """Retain the real state visible to the public publication callback."""
        assert result[0] and not path.exists()
        path.write_text(json.dumps(state.claims["volatile-claim"].as_persisted_dict()))

    accepted = await actor.run(
        state,
        lambda candidate: candidate.claim("EXAMPLE/author", "volatile-claim"),
        publish=publish if publish_updates else None,
    )
    assert accepted[0]
    assert path.exists() is publish_updates
    if publish_updates:
        assert json.loads(path.read_text())["epoch"] == state.claims["volatile-claim"].epoch


def test_expired_rollback_retains_only_the_previously_committed_checkpoint(
    tmp_path: Path,
) -> None:
    """A lease that expires during failed I/O cannot retain a provisional token."""
    state = SynapseState()
    assert state.claim("EXAMPLE/author", "expiring-claim", ttl_seconds=MINIMUM_TTL_SECONDS)[0]
    assert state.save_checkpoint("EXAMPLE/author", "expiring-claim", "committed-token")[0]
    original = state.claims["expiring-claim"]
    expires = original.lease_expires_at
    path = tmp_path / "expired-rollback.db"
    store = EventStore(path)
    record_claim(store, original)
    store.close()

    with (
        pytest.raises(sqlite3.ProgrammingError),
        durable_state_transaction(state, "expiring-claim", enabled=True),
    ):
        assert state.save_checkpoint("EXAMPLE/author", "expiring-claim", "uncommitted-token")[0]
        time.sleep(max(0.0, expires - time.time()) + 0.01)
        assert time.time() >= expires
        record_checkpoint(store, original)

    assert "expiring-claim" not in state.claims
    assert state.expired_checkpoints["expiring-claim"] == "committed-token"
    assert original.checkpoint == "committed-token"
    with (
        pytest.raises(sqlite3.ProgrammingError),
        durable_state_transaction(state, "expiring-claim", enabled=True),
    ):
        assert state.claim("EXAMPLE/successor", "expiring-claim")[0]
        record_claim(store, state.claims["expiring-claim"])
    assert "expiring-claim" not in state.claims
    assert state.expired_checkpoints["expiring-claim"] == "committed-token"
    assert state.claim("EXAMPLE/successor", "expiring-claim")[0]
    assert state.claims["expiring-claim"].checkpoint == "committed-token"
    with EventStore(path) as retained:
        assert retained.count() == 1
        assert retained.read_all()[0].payload["checkpoint"] == "committed-token"


async def test_nonkeyed_actor_propagates_worker_cancellation_without_publication() -> None:
    """A cancelled persistence callback cannot publish a candidate or spin forever."""
    actor = SerializedStateMutationActor()
    state = SynapseState()

    def cancelled(_value: object) -> NoReturn:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(
            actor.run(
                state,
                lambda candidate: candidate.claim("seat", "cancelled-task")[:2],
                persist=cancelled,
            ),
            2,
        )
    assert state.claims == {}


def test_native_transaction_retains_success_and_rolls_back_a_failed_new_grant(
    tmp_path: Path,
) -> None:
    """Successful grants survive replay; a later failed grant leaves no live lease."""
    state = SynapseState()
    path = tmp_path / "new-grants.db"
    store = EventStore(path)
    with durable_state_transaction(state, "committed", enabled=True):
        assert state.claim("EXAMPLE/author", "committed", paths=["src/retained.py"])[0]
        record_claim(store, state.claims["committed"])
    committed = state.claims["committed"]
    store.close()
    with (
        pytest.raises(sqlite3.ProgrammingError),
        durable_state_transaction(state, "failed", enabled=True),
    ):
        assert state.claim("EXAMPLE/author", "failed", paths=["src/new.py"])[0]
        record_claim(store, state.claims["failed"])
    assert set(state.claims) == {"committed"}
    assert state.expired_checkpoints == {}
    assert state.claim("EXAMPLE/successor", "failed", paths=["src/new.py"])[0]
    assert state.claims["failed"].epoch == committed.epoch + 1
    with EventStore(path) as retained:
        assert retained.count() == 1
        assert replay(retained).state.claims["committed"].as_persisted_dict() == (
            committed.as_persisted_dict()
        )


@pytest.mark.parametrize("same_request", [True, False])
async def test_keyed_actor_observes_a_real_competing_commit_and_rechecks_its_caller(
    tmp_path: Path, same_request: bool
) -> None:
    """A second SQLite writer wins once; the actor discards its losing candidate."""
    state = SynapseState()
    assert state.claim("EXAMPLE/author", "actor-caller")[0]
    winner = SynapseState()
    assert winner.claim("EXAMPLE/other", "winner")[0]
    cache = IdempotencyCache()
    actor = SerializedStateMutationActor()
    digest = "a" * 64
    path = tmp_path / "competing.db"
    stages: list[str] = []
    with EventStore(path) as store, EventStore(path) as competitor:

        def authorize() -> None:
            stages.append("authorize")
            if state.claims["actor-caller"].owner != "EXAMPLE/author":
                raise PermissionError("caller no longer owns the actor scope")

        def lookup() -> OperationRecord | None:
            stages.append("lookup")
            operation = store.get_operation("competing-key")
            if operation is None:
                return None
            return OperationRecord(
                operation.operation_key, operation.request_digest, operation.response
            )

        def mutate(candidate: SynapseState) -> TaskClaim:
            stages.append("mutate")
            assert candidate.claim("EXAMPLE/author", "loser")[0]
            return candidate.claims["loser"]

        def prepare(claim: TaskClaim) -> OperationDraft:
            stages.append("prepare")
            competitor.commit_operation(
                operation_key="competing-key",
                request_digest=digest if same_request else "b" * 64,
                response={"type": "claim_granted", "task_id": "winner"},
                events=(("claim", winner.claims["winner"].as_persisted_dict()),),
                intent={"task_id": "winner"},
            )
            return OperationDraft(
                response={"type": "claim_granted", "task_id": claim.task_id},
                events=(("claim", claim.as_persisted_dict()),),
                intent={"task_id": claim.task_id},
            )

        def commit(draft: OperationDraft) -> OperationCommitResult:
            stages.append("commit")
            return store.commit_operation(
                operation_key="competing-key",
                request_digest=digest,
                response=draft.response,
                events=draft.events,
                intent=draft.intent,
            )

        def remember(record: OperationRecord) -> None:
            cache.put(record.key, record.response, request_digest=record.request_digest)

        async def apply() -> str:
            execution = await actor.run_atomic(
                state,
                mutate,
                request_digest=digest,
                lookup=lookup,
                prepare=prepare,
                commit=commit,
                remember=remember,
                conflict=lambda _record: idempotency_conflict_response(sender="EXAMPLE/author"),
                authorize=authorize,
            )
            return execution.outcome

        expected = "replayed" if same_request else "conflict"
        assert await apply() == expected
        assert stages == ["authorize", "lookup", "mutate", "prepare", "commit"]
        assert cache.get("competing-key") == {"type": "claim_granted", "task_id": "winner"}
        assert set(state.claims) == {"actor-caller"}
        assert await apply() == expected
        assert stages[-2:] == ["authorize", "lookup"]
        assert len(stages) == 7
        assert store.count() == 2
        assert set(replay(store).state.claims) == {"winner"}
        assert state.release("EXAMPLE/author", "actor-caller")[0]
        assert state.claim("EXAMPLE/replacement", "actor-caller")[0]
        with pytest.raises(PermissionError, match="no longer owns"):
            await apply()
        assert stages[-1] == "authorize" and len(stages) == 8
        assert store.count() == 2


def test_shutdown_scenario_refuses_an_unknown_mode_before_creating_storage(
    tmp_path: Path,
) -> None:
    """The real subprocess entry point refuses an invalid execution profile."""
    root = Path(__file__).resolve().parents[1]
    storage = tmp_path / "uncreated"
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            str(root / "tests/fixtures/state_actor_shutdown.py"),
            str(storage),
            "unknown",
        ],
        env={
            **os.environ,
            "PYTHONPATH": os.pathsep.join((str(root / "src"), os.environ.get("PYTHONPATH", ""))),
        },
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 1 and result.stdout == ""
    assert "unknown shutdown scenario" in result.stderr
    assert not storage.exists()


@pytest.mark.parametrize("mode", ["plain", "uncommitted", "atomic"])
@pytest.mark.parametrize("publish_updates", [True, False])
async def test_native_actor_completion_and_keyed_retry_observe_one_committed_grant(
    tmp_path: Path, mode: Literal["plain", "uncommitted", "atomic"], publish_updates: bool
) -> None:
    """Normal completion and replay agree with SQLite with or without a subscriber."""
    scenario = ShutdownClaimScenario(tmp_path / "normal", mode, publish_updates=publish_updates)
    scenario.blocker.commit()
    scenario.request_context.set("EXAMPLE/author")
    try:
        await scenario.apply()
        granted = scenario.state.claims["shutdown-claim"].as_persisted_dict()
        assert replay(scenario.store).state.claims["shutdown-claim"].as_persisted_dict() == granted
        assert scenario.observed_contexts == ["EXAMPLE/author"]
        assert scenario.publications == ([granted] if publish_updates else [])
        if mode == "atomic":
            await scenario.apply()
            assert scenario.observed_contexts == ["EXAMPLE/author"]
            assert scenario.publications == ([granted] if publish_updates else [])
            assert scenario.store.count() == 2
            remembered = scenario.cache.get("shutdown-key")
            assert remembered is not None and remembered["task_id"] == "shutdown-claim"
        else:
            assert scenario.store.count() == 1
        assert scenario.state.claims["shutdown-claim"].as_persisted_dict() == granted
    finally:
        scenario.close()
