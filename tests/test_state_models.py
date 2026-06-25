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
    GitContext,
    ResourceOffer,
    SynapseState,
    TaskClaim,
)


def test_default_ttl_is_clamped_to_minimum() -> None:
    state = SynapseState(default_ttl_seconds=1.0)
    assert state.default_ttl_seconds == MINIMUM_TTL_SECONDS


def test_taskclaim_as_dict_exposes_all_public_fields() -> None:
    claim = TaskClaim(
        task_id="T",
        owner="A",
        note="n",
        claimed_at=1.0,
        lease_expires_at=2.0,
        status="working",
        data_ref="mem://k",
        version=4,
        checkpoint="step-3",
    )
    assert claim.as_dict() == {
        "task_id": "T",
        "owner": "A",
        "note": "n",
        "claimed_at": 1.0,
        "lease_expires_at": 2.0,
        "status": "working",
        "data_ref": "mem://k",
        "worktree": "",
        "paths": [],
        "epoch": 0,
        "version": 4,
        "checkpoint": "step-3",
        "git": None,
    }


def test_resourceoffer_defaults_are_independent() -> None:
    first = ResourceOffer(agent="A", kind="llm", name="m1")
    second = ResourceOffer(agent="B", kind="llm", name="m2")
    first.meta["x"] = 1
    assert second.meta == {}
    assert first.capacity == 1


def test_gitcontext_as_dict_round_trips() -> None:
    ctx = GitContext(branch="feature/x", base="develop", auto_release_on="commit")
    assert ctx.as_dict() == {
        "branch": "feature/x",
        "base": "develop",
        "auto_release_on": "commit",
    }
    assert GitContext.from_dict(ctx.as_dict()) == ctx


def test_gitcontext_defaults() -> None:
    ctx = GitContext(branch="main")
    assert ctx.base == "main"
    assert ctx.auto_release_on == "merge"


def test_gitcontext_from_dict_normalises_unknown_mode_and_empty_base() -> None:
    ctx = GitContext.from_dict({"branch": "wip", "base": "", "auto_release_on": "nonsense"})
    assert ctx.branch == "wip"
    assert ctx.base == "main"  # empty base falls back
    assert ctx.auto_release_on == "manual"  # unknown trigger falls back


def test_gitcontext_from_dict_uses_field_defaults() -> None:
    ctx = GitContext.from_dict({"branch": "wip"})
    assert ctx == GitContext(branch="wip", base="main", auto_release_on="merge")
