# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — read-only CLI query output rendering
"""Stdout renderers for read-only hub query snapshots."""

from __future__ import annotations

import time
from typing import Any

from synapse_channel.core.clock_skew import format_clock_skew
from synapse_channel.core.mailbox_pending import format_pending_line
from synapse_channel.core.wake_capability import WAKE_UNKNOWN, wake_capability_label
from synapse_channel.observed_peers import ObservedPeerSnapshot
from synapse_channel.waiter_identity import split_roster, waiter_name


def _format_reaction_age(seconds: float) -> str:
    """Render an elapsed-since-last-reaction duration coarsely (``~4m``, ``~2h``)."""
    if seconds < 60:
        return f"~{int(seconds)}s"
    if seconds < 3600:
        return f"~{int(seconds // 60)}m"
    return f"~{int(seconds // 3600)}h"


def _liveness_suffix(info: dict[str, Any]) -> str:
    """Return the deaf marker for one agent's liveness record, or empty if proven live.

    A proven-live agent — one with an armed ``-rx`` waiter or a recent reaction — gets
    no marker, so the roster stays quiet and only the "online but deaf" agents stand
    out. A stale agent is flagged with how long it has been silent, or that no reaction
    has ever been seen from it.
    """
    if info.get("proven_live"):
        return ""
    age = info.get("last_reaction_age")
    if age is None:
        return "  (deaf — no reaction seen)"
    return f"  (deaf {_format_reaction_age(float(age))})"


def _wake_capability_suffix(name: str, capabilities: dict[str, str] | None) -> str:
    """Return the wake-capability marker for one roster entry."""
    if not capabilities:
        return ""
    capability = capabilities.get(name, WAKE_UNKNOWN)
    if capability == WAKE_UNKNOWN:
        return ""
    return f"  ({wake_capability_label(capability)})"


def _render_who(
    roster: list[str],
    *,
    project: str | None = None,
    liveness: dict[str, dict[str, Any]] | None = None,
    wake_capabilities: dict[str, str] | None = None,
    mailbox_pending: dict[str, int] | None = None,
    show_mailbox_pending: bool = False,
    observed_peers: tuple[ObservedPeerSnapshot, ...] = (),
) -> None:
    """Render an online roster split into agents and waiter sidecars.

    Every name in the roster holds a live socket, but only the non-``-rx``
    identities are agents someone acts as — a wake-listener sidecar is presence
    plumbing. Counting the two apart keeps the headline honest: a workstation
    with 30 terminals must never read as 200 agents. The optional ``project``
    filter applies to both sections.

    When the hub reports per-agent liveness (only under ``--warn-stale-recipients``),
    an agent that is present but not proven wake-capable is marked ``(deaf …)`` so a
    connected-but-deaf terminal is told apart from a live one, and a trailing
    ``Unarmed`` line names the present agents that have no live ``-rx`` waiter — the
    ones an operator should re-arm before they go quiet. Without that data the roster
    renders exactly as before.
    """
    names = roster
    if project:
        prefix = f"{project}/"
        names = [name for name in names if name == project or name.startswith(prefix)]
    agents, waiters = split_roster(names)
    scope = f" in {project}" if project else ""
    print(f"Online{scope} ({len(agents)} agents · {len(waiters)} waiters):")
    marks = liveness or {}
    for agent_name in agents:
        capability = _wake_capability_suffix(agent_name, wake_capabilities)
        liveness_mark = _liveness_suffix(marks[agent_name]) if agent_name in marks else ""
        print(f"  {agent_name}{capability}{liveness_mark}")
    if waiters:
        print(f"Waiters ({len(waiters)}):")
        for waiter in waiters:
            print(f"  {waiter}{_wake_capability_suffix(waiter, wake_capabilities)}")
    unarmed = [
        agent_name
        for agent_name in agents
        if marks.get(agent_name, {}).get("has_live_waiter") is False
    ]
    if unarmed:
        print(f"Unarmed (present, no live waiter): {', '.join(unarmed)}")
    if show_mailbox_pending:
        _render_mailbox_pending(mailbox_pending, project=project)
    _render_observed_peers(observed_peers, project=project)


def _render_who_me(
    roster: list[str],
    *,
    name: str,
    mailbox_pending: dict[str, int] | None = None,
    show_mailbox_pending: bool = False,
) -> None:
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
    waiter = waiter_name(name)
    presence = "online" if name in agents else "missing"
    waiter_state = "online" if waiter in agents else "missing"
    print(f"Me: {name}")
    print(f"  presence: {presence}")
    print(f"  waiter: {waiter_state} ({waiter})")
    print("  note: presence is not a wake loop; the waiter is what wakes quiet terminals.")
    if show_mailbox_pending:
        if mailbox_pending is None:
            print("  mailbox pending: unavailable (hub has no durable projection)")
        else:
            print(f"  {format_pending_line(name, mailbox_pending.get(name, 0))}")


