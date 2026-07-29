# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — bounded exhaustive coordination-state exploration
"""Enumerate bounded claim/fencing traces against the real coordination state."""

from __future__ import annotations

import argparse
import itertools
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, TypedDict

from synapse_channel.core.scoping import scopes_conflict
from synapse_channel.core.state import MAXIMUM_TTL_SECONDS, SynapseState
from synapse_channel.core.state_models import TaskClaim

ActionName = Literal[
    "claim-a-t1-root",
    "claim-a-t1-src",
    "claim-b-t1-src",
    "claim-b-t2-src-child",
    "claim-b-t2-tests",
    "claim-b-t2-side-src",
    "renew-t1",
    "handoff-t1",
    "release-t1",
    "stale-release-t1",
    "update-t1",
    "stale-update-t1",
    "expire",
]

ACTIONS: tuple[ActionName, ...] = (
    "claim-a-t1-root",
    "claim-a-t1-src",
    "claim-b-t1-src",
    "claim-b-t2-src-child",
    "claim-b-t2-tests",
    "claim-b-t2-side-src",
    "renew-t1",
    "handoff-t1",
    "release-t1",
    "stale-release-t1",
    "update-t1",
    "stale-update-t1",
    "expire",
)
CHECKED_INVARIANTS = (
    "INV-ME-1",
    "INV-ME-2",
    "INV-ME-3",
    "INV-ME-4",
    "INV-EF-1",
    "INV-EF-2",
    "INV-EF-3",
    "INV-LL-1",
    "INV-LL-2",
    "INV-LL-4",
    "INV-CR-2",
)
DEFAULT_DEPTH = 4
MAX_DEPTH = 4
START_TIME = 1_000.0


class ExplorationReport(TypedDict):
    """Stable summary of one complete bounded exploration."""

    action_count: int
    action_steps: int
    checked_invariants: list[str]
    depth: int
    traces: int
    unique_states: int


class ModelViolation(AssertionError):
    """A real state transition violated a checked coordination invariant."""


@dataclass(frozen=True)
class ModelRun:
    """The real state and logical time after one trace."""

    state: SynapseState
    now: float


def _require(condition: bool, invariant: str, detail: str) -> None:
    if not condition:
        raise ModelViolation(f"{invariant}: {detail}")


def _live_claims(state: SynapseState, now: float) -> list[TaskClaim]:
    state.snapshot(now=now)
    return list(state.claims.values())


def _assert_invariants(state: SynapseState, now: float) -> None:
    claims = _live_claims(state, now)
    for task_id, claim in state.claims.items():
        _require(claim.task_id == task_id, "INV-ME-1", "claim key differs from task id")
        _require(claim.lease_expires_at > now, "INV-LL-1", "live lease is not future")
        _require(1 <= claim.epoch <= state._epoch_seq, "INV-EF-1", "epoch is unissued")
        _require(claim.version >= 0, "INV-EF-3", "version is negative")
    for index, first in enumerate(claims):
        for second in claims[index + 1 :]:
            if first.owner == second.owner:
                continue
            conflict = scopes_conflict(
                first.worktree,
                first.paths,
                second.worktree,
                second.paths,
            )
            _require(not conflict, "INV-ME-2/3/4", "different owners hold overlapping scopes")


def _authority_fingerprint(state: SynapseState, now: float) -> tuple[object, ...]:
    _assert_invariants(state, now)
    claims = tuple(
        (
            task_id,
            claim.owner,
            claim.worktree,
            claim.paths,
            claim.epoch,
            claim.version,
            claim.status,
            claim.lease_expires_at,
        )
        for task_id, claim in sorted(state.claims.items())
    )
    return (now, state._epoch_seq, claims)


def _assert_epoch_advanced(state: SynapseState, before: int, ok: bool) -> None:
    if ok:
        _require(state._epoch_seq > before, "INV-EF-1", "successful lease mutation reused epoch")
    else:
        _require(state._epoch_seq == before, "INV-EF-1", "refused mutation consumed epoch")


def _claim(
    state: SynapseState,
    now: float,
    *,
    agent: str,
    task: str,
    worktree: str,
    paths: tuple[str, ...],
    required_invariant: str | None = None,
) -> None:
    before = state._epoch_seq
    ok, _ = state.claim(agent, task, now=now, worktree=worktree, paths=paths)
    _assert_epoch_advanced(state, before, ok)
    if required_invariant is not None:
        _require(ok, required_invariant, "non-conflicting scope was refused")


