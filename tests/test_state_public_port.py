# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the coordination state's public port: epoch, expiry, publication

from __future__ import annotations

import inspect
from collections.abc import Callable
from copy import deepcopy
from dataclasses import FrozenInstanceError, fields
from pathlib import Path

import pytest

from synapse_channel.core.journal import record_claim, replay
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protected_write_custody import ProtectedClaimCustody
from synapse_channel.core.state import (
    DEFAULT_RESOURCE_TTL_SECONDS,
    StatePublicationParts,
    SynapseState,
    TaskClaim,
)
from synapse_channel.core.state_leases import LeaseIndex
from synapse_channel.core.state_transaction import durable_state_transaction


def test_epoch_seq_is_the_last_issued_lease_epoch() -> None:
    state = SynapseState(default_ttl_seconds=100.0)
    assert state.epoch_seq == 0

    assert state.claim("A", "T1", now=0.0, worktree="wtA")[0] is True
    assert state.claims["T1"].epoch == state.epoch_seq == 1

    assert state.claim("B", "T1", now=1.0, worktree="wtA")[0] is False
    assert state.epoch_seq == 1

    assert state.claim("A", "T2", now=2.0, worktree="wtB")[0] is True
    assert state.claims["T2"].epoch == state.epoch_seq == 2


def test_epoch_seq_is_a_property_without_a_setter() -> None:
    member = inspect.getattr_static(SynapseState, "epoch_seq")

    assert isinstance(member, property)
    assert member.fset is None


def test_restore_epoch_seq_sets_the_mark_the_next_lease_continues_from() -> None:
    state = SynapseState()

    state.restore_epoch_seq(41)
    assert state.epoch_seq == 41

    assert state.claim("A", "T1", worktree="wtA")[0] is True
    assert state.claims["T1"].epoch == state.epoch_seq == 42


def test_restore_epoch_seq_is_a_plain_assignment_that_checks_nothing() -> None:
    state = SynapseState()
    state.restore_epoch_seq(41)

    state.restore_epoch_seq(3)

    assert state.epoch_seq == 3


def test_a_refused_durable_mutation_restores_the_epoch_through_the_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = SynapseState(default_ttl_seconds=100.0)
    assert state.claim("A", "T0", worktree="wt0")[0] is True
    restored: list[int] = []
    restore = state.restore_epoch_seq

    def recording_restore(value: int) -> None:
        restored.append(value)
        restore(value)

    monkeypatch.setattr(state, "restore_epoch_seq", recording_restore)

    with pytest.raises(OSError, match="journal unavailable"):
        with durable_state_transaction(state, "T1", enabled=True):
            assert state.claim("A", "T1", worktree="wt1")[0] is True
            assert state.epoch_seq == 2
            raise OSError("journal unavailable")

    assert restored == [1]
    assert state.epoch_seq == 1
    assert "T1" not in state.claims


def test_publication_parts_are_the_states_own_registries_in_a_frozen_record() -> None:
    state = SynapseState()

    parts = state.publication_parts()

    assert isinstance(parts, StatePublicationParts)
    assert parts.resource_registry.resources is state.resources
    assert parts.lease_index is state._lease_index
    assert [field.name for field in fields(parts)] == ["resource_registry", "lease_index"]
    for field in fields(parts):
        with pytest.raises(FrozenInstanceError):
            setattr(parts, field.name, LeaseIndex())


def test_publish_from_adopts_the_candidates_registries_and_epoch() -> None:
    live = SynapseState(default_ttl_seconds=100.0)
    candidate = deepcopy(live)
    assert candidate.claim("A", "T1", now=0.0, worktree="wtA")[0] is True
    key = candidate.offer_resource("A", kind="llm", name="model", now=0.0)
    parts = candidate.publication_parts()

    live.publish_from(candidate)

    published = live.publication_parts()
    assert published.resource_registry is parts.resource_registry
    assert published.lease_index is parts.lease_index
    assert live.resources is parts.resource_registry.resources
    assert key in live.resources
    assert live.epoch_seq == candidate.epoch_seq == 1

    live.expire_stale(100.0)
    assert "T1" not in live.claims


