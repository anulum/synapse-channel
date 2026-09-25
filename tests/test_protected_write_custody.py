# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — protected custody versus legacy claim mutations
from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError

import pytest

from synapse_channel.core.path_identity import CanonicalPathIdentity, ClaimScopeIdentity
from synapse_channel.core.protected_write_custody import ProtectedClaimCustody
from synapse_channel.core.state import SynapseState


def _held() -> SynapseState:
    state = SynapseState(default_ttl_seconds=30)
    assert state.claim("author", "task", worktree="tree", paths=["src"], now=1000)[0]
    state.protected_claim_custody["reservation"] = (
        ProtectedClaimCustody.capture(state.claims["task"]),
    )
    return state


def test_custody_is_an_immutable_snapshot_not_a_live_claim_alias() -> None:
    state = _held()
    witness = state.protected_claim_custody["reservation"][0]
    state.claims["task"].version += 1
    state.claims["task"].paths = ("other",)
    assert witness.version == 0
    assert witness.paths == ("src",)
    with pytest.raises(FrozenInstanceError):
        witness.owner = "other"  # type: ignore[misc]


@pytest.mark.parametrize("owner", ["author", "other"])
@pytest.mark.parametrize("now", [1001.0, 2000.0])
def test_custody_blocks_same_task_and_overlapping_scope_after_expiry(
    owner: str, now: float
) -> None:
    state = _held()
    assert not state.claim(owner, "task", worktree="elsewhere", paths=["free"], now=now)[0]
    assert not state.claim(owner, "new-task", worktree="tree", paths=["src/file"], now=now)[0]
    assert state.claim(owner, "unrelated", worktree="tree", paths=["docs"], now=now)[0]
    assert "reservation" in state.protected_claim_custody


@pytest.mark.parametrize(
    "operation", ["release", "force_release", "handoff", "update", "checkpoint"]
)
def test_custody_blocks_legacy_mutators(operation: str) -> None:
    state = _held()
    before = deepcopy(state.claims["task"])
    if operation == "release":
        accepted, reason = state.release("author", "task", now=1001)
    elif operation == "force_release":
        accepted, reason = state.force_release("task", by="operator")
    elif operation == "handoff":
        accepted, reason = state.handoff("author", "task", "other", now=1001)
    elif operation == "update":
        accepted, reason = state.update_task("author", "task", note="changed", now=1001)
    else:
        accepted, reason = state.save_checkpoint("author", "task", "changed", now=1001)
    assert not accepted
    assert "protected custody" in reason
    assert state.claims["task"] == before


def test_custody_preserves_canonical_object_alias_conflicts_after_expiry() -> None:
    state = SynapseState(default_ttl_seconds=30)
    source = ClaimScopeIdentity(
        "/tree",
        True,
        (CanonicalPathIdentity("original", "original", "1:2"),),
        worktree_object_id="1:1",
        filesystem_namespace="host",
    )
    alias = ClaimScopeIdentity(
        "/tree",
        True,
        (CanonicalPathIdentity("alias", "alias", "1:2"),),
        worktree_object_id="1:1",
        filesystem_namespace="host",
    )
    assert state.claim(
        "author", "task", worktree="/tree", paths=["original"], path_identity=source, now=1000
    )[0]
    state.protected_claim_custody["reservation"] = (
        ProtectedClaimCustody.capture(state.claims["task"]),
    )
    assert not state.claim(
        "author", "other", worktree="/tree", paths=["alias"], path_identity=alias, now=2000
    )[0]
    assert "task" not in state.claims


def test_handoff_cannot_bypass_custody_via_a_different_task_id() -> None:
    state = _held()
    witness = state.protected_claim_custody.pop("reservation")
    assert state.claim("author", "overlap", worktree="tree", paths=["src/sub"], now=1001)[0]
    assert state.claim("author", "free", worktree="tree", paths=["docs"], now=1001)[0]
    state.protected_claim_custody["reservation"] = witness
    assert not state.handoff("author", "overlap", "other", now=1002)[0]
    assert state.handoff("author", "free", "other", now=1002)[0]


def test_custody_survives_expiry_snapshot_and_actor_publication() -> None:
    state = _held()
    candidate = deepcopy(state)
    candidate.heartbeat("author", now=2000)
    assert "task" not in candidate.claims
    assert candidate.protected_claim_custody == state.protected_claim_custody
    state.publish_from(candidate)
    state.snapshot(now=3000)
    assert "reservation" in state.protected_claim_custody
    assert not state.claim("author", "new", worktree="tree", paths=["src"], now=3000)[0]
