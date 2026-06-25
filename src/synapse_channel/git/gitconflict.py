# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — client-side merge-conflict prediction for branch-scoped claims
"""Predict merge conflicts from branch-scoped claims, before they happen.

``synapse conflicts`` reads the hub's live claims and flags pairs held on
*different* branches whose declared paths overlap — the agents are about to edit
the same files on branches that will merge into the same base. With
``--check-diff`` it refines the prediction against ``git diff base...branch`` so
only files each branch has actually changed are reported. As everywhere in the
git integration, all git work is client-side: the hub only ever serves its
ordinary state snapshot and never runs git.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from typing import Any

from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.protocol import MessageType
from synapse_channel.git.gitclaim import AgentFactory, GitError, GitRunner, _default_git_runner


@dataclass(frozen=True)
class PredictedConflict:
    """Two claims on different branches whose file scopes overlap.

    Attributes
    ----------
    owner_a, branch_a, base_a : str
        The first claim's owner, branch, and merge base.
    owner_b, branch_b, base_b : str
        The second claim's owner, branch, and merge base.
    paths : tuple[str, ...]
        The overlapping paths; empty means both claims hold the whole worktree.
    """

    owner_a: str
    branch_a: str
    base_a: str
    owner_b: str
    branch_b: str
    base_b: str
    paths: tuple[str, ...]

    def describe(self) -> str:
        """Render the predicted conflict as one human-readable line."""
        where = ", ".join(self.paths) if self.paths else "the whole worktree"
        return (
            f"{self.owner_a}@{self.branch_a} vs {self.owner_b}@{self.branch_b} "
            f"(both -> base): {where}"
        )


def _normalise(paths: Any) -> list[str]:
    """Return claim paths as plain strings with any trailing slash removed.

    A missing or ``None`` scope is treated as the empty list, so a malformed claim
    snapshot can never crash the prediction.
    """
    return [str(p).rstrip("/") for p in (paths or [])]


def _overlap(paths_a: list[str], paths_b: list[str]) -> list[str]:
    """Return the paths shared by two scopes by equality or directory containment.

    A whole-worktree scope (empty paths) overlaps the other scope entirely, so the
    other's paths are returned.
    """
    if not paths_a:
        return sorted(paths_b)
    if not paths_b:
        return sorted(paths_a)
    shared: set[str] = set()
    for a in paths_a:
        for b in paths_b:
            if a == b or a.startswith(b + "/") or b.startswith(a + "/"):
                shared.add(a if len(a) >= len(b) else b)
    return sorted(shared)


def find_conflicts(claims: list[dict[str, Any]]) -> list[PredictedConflict]:
    """Find every pair of git-scoped claims on different branches that overlap.

    Parameters
    ----------
    claims : list[dict[str, Any]]
        Active claims from a state snapshot, each optionally carrying a ``git``
        context and ``paths``.

    Returns
    -------
    list[PredictedConflict]
        One entry per overlapping cross-branch pair.
    """
    git_claims = [c for c in claims if (c.get("git") or {}).get("branch")]
    out: list[PredictedConflict] = []
    for index, first in enumerate(git_claims):
        for second in git_claims[index + 1 :]:
            git_a, git_b = first["git"], second["git"]
            if git_a["branch"] == git_b["branch"]:
                continue
            paths_a = _normalise(first.get("paths", []))
            paths_b = _normalise(second.get("paths", []))
            both_whole = not paths_a and not paths_b
            overlap = _overlap(paths_a, paths_b)
            if not overlap and not both_whole:
                continue
            out.append(
                PredictedConflict(
                    owner_a=str(first.get("owner", "")),
                    branch_a=str(git_a["branch"]),
                    base_a=str(git_a.get("base", "main")),
                    owner_b=str(second.get("owner", "")),
                    branch_b=str(git_b["branch"]),
                    base_b=str(git_b.get("base", "main")),
                    paths=tuple(overlap),
                )
            )
    return out


def branch_diff_files(
    branch: str, base: str, *, runner: GitRunner = _default_git_runner
) -> list[str]:
    """Return the files ``branch`` changed since it diverged from ``base``."""
    out = runner(["diff", "--name-only", f"{base}...{branch}"])
    return [line for line in out.splitlines() if line.strip()]


def _refine_with_diff(
    conflicts: list[PredictedConflict], *, runner: GitRunner
) -> list[PredictedConflict]:
    """Keep only the overlapping paths each branch has actually changed.

    A pair whose declared overlap is not changed on both branches is dropped. A
    pair whose branch diff cannot be computed (the branch is not checked out
    locally), or that holds the whole worktree, is kept unrefined — better to
    over-warn than to miss a real conflict.
    """
    cache: dict[tuple[str, str], set[str] | None] = {}

    def changed(branch: str, base: str) -> set[str] | None:
        key = (base, branch)
        if key not in cache:
            try:
                cache[key] = set(branch_diff_files(branch, base, runner=runner))
            except GitError:
                cache[key] = None
        return cache[key]

    refined: list[PredictedConflict] = []
    for conflict in conflicts:
        changed_a = changed(conflict.branch_a, conflict.base_a)
        changed_b = changed(conflict.branch_b, conflict.base_b)
        if changed_a is None or changed_b is None or not conflict.paths:
            refined.append(conflict)
            continue
        kept = tuple(p for p in conflict.paths if p in changed_a and p in changed_b)
        if kept:
            refined.append(replace(conflict, paths=kept))
    return refined


async def run_conflicts(
    *,
    uri: str,
    name: str,
    token: str | None = None,
    check_diff: bool = False,
    agent_factory: AgentFactory = SynapseAgent,
    runner: GitRunner = _default_git_runner,
    ready_timeout: float = 5.0,
    attempts: int = 40,
    poll_interval: float = 0.05,
) -> int:
    """Predict merge conflicts from the hub's live claims and print them.

    Parameters
    ----------
    uri, name : str
        Hub URI and the requesting agent's identity.
    token : str or None, optional
        Shared-secret token for a secured hub.
    check_diff : bool, optional
        When ``True``, refine the prediction against each branch's ``git diff``.
    agent_factory : AgentFactory, optional
        Factory for the hub client; injectable for testing.
    runner : GitRunner, optional
        The git executor; injectable for testing.
    ready_timeout : float, optional
        Seconds to wait for hub connection readiness.
    attempts : int, optional
        Number of state snapshot polling attempts.
    poll_interval : float, optional
        Seconds to sleep between state snapshot polls.

    Returns
    -------
    int
        ``0`` when no conflict is predicted (safe to proceed), ``2`` when one or
        more are predicted, and ``1`` when the hub is unreachable. The non-zero
        codes let ``synapse conflicts && <merge>`` proceed only on a clean,
        successfully checked result.
    """
    snapshots: list[dict[str, Any]] = []

    async def collect(data: dict[str, Any]) -> None:
        if data.get("type") == MessageType.STATE_SNAPSHOT:
            snapshots.append(data.get("snapshot", {}))

    agent = agent_factory(name, collect, uri=uri, verbose=False, token=token)
    conn_task = asyncio.create_task(agent.connect())
    try:
        if not await agent.wait_until_ready(timeout=ready_timeout):
            print(f"[{name}] Could not reach hub at {uri}.")
            return 1
        await agent.request_state()
        for _ in range(attempts):
            if snapshots:
                break
            await asyncio.sleep(poll_interval)
        claims = (snapshots[-1].get("active_claims") or []) if snapshots else []
        conflicts = find_conflicts(claims)
        if check_diff:
            conflicts = _refine_with_diff(conflicts, runner=runner)
        if not conflicts:
            print("No predicted conflicts.")
            return 0
        print(f"Predicted conflicts ({len(conflicts)}):")
        for conflict in conflicts:
            print(f"  {conflict.describe()}")
        return 2
    finally:
        agent.running = False
        conn_task.cancel()