def test_publish_from_refused_for_its_configuration_changes_nothing() -> None:
    live = SynapseState(default_ttl_seconds=100.0)
    assert live.claim("A", "T1", now=0.0, worktree="wtA")[0] is True
    before = live.publication_parts()
    candidate = SynapseState(default_ttl_seconds=200.0)
    assert candidate.claim("B", "T2", now=0.0, worktree="wtB")[0] is True
    assert candidate.claim("B", "T3", now=0.0, worktree="wtC")[0] is True

    with pytest.raises(ValueError, match="configuration does not match"):
        live.publish_from(candidate)

    after = live.publication_parts()
    assert after.resource_registry is before.resource_registry
    assert after.lease_index is before.lease_index
    assert live.epoch_seq == 1
    assert set(live.claims) == {"T1"}


def test_expire_stale_expires_claims_before_offers(monkeypatch: pytest.MonkeyPatch) -> None:
    state = SynapseState()
    calls: list[tuple[str, float]] = []
    monkeypatch.setattr(state, "_expire_claims", lambda now: calls.append(("claims", now)))
    monkeypatch.setattr(state, "_expire_resources", lambda now: calls.append(("offers", now)))

    state.expire_stale(5.0)

    assert calls == [("claims", 5.0), ("offers", 5.0)]


def test_expire_stale_drops_lapsed_claims_and_offers_past_the_default_ttl() -> None:
    state = SynapseState(default_ttl_seconds=100.0)
    assert state.claim("A", "T1", now=0.0, worktree="wtA")[0] is True
    key = state.offer_resource("A", kind="llm", name="model", now=0.0)

    state.expire_stale(99.0)
    assert "T1" in state.claims

    state.expire_stale(100.0)
    assert "T1" not in state.claims
    assert key in state.resources

    state.expire_stale(DEFAULT_RESOURCE_TTL_SECONDS)
    assert key in state.resources

    state.expire_stale(DEFAULT_RESOURCE_TTL_SECONDS + 1.0)
    assert key not in state.resources


def _state_with_custody_of_src() -> SynapseState:
    state = SynapseState(default_ttl_seconds=100.0)
    assert state.claim("A", "T1", now=0.0, worktree="wtA", paths=("src",))[0] is True
    state.protected_claim_custody["R1"] = (ProtectedClaimCustody.capture(state.claims["T1"]),)
    return state


def test_protected_task_held_names_only_the_witnessed_task() -> None:
    state = _state_with_custody_of_src()

    assert state.protected_task_held("T1") is True
    assert state.protected_task_held("T2") is False
    assert SynapseState().protected_task_held("T1") is False


def test_protected_scope_held_matches_an_overlapping_scope_or_the_same_task() -> None:
    state = _state_with_custody_of_src()

    assert state.protected_scope_held("T2", "wtA", ("src/a.py",), None) is True
    assert state.protected_scope_held("T1", "wtB", ("docs",), None) is True
    assert state.protected_scope_held("T2", "wtA", ("docs",), None) is False
    assert state.protected_scope_held("T2", "wtB", ("src",), None) is False


def test_the_protected_queries_mutate_nothing() -> None:
    state = _state_with_custody_of_src()
    custody = dict(state.protected_claim_custody)
    claims = dict(state.claims)

    state.protected_task_held("T1")
    state.protected_scope_held("T2", "wtA", ("src",), None)

    assert state.protected_claim_custody == custody
    assert state.claims == claims
    assert state.epoch_seq == 1


