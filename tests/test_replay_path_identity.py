# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — replayed claims carry, and validate, their path identity
"""A replayed claim keeps an aligned path identity and refuses a misaligned one."""

from __future__ import annotations

from typing import Any

import pytest

from synapse_channel.core.journal import EventKind
from synapse_channel.core.path_identity import CanonicalPathIdentity, ClaimScopeIdentity
from synapse_channel.core.persistence import StoredEvent
from synapse_channel.core.replay import build_fork_plan, fork_plan_to_json, reconstruct_claim


def _identity(*paths: str) -> ClaimScopeIdentity:
    return ClaimScopeIdentity(
        worktree_path="/repo",
        worktree_object_id="811:10:7",
        case_sensitive=True,
        paths=tuple(CanonicalPathIdentity(path, path, "811:11:3") for path in paths),
    )


def _claim_event(identity: ClaimScopeIdentity, paths: list[str]) -> StoredEvent:
    payload: dict[str, Any] = {
        "task_id": "T1",
        "owner": "alice",
        "status": "claimed",
        "worktree": "/repo",
        "paths": paths,
        "epoch": 1,
        "version": 0,
        "path_identity": identity.as_dict(),
    }
    return StoredEvent(seq=1, ts=10.0, kind=EventKind.CLAIM, payload=payload)


def test_aligned_identity_is_replayed_and_reported_in_the_fork_plan() -> None:
    """The identity recorded with the claim survives replay and appears in the plan JSON."""
    identity = _identity("src/auth.py")
    events = (_claim_event(identity, ["src/auth.py"]),)
    claim = reconstruct_claim("T1", events)
    assert claim is not None
    assert claim.path_identity == identity
    plan = fork_plan_to_json(build_fork_plan("T1", events, fork_seq=1, overrides={}))
    base = plan["base"]
    assert isinstance(base, dict)
    assert base["path_identity"] == identity.as_dict()


def test_identity_that_does_not_match_the_claimed_paths_is_refused() -> None:
    """A recorded identity naming other paths cannot be replayed as this claim's scope."""
    events = (_claim_event(_identity("src/other.py"), ["src/auth.py"]),)
    with pytest.raises(
        ValueError, match="^replayed claim path identity does not match its display scope$"
    ):
        reconstruct_claim("T1", events)
