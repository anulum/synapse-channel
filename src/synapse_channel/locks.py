# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — identity-aware lease listing for the `syn locks` command
"""Render active coordination leases for the ergonomic ``syn locks`` command.

``synapse state`` is the broad recovery view; ``syn locks`` is the operator view
for "what is currently held, who holds it, how old is it, how much lease time is
left, and what is the explicit release command?". It queries the same hub state
snapshot, but presents lease rows scoped to the resolved ``syn`` project by
default. No new hub protocol is introduced.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any, Protocol

from synapse_channel.cli_query_transport import AgentFactory, _query_hub
from synapse_channel.client.agent import DEFAULT_HUB_URI, SynapseAgent, default_hub_uri
from synapse_channel.core.numeric_coercion import safe_float
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.scoping import DEFAULT_WORKTREE


class LockIdentity(Protocol):
    """Structural identity contract required by ``syn locks``."""

    @property
    def project(self) -> str:
        """Project used as the default lease filter."""
        ...  # pragma: no cover

    @property
    def identity(self) -> str:
        """Full query identity used to name the short-lived hub connection."""
        ...  # pragma: no cover


@dataclass(frozen=True)
class LeaseRow:
    """One rendered lease row for operator output.

    Attributes
    ----------
    task_id : str
        Claimed task or mutex id.
    owner : str
        Agent identity currently holding the lease.
    status : str
        Claim lifecycle status.
    scope : str
        Human-readable scope, either a named mutex or worktree/path scope.
    age : str
        Elapsed time since the lease was claimed.
    remaining : str
        Time until lease expiry, clamped at zero for stale snapshots.
    release_command : str
        Explicit command that the recorded owner can run to release the lease.
    checkpoint : str
        Resume checkpoint, or ``"-"`` when absent.
    git : str
        Branch context as ``branch->base``, or ``"-"`` when absent.
    """

    task_id: str
    owner: str
    status: str
    scope: str
    age: str
    remaining: str
    release_command: str
    checkpoint: str
    git: str


def _duration(seconds: float) -> str:
    """Return a compact non-negative duration string."""
    total = max(0, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h{minutes:02d}m{secs:02d}s"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"


def _namespace_matches(value: str, namespace: str) -> bool:
    """Return whether ``value`` is exactly ``namespace`` or below it."""
    return (
        value == namespace or value.startswith(f"{namespace}/") or value.startswith(f"{namespace}:")
    )


def _claim_matches(claim: Mapping[str, Any], *, project: str | None, owner: str | None) -> bool:
    """Return whether a snapshot claim belongs in the requested lease view."""
    claim_owner = str(claim.get("owner", ""))
    if owner:
        return _namespace_matches(claim_owner, owner)
    if project is None:
        return True
    task_id = str(claim.get("task_id", ""))
    worktree = str(claim.get("worktree", ""))
    return (
        _namespace_matches(claim_owner, project)
        or _namespace_matches(task_id, project)
        or _namespace_matches(worktree, project)
    )


def _scope(claim: Mapping[str, Any]) -> str:
    """Return a readable scope string for one active claim."""
    task_id = str(claim.get("task_id", ""))
    worktree = str(claim.get("worktree") or DEFAULT_WORKTREE or "default")
    paths = [str(path) for path in claim.get("paths", [])]
    if not paths and worktree == task_id:
        return f"mutex:{task_id}"
    path_label = ", ".join(paths) if paths else "*"
    return f"worktree:{worktree} paths={path_label}"


def _git_label(claim: Mapping[str, Any]) -> str:
    """Return the compact git branch label for one claim."""
    git = claim.get("git")
    if not isinstance(git, Mapping):
        return "-"
    branch = str(git.get("branch") or "")
    base = str(git.get("base") or "")
    return f"{branch}->{base}" if branch or base else "-"


def _release_command(task_id: str, owner: str) -> str:
    """Return the explicit release command for one lease owner."""
    return f"synapse release {task_id} --name {owner}"


def build_rows(
    snapshot: Mapping[str, Any],
    *,
    project: str | None,
    owner: str | None,
    now: float | None = None,
) -> list[LeaseRow]:
    """Build filtered lease rows from a hub state snapshot.

    Parameters
    ----------
    snapshot : Mapping[str, Any]
        State snapshot returned by the hub.
    project : str or None
        Default project namespace filter; ``None`` lists every lease.
    owner : str or None
        Explicit owner namespace filter. When provided it overrides ``project``.
    now : float or None, optional
        Timestamp used for age/remaining calculations. Defaults to ``time.time``.

    Returns
    -------
    list[LeaseRow]
        Operator-ready lease rows.
    """
    ts = time.time() if now is None else float(now)
    rows: list[LeaseRow] = []
    for claim in snapshot.get("active_claims", []):
        if not isinstance(claim, Mapping) or not _claim_matches(
            claim, project=project, owner=owner
        ):
            continue
        task_id = str(claim.get("task_id", ""))
        holder = str(claim.get("owner", ""))
        claimed_at = safe_float(claim.get("claimed_at", ts) or ts, default=ts)
        lease_expires_at = safe_float(claim.get("lease_expires_at", ts) or ts, default=ts)
        rows.append(
            LeaseRow(
                task_id=task_id,
                owner=holder,
                status=str(claim.get("status", "-")),
                scope=_scope(claim),
                age=_duration(ts - claimed_at),
                remaining=_duration(lease_expires_at - ts),
                release_command=_release_command(task_id, holder),
                checkpoint=str(claim.get("checkpoint") or "-"),
                git=_git_label(claim),
            )
        )
    return rows


def render_locks(rows: Sequence[LeaseRow], *, label: str, as_json: bool) -> None:
    """Render lease rows as human-readable text or JSON."""
    if as_json:
        print(json.dumps({"label": label, "leases": [asdict(row) for row in rows]}, indent=2))
        return
    print(f"Active leases in {label} ({len(rows)}):")
    for row in rows:
        print(
            f"  {row.task_id} [{row.status}] owner={row.owner} "
            f"scope={row.scope} age={row.age} remaining={row.remaining} "
            f"checkpoint={row.checkpoint} git={row.git} release={row.release_command}"
        )


async def query_locks(
    identity: LockIdentity,
    *,
    uri: str = DEFAULT_HUB_URI,
    owner: str | None = None,
    all_projects: bool = False,
    as_json: bool = False,
    token: str | None = None,
    agent_factory: AgentFactory = SynapseAgent,
    ready_timeout: float = 5.0,
    now: float | None = None,
) -> int:
    """Query the hub and render active leases for ``identity``.

    Parameters
    ----------
    identity : LockIdentity
        Resolved ``syn`` identity. Its project is the default namespace filter.
    uri : str, optional
        Hub WebSocket URI.
    owner : str or None, optional
        Owner namespace filter. Overrides the project filter.
    all_projects : bool, optional
        When true, list every active lease instead of one project.
    as_json : bool, optional
        Emit machine-readable JSON instead of text.
    token : str or None, optional
        Shared-secret token for a secured hub.
    agent_factory : AgentFactory, optional
        Client factory used for testing.
    ready_timeout : float, optional
        Seconds to await hub readiness.
    now : float or None, optional
        Timestamp override for deterministic rendering.

    Returns
    -------
    int
        ``0`` once the snapshot is rendered, ``1`` when the hub cannot be reached.
    """
    project = None if all_projects else identity.project
    label = owner or ("all projects" if all_projects else identity.project)
    return await _query_hub(
        uri=uri,
        name=f"{identity.identity}-locks",
        token=token,
        agent_factory=agent_factory,
        response_type=MessageType.STATE_SNAPSHOT,
        request=lambda agent: agent.request_state(),
        transform=lambda data: data.get("snapshot", {}),
        render=lambda snapshot: render_locks(
            build_rows(snapshot, project=project, owner=owner, now=now),
            label=label,
            as_json=as_json,
        ),
        ready_timeout=ready_timeout,
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the ``syn locks`` parser."""
    parser = argparse.ArgumentParser(
        prog="syn locks",
        description="List active coordination leases with owner, scope, age, and release command.",
    )
    parser.add_argument("--all", action="store_true", help="List leases across all projects.")
    parser.add_argument(
        "--owner",
        default=None,
        help="Show leases owned by this identity or project namespace.",
    )
    parser.add_argument("--json", action="store_true", help="Emit a machine-readable JSON view.")
    parser.add_argument("--uri", default=default_hub_uri())
    parser.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    parser.add_argument(
        "--ready-timeout", type=float, default=5.0, help="Seconds to await hub readiness."
    )
    return parser


def main(identity: LockIdentity, argv: Sequence[str] | None = None) -> int:
    """Run the synchronous ``syn locks`` entry point."""
    args = build_parser().parse_args(list(argv or ()))
    return asyncio.run(
        query_locks(
            identity,
            uri=args.uri,
            owner=args.owner,
            all_projects=args.all,
            as_json=args.json,
            token=args.token,
            ready_timeout=args.ready_timeout,
        )
    )