_HELD_TASK_MUTATIONS: dict[str, Callable[[SynapseState], tuple[bool, str]]] = {
    "renew": lambda state: state.renew_claim("A", "T1", now=1.0, epoch=1, paths=("src",)),
    "update": lambda state: state.update_task("A", "T1", note="x", epoch=1, now=1.0),
    "checkpoint": lambda state: state.save_checkpoint("A", "T1", "token", epoch=1, now=1.0),
    "release": lambda state: state.release("A", "T1", now=1.0),
    "force release": lambda state: state.force_release("T1", by="operator"),
    "handoff": lambda state: state.handoff("A", "T1", "B", epoch=1, now=1.0),
}


@pytest.mark.parametrize("mutation", sorted(_HELD_TASK_MUTATIONS))
def test_every_mutation_of_a_held_task_is_refused_through_the_public_query(mutation: str) -> None:
    state = _state_with_custody_of_src()
    before = deepcopy(state.claims["T1"])

    ok, message = _HELD_TASK_MUTATIONS[mutation](state)

    assert ok is False
    assert message == "Task 'T1' has outstanding protected custody."
    assert state.claims["T1"] == before
    assert state.epoch_seq == 1


@pytest.mark.parametrize("mutation", sorted(_HELD_TASK_MUTATIONS))
def test_every_mutation_of_a_task_without_custody_proceeds(mutation: str) -> None:
    state = SynapseState(default_ttl_seconds=100.0)
    assert state.claim("A", "T1", now=0.0, worktree="wtA", paths=("src",))[0] is True

    ok, message = _HELD_TASK_MUTATIONS[mutation](state)

    assert ok is True, message


def test_a_handoff_into_a_scope_another_task_holds_in_custody_is_refused() -> None:
    state = SynapseState(default_ttl_seconds=100.0)
    assert state.claim("A", "T1", now=0.0, worktree="wtA", paths=("src",))[0] is True
    state.protected_claim_custody["R9"] = (
        ProtectedClaimCustody(
            task_id="T9",
            owner="C",
            epoch=9,
            version=0,
            lease_expires_at=100.0,
            worktree="wtA",
            paths=("src/a.py",),
            path_identity=None,
        ),
    )

    ok, message = state.handoff("A", "T1", "B", epoch=1, now=1.0)

    assert ok is False
    assert message == "Task 'T1' conflicts with outstanding protected custody."
    assert state.claims["T1"].owner == "A"


def test_replay_restores_the_epoch_and_runs_the_one_expiry_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = EventStore(tmp_path / "events.db")
    for epoch, expires in ((1, 1500.0), (2, 5000.0)):
        record_claim(
            store,
            TaskClaim(
                task_id=f"T{epoch}",
                owner="A",
                note="",
                claimed_at=1000.0,
                lease_expires_at=expires,
                worktree=f"wt{epoch}",
                epoch=epoch,
            ),
        )
    restored: list[int] = []
    passes: list[float] = []
    restore = SynapseState.restore_epoch_seq
    expire = SynapseState.expire_stale

    def recording_restore(self: SynapseState, value: int) -> None:
        restored.append(value)
        restore(self, value)

    def recording_expire(self: SynapseState, now: float) -> None:
        passes.append(now)
        expire(self, now)

    monkeypatch.setattr(SynapseState, "restore_epoch_seq", recording_restore)
    monkeypatch.setattr(SynapseState, "expire_stale", recording_expire)

    result = replay(store, now=2000.0)
    store.close()

    assert restored == [2]
    assert passes == [2000.0]
    assert result.state.epoch_seq == 2
    assert set(result.state.claims) == {"T2"}


def test_heartbeat_and_snapshot_run_the_one_expiry_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    state = SynapseState()
    passes: list[float] = []
    monkeypatch.setattr(state, "expire_stale", passes.append)

    state.heartbeat("A", now=7.0)
    view = state.snapshot(now=8.0)

    assert passes == [7.0, 8.0]
    assert state.last_seen == {"A": 7.0}
    assert view["generated_at"] == 8.0