def _apply_action(state: SynapseState, now: float, action: ActionName) -> float:
    if action == "claim-a-t1-root":
        _claim(state, now, agent="A", task="T1", worktree="main", paths=())
    elif action == "claim-a-t1-src":
        _claim(state, now, agent="A", task="T1", worktree="main", paths=("src",))
    elif action == "claim-b-t1-src":
        _claim(state, now, agent="B", task="T1", worktree="main", paths=("src",))
    elif action == "claim-b-t2-src-child":
        _claim(state, now, agent="B", task="T2", worktree="main", paths=("src/a.py",))
    elif action == "claim-b-t2-tests":
        _claim(state, now, agent="B", task="T2", worktree="main", paths=("tests",))
    elif action == "claim-b-t2-side-src":
        _claim(
            state,
            now,
            agent="B",
            task="T2",
            worktree="side",
            paths=("src",),
            required_invariant="INV-ME-3",
        )
    elif action == "renew-t1":
        claim = state.claims.get("T1")
        if claim is not None:
            before = state._epoch_seq
            ok, _ = state.claim(
                claim.owner,
                "T1",
                now=now,
                worktree=claim.worktree,
                paths=claim.paths,
            )
            _require(ok, "INV-LL-4", "live owner renewal was refused")
            _assert_epoch_advanced(state, before, ok)
    elif action == "handoff-t1":
        claim = state.claims.get("T1")
        if claim is not None:
            before = state._epoch_seq
            target = "B" if claim.owner == "A" else "A"
            ok, _ = state.handoff(claim.owner, "T1", target, now=now, epoch=claim.epoch)
            _assert_epoch_advanced(state, before, ok)
            if ok:
                moved = state.claims["T1"]
                _require(moved.owner == target, "INV-CR-2", "handoff did not transfer owner")
                _require(moved.version == 0, "INV-CR-2", "handoff did not reset version")
    elif action == "release-t1":
        claim = state.claims.get("T1")
        if claim is not None:
            ok, _ = state.release(claim.owner, "T1", now=now, epoch=claim.epoch)
            _require(ok and "T1" not in state.claims, "INV-ME-1", "release retained claim")
    elif action == "stale-release-t1":
        claim = state.claims.get("T1")
        if claim is not None:
            before_authority = _authority_fingerprint(state, now)
            ok, _ = state.release(claim.owner, "T1", now=now, epoch=claim.epoch + 1)
            _require(not ok, "INV-EF-2", "mismatched epoch released claim")
            _require(
                _authority_fingerprint(state, now) == before_authority,
                "INV-EF-2",
                "mismatched epoch changed authority",
            )
    elif action == "update-t1":
        claim = state.claims.get("T1")
        if claim is not None:
            before = claim.version
            ok, _ = state.update_task(
                claim.owner,
                "T1",
                note="bounded-model",
                now=now,
                epoch=claim.epoch,
                expected_version=claim.version,
            )
            _require(ok, "INV-EF-3", "current version update was refused")
            _require(state.claims["T1"].version == before + 1, "INV-EF-3", "version did not bump")
    elif action == "stale-update-t1":
        claim = state.claims.get("T1")
        if claim is not None:
            before_authority = _authority_fingerprint(state, now)
            ok, _ = state.update_task(
                claim.owner,
                "T1",
                note="must-not-apply",
                now=now,
                epoch=claim.epoch,
                expected_version=claim.version + 1,
            )
            _require(not ok, "INV-EF-3", "mismatched version updated claim")
            _require(
                _authority_fingerprint(state, now) == before_authority,
                "INV-EF-3",
                "mismatched version changed authority",
            )
    elif action == "expire":
        now += MAXIMUM_TTL_SECONDS + 1.0
        state.snapshot(now=now)
        _require(not state.claims, "INV-LL-2", "far-future sweep retained a live claim")
    else:
        raise AssertionError(f"unhandled action: {action}")
    _assert_invariants(state, now)
    return now


def replay_trace(trace: Sequence[ActionName]) -> ModelRun:
    """Replay one action trace from an empty real state and assert every step."""
    state = SynapseState(default_ttl_seconds=3_600.0)
    now = START_TIME
    _assert_invariants(state, now)
    for action in trace:
        now = _apply_action(state, now, action)
    return ModelRun(state=state, now=now)


def explore(depth: int = DEFAULT_DEPTH) -> ExplorationReport:
    """Enumerate every action sequence up to ``depth`` and return stable counts."""
    if not 0 <= depth <= MAX_DEPTH:
        raise ValueError(f"depth must be between 0 and {MAX_DEPTH}")
    traces = 0
    action_steps = 0
    fingerprints: set[tuple[object, ...]] = set()
    for length in range(depth + 1):
        for trace in itertools.product(ACTIONS, repeat=length):
            try:
                run = replay_trace(trace)
            except ModelViolation as exc:
                rendered = " -> ".join(trace) or "<empty>"
                raise ModelViolation(f"trace {rendered}: {exc}") from exc
            fingerprints.add(_authority_fingerprint(run.state, run.now))
            traces += 1
            action_steps += length
    return {
        "action_count": len(ACTIONS),
        "action_steps": action_steps,
        "checked_invariants": list(CHECKED_INVARIANTS),
        "depth": depth,
        "traces": traces,
        "unique_states": len(fingerprints),
    }


def main(argv: list[str] | None = None) -> int:
    """Run bounded exploration and emit its deterministic JSON summary."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--depth", type=int, default=DEFAULT_DEPTH)
    args = parser.parse_args(argv)
    try:
        report = explore(args.depth)
    except (ModelViolation, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
