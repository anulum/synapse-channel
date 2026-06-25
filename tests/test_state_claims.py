# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — exhaustive tests for the coordination state registry

from __future__ import annotations

from synapse_channel.core.state import (
    MINIMUM_TTL_SECONDS,
    SynapseState,
)


def test_claim_requires_non_empty_task_id() -> None:
    state = SynapseState()
    ok, msg = state.claim("A", "   ")
    assert ok is False
    assert "required" in msg


def test_claim_strips_task_and_note() -> None:
    state = SynapseState(default_ttl_seconds=300)
    ok, _ = state.claim("A", "  TASK-1  ", note="  do work  ", now=1000.0)
    assert ok is True
    claim = state.claims["TASK-1"]
    assert claim.owner == "A"
    assert claim.note == "do work"
    assert claim.lease_expires_at == 1000.0 + 300.0


def test_claim_blocked_by_live_owner() -> None:
    state = SynapseState(default_ttl_seconds=300)
    state.claim("A", "TASK-2", now=1000.0)
    ok, msg = state.claim("B", "TASK-2", now=1010.0)
    assert ok is False
    assert "already claimed by A" in msg


def test_owner_renews_extends_lease() -> None:
    state = SynapseState(default_ttl_seconds=300)
    state.claim("A", "TASK-3", now=1000.0)
    first = state.claims["TASK-3"].lease_expires_at
    ok, _ = state.claim("A", "TASK-3", now=1050.0)
    assert ok is True
    assert state.claims["TASK-3"].lease_expires_at > first


def test_expired_claim_can_be_taken_over() -> None:
    state = SynapseState(default_ttl_seconds=60)
    state.claim("A", "TASK-4", now=1000.0)
    ok, _ = state.claim("B", "TASK-4", now=1070.0)
    assert ok is True
    assert state.claims["TASK-4"].owner == "B"


def test_explicit_ttl_is_clamped_to_minimum() -> None:
    state = SynapseState(default_ttl_seconds=300)
    state.claim("A", "TASK-5", ttl_seconds=1.0, now=1000.0)
    assert state.claims["TASK-5"].lease_expires_at == 1000.0 + MINIMUM_TTL_SECONDS


def test_explicit_ttl_above_minimum_is_honoured() -> None:
    state = SynapseState(default_ttl_seconds=300)
    state.claim("A", "TASK-6", ttl_seconds=120.0, now=1000.0)
    assert state.claims["TASK-6"].lease_expires_at == 1000.0 + 120.0


# --- update_task -------------------------------------------------------------


def test_update_task_unknown_returns_error() -> None:
    state = SynapseState()
    ok, msg = state.update_task("A", "NOPE")
    assert ok is False
    assert "not found" in msg


def test_update_task_rejects_non_owner() -> None:
    state = SynapseState(default_ttl_seconds=300)
    state.claim("A", "TASK-7", now=1000.0)
    ok, msg = state.update_task("B", "TASK-7", status="blocked", now=1010.0)
    assert ok is False
    assert "owned by A" in msg


def test_update_task_sets_fields() -> None:
    state = SynapseState(default_ttl_seconds=300)
    state.claim("A", "TASK-8", now=1000.0)
    ok, _ = state.update_task(
        "A", "TASK-8", status="done", note="  done  ", data_ref="  mem://x  ", now=1010.0
    )
    assert ok is True
    claim = state.claims["TASK-8"]
    assert claim.status == "done"
    assert claim.note == "done"
    assert claim.data_ref == "mem://x"


def test_update_task_ignores_empty_status() -> None:
    state = SynapseState(default_ttl_seconds=300)
    state.claim("A", "TASK-9", now=1000.0)
    state.update_task("A", "TASK-9", status="", now=1010.0)
    assert state.claims["TASK-9"].status == "claimed"


# --- release -----------------------------------------------------------------


def test_release_requires_task_id() -> None:
    state = SynapseState()
    ok, msg = state.release("A", "  ")
    assert ok is False
    assert "required" in msg


def test_release_unclaimed_returns_error() -> None:
    state = SynapseState()
    ok, msg = state.release("A", "GHOST")
    assert ok is False
    assert "not currently claimed" in msg


def test_release_rejects_non_owner() -> None:
    state = SynapseState(default_ttl_seconds=300)
    state.claim("A", "TASK-10", now=1000.0)
    ok, msg = state.release("B", "TASK-10", now=1010.0)
    assert ok is False
    assert "owned by A" in msg


def test_release_roundtrip() -> None:
    state = SynapseState(default_ttl_seconds=300)
    state.claim("A", "TASK-11", now=1000.0)
    ok, msg = state.release("A", "TASK-11", now=1010.0)
    assert ok is True
    assert "released" in msg
    assert "TASK-11" not in state.claims


# --- resources ---------------------------------------------------------------