def _render_mailbox_pending(
    counts: dict[str, int] | None,
    *,
    project: str | None,
) -> None:
    """Render positive pending counts, an empty verdict, or unavailability."""
    if counts is None:
        print("Mailbox pending: unavailable (hub has no durable projection)")
        return
    prefix = f"{project}/" if project else ""
    pending = {
        identity: count
        for identity, count in counts.items()
        if count > 0 and (project is None or identity == project or identity.startswith(prefix))
    }
    if not pending:
        print("Mailbox pending: none")
        return
    print(f"Mailbox pending ({len(pending)} identities):")
    for identity in sorted(pending):
        print(f"  {format_pending_line(identity, pending[identity])}")


def _render_state(
    snapshot: dict[str, Any],
    *,
    owner: str | None = None,
    observed_peers: tuple[ObservedPeerSnapshot, ...] = (),
) -> None:
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
    _render_observed_claims(observed_peers, owner=owner)


def _render_observed_peers(
    peers: tuple[ObservedPeerSnapshot, ...], *, project: str | None = None
) -> None:
    """Render advisory peer rows for ``who``."""
    if not peers:
        return
    print(f"Observed peers ({len(peers)}; advisory, not local authority):")
    for peer in peers:
        if not peer.reachable:
            print(f"  observed@{peer.hub_id} unreachable: {peer.error or 'fetch failed'}")
            continue
        agents = [
            agent
            for agent in peer.observed_agents
            if project is None or agent == project or agent.startswith(f"{project}/")
        ]
        lag = "unknown" if peer.lag is None else str(peer.lag)
        skew = (
            ""
            if peer.clock_skew_seconds is None
            else f" skew={format_clock_skew(peer.clock_skew_seconds)}"
        )
        agent_text = ", ".join(agents) if agents else "no observed claim owners"
        print(f"  observed@{peer.hub_id} online cursor={peer.cursor} lag={lag}{skew}: {agent_text}")


def _render_observed_claims(
    peers: tuple[ObservedPeerSnapshot, ...], *, owner: str | None = None
) -> None:
    """Render advisory observed claims after the local state view."""
    if not peers:
        return
    rows: list[str] = []
    for peer in peers:
        if not peer.reachable:
            rows.append(f"  observed@{peer.hub_id} unreachable: {peer.error or 'fetch failed'}")
            continue
        for observed in peer.state.observed_claims.values():
            claim = dict(observed.claim)
            claim_owner = str(claim.get("owner", ""))
            if owner and claim_owner != owner and not claim_owner.startswith(f"{owner}/"):
                continue
            paths = ", ".join(str(path) for path in claim.get("paths", []) or []) or "-"
            rows.append(
                f"  {observed.task_id} [observed@{peer.hub_id}] "
                f"owner={claim_owner or '-'} paths={paths}"
            )
    print(f"Observed claims ({len(rows)}; advisory, never local grants):")
    for row in rows:
        print(row)


def _render_dead_letters(snapshot: dict[str, Any]) -> None:
    """Render the hub's dead-letter ledger — directed chats that reached nobody.

    The ledger already rides in the state snapshot for the dashboard and the
    cockpit; this brings the same blackhole list to a terminal operator, worst
    first, with the exact one-line remedy the doctor's addressee check emits.
    An empty ledger is stated plainly, not left as silence a reader must guess.
    """
    entries = list(snapshot.get("dead_letters", []))
    if not entries:
        print("Dead letters: none — every recent directed message reached a live connection.")
        return
    print(f"Dead letters ({len(entries)}: directed messages the hub delivered to nobody live):")
    for entry in entries:
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(float(entry.get("last_ts", 0.0))))
        print(
            f"  {entry.get('target')}  count={entry.get('count')} "
            f"from={entry.get('last_sender')}  last={when}"
        )
    print(f"  drain a name's backlog: syn inbox --as {entries[0].get('target')}")


def _render_approvals(snapshot: dict[str, Any]) -> None:
    """Render the relays awaiting a second operator — the two-person quorum's pending set.

    The two-person approval ledger is per-hub live state with no other query
    surface, so an enforced quorum was otherwise invisible to an operator. It
    rides in the same state snapshot the dashboard and cockpit read; this brings
    the pending set to a terminal, oldest first, naming each pending action and
    the first requester a second, different operator must join to reach quorum,
    with the exact remedy. An empty ledger is stated plainly, not left as silence.
    """
    entries = list(snapshot.get("pending_relay_approvals", []))
    if not entries:
        print("Pending approvals: none — no relay is awaiting a second operator.")
        return
    print(f"Pending approvals ({len(entries)}: relays awaiting a second, different operator):")
    for entry in entries:
        print(
            f"  {entry.get('action')} on {entry.get('namespace')}/{entry.get('task_id')}  "
            f"requested by {entry.get('requester')}  awaiting a different operator"
        )
    print(
        "  approve: a second, different operator re-issues the same "
        "`synapse federation relay` on the owning hub"
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
        contracts = card.get("contracts")
        suffix = (
            f" (contracts: {len(contracts)})" if isinstance(contracts, list) and contracts else ""
        )
        print(f"  {card.get('agent')} [{classes}] model={model}: {description}{suffix}")
