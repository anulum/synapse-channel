# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — read-only CLI query output rendering
"""Stdout renderers for read-only hub query snapshots."""

from __future__ import annotations

from typing import Any


def _render_who(roster: list[str], *, project: str | None = None) -> None:
    """Render an online roster, optionally filtered to one project namespace."""
    agents = sorted(roster)
    if project:
        prefix = f"{project}/"
        agents = [agent for agent in agents if agent == project or agent.startswith(prefix)]
    label = f"Online in {project}" if project else "Online"
    print(f"{label} ({len(agents)}):")
    for agent_name in agents:
        print(f"  {agent_name}")


def _render_who_me(roster: list[str], *, name: str) -> None:
    """Render one identity's presence and wake-loop status from a roster snapshot.

    Parameters
    ----------
    roster : list[str]
        Online identities reported by the hub.
    name : str
        Identity being inspected. The query connection must use a different
        connection name so this report does not create the presence it describes.
    """
    agents = set(roster)
    waiter = f"{name}-rx"
    presence = "online" if name in agents else "missing"
    waiter_state = "online" if waiter in agents else "missing"
    print(f"Me: {name}")
    print(f"  presence: {presence}")
    print(f"  waiter: {waiter_state} ({waiter})")
    print("  note: presence is not a wake loop; the waiter is what wakes quiet terminals.")


def _render_state(snapshot: dict[str, Any], *, owner: str | None = None) -> None:
    """Render live claims and checkpoints, optionally filtered to one owner namespace."""
    claims = list(snapshot.get("active_claims", []))
    if owner:
        prefix = f"{owner}/"
        claims = [
            claim
            for claim in claims
            if claim.get("owner") == owner or str(claim.get("owner", "")).startswith(prefix)
        ]
    print(f"Active claims ({len(claims)}):")
    for claim in claims:
        paths = ", ".join(claim.get("paths", [])) or "-"
        checkpoint = claim.get("checkpoint") or "-"
        git = claim.get("git")
        git_suffix = f" git={git['branch']}->{git['base']}" if git else ""
        print(
            f"  {claim.get('task_id')} [{claim.get('status')}] "
            f"owner={claim.get('owner')} paths={paths} checkpoint={checkpoint}{git_suffix}"
        )


def _print_board(board: dict[str, Any]) -> None:
    """Render a blackboard snapshot as readable lines on stdout."""
    tasks = board.get("tasks", [])
    ready = board.get("ready", [])
    progress = board.get("progress", [])
    print(f"Tasks ({len(tasks)}):")
    for task in tasks:
        deps = ", ".join(task.get("depends_on", []))
        suffix = f"  (deps: {deps})" if deps else ""
        print(f"  [{task.get('status')}] {task.get('task_id')} — {task.get('title')}{suffix}")
    print(f"Ready: {', '.join(ready) if ready else '(none)'}")
    if progress:
        print("Recent progress:")
        for note in progress[-10:]:
            task_id = note.get("task_id") or "-"
            print(f"  {note.get('author')} [{note.get('kind')}] {task_id}: {note.get('text')}")


def _print_manifest(manifest: list[dict[str, Any]]) -> None:
    """Render a capability manifest as readable lines on stdout."""
    print(f"Agents ({len(manifest)}):")
    for card in manifest:
        classes = ", ".join(card.get("task_classes", [])) or "none"
        model = card.get("model") or "-"
        description = card.get("description", "")
        print(f"  {card.get('agent')} [{classes}] model={model}: {description}")
