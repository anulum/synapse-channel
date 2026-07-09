# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — one-line hub status for shell prompts and tmux status bars
"""A single glanceable line summarising the hub — built for prompts and status bars.

``synapse status`` answers the question a shell prompt or a tmux status bar asks
dozens of times a day: is the hub up, how many agents are live, and how contended
is it right now. It draws the live roster from the ``who`` snapshot (the authoritative
set of open connections, not the cumulative ``last_seen`` ledger) and the active
leases from the ``state`` snapshot, over a single connection, and prints one line.
The exit code doubles as the signal a prompt colours on: ``0`` reachable, ``1`` down.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import sys
from dataclasses import dataclass
from typing import Any, TextIO

from synapse_channel.cli_query_transport import AgentFactory
from synapse_channel.client.agent import SynapseAgent, default_hub_uri
from synapse_channel.core.protocol import MessageType
from synapse_channel.observed_peers import (
    ObservedPeerSnapshot,
    ObservedPeerSpec,
    fetch_observed_peers,
    network_observed_fetcher_factory,
    observed_claim_count,
    observed_max_lag,
    observed_peers_to_dict,
    parse_observed_peer,
)
from synapse_channel.waiter_identity import split_roster


@dataclass(frozen=True)
class HubStatus:
    """The counts behind a status line: reachability plus live agents and leases.

    Parameters
    ----------
    reachable : bool
        Whether the welcome handshake completed within the readiness window.
    online : int
        Agents holding an open connection right now (excludes the status probe
        and ``-rx`` waiter sidecars — a wake listener is presence plumbing, not
        an agent, and counting sidecars let a 30-terminal workstation read as
        hundreds of agents).
    claims : int
        Active task leases on the hub.
    resources : int
        Live resource offers on the hub.
    waiters : int
        Wake-listener sidecars (``-rx``) holding open connections.
    """

    reachable: bool
    online: int = 0
    claims: int = 0
    resources: int = 0
    waiters: int = 0
    observed_peers: tuple[ObservedPeerSnapshot, ...] = ()


def _count_word(count: int, singular: str) -> str:
    """Return ``"1 agent"`` or ``"3 agents"`` — singular for one, plural otherwise."""
    word = singular if count == 1 else f"{singular}s"
    return f"{count} {word}"


def render_status_line(status: HubStatus, *, plain: bool = False) -> str:
    """Build the one-line summary for ``status``.

    Agents and claims always appear — they are the two numbers a coordinating agent
    reads at a glance. Waiter sidecars and resources are appended only when at least
    one is live, so an unused feature never widens a status bar. ``plain`` drops
    every non-ASCII glyph for prompts and terminals that cannot render them.

    Parameters
    ----------
    status : HubStatus
        The counts to render.
    plain : bool, optional
        When true, emit ASCII only (no ``●``/``○`` liveness glyph, no ``·`` divider).

    Returns
    -------
    str
        The status line, without a trailing newline.
    """
    if not status.reachable:
        return "synapse offline" if plain else "synapse ○ offline"
    segments = [_count_word(status.online, "agent"), _count_word(status.claims, "claim")]
    if status.waiters:
        segments.append(_count_word(status.waiters, "waiter"))
    if status.resources:
        segments.append(_count_word(status.resources, "resource"))
    if status.observed_peers:
        observed_claims = observed_claim_count(status.observed_peers)
        segments.append(_count_word(len(status.observed_peers), "observed peer"))
        if observed_claims:
            segments.append(_count_word(observed_claims, "observed claim"))
        max_lag = observed_max_lag(status.observed_peers)
        if max_lag is not None:
            segments.append(f"max lag {max_lag}")
    if plain:
        return "synapse online " + " ".join(segments)
    return "synapse ● " + " · ".join(segments)


async def query_status(
    *,
    uri: str,
    name: str = "USER",
    agent_factory: AgentFactory = SynapseAgent,
    token: str | None = None,
    ready_timeout: float = 5.0,
    attempts: int = 50,
    observed_peers: tuple[ObservedPeerSpec, ...] = (),
    observed_token: str | None = None,
    observed_timeout: float = 10.0,
) -> HubStatus:
    """Connect once, request the roster and the state, and return the status counts.

    A ``<name>-status`` probe connection asks both questions so the caller's own
    identity keeps its real presence unchanged, and the probe is filtered back out
    of the roster so it never counts itself. If the hub cannot be reached the
    returned status is simply unreachable — the command turns that into the offline
    line and a non-zero exit rather than a diagnostic dump.

    Parameters
    ----------
    uri, name : str
        Hub URI and the caller's display name; the probe connects as ``<name>-status``.
    agent_factory : AgentFactory, optional
        Factory for the client agent; injectable for testing.
    token : str or None, optional
        Shared-secret token for a secured hub.
    ready_timeout : float, optional
        Seconds to await the welcome handshake before treating the hub as down.
    attempts : int, optional
        Poll attempts (50 ms each) for both snapshots to arrive. Defaults to ``50``.

    Returns
    -------
    HubStatus
        Reachable with counts, or ``HubStatus(reachable=False)`` when the hub is down.
    """
    probe = f"{name}-status"
    seen: dict[str, dict[str, Any]] = {}

    async def collect(data: dict[str, Any]) -> None:
        kind = data.get("type")
        if kind in (MessageType.WHO_SNAPSHOT, MessageType.STATE_SNAPSHOT):
            seen[str(kind)] = data

    agent = agent_factory(probe, collect, uri=uri, verbose=False, token=token)
    conn_task = asyncio.create_task(agent.connect())
    try:
        if not await agent.wait_until_ready(timeout=ready_timeout):
            return HubStatus(reachable=False)
        await agent.request_who()
        await agent.request_state()
        for _ in range(attempts):
            if MessageType.WHO_SNAPSHOT in seen and MessageType.STATE_SNAPSHOT in seen:
                break
            await asyncio.sleep(0.05)
        observed = await fetch_observed_peers(
            observed_peers,
            fetcher_factory=network_observed_fetcher_factory(
                local_id=f"{name}-observed",
                token=observed_token,
                timeout=observed_timeout,
            ),
        )
        return _tally(seen, probe=probe, observed_peers=observed)
    finally:
        agent.running = False
        conn_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await conn_task


def _tally(
    seen: dict[str, dict[str, Any]],
    *,
    probe: str,
    observed_peers: tuple[ObservedPeerSnapshot, ...] = (),
) -> HubStatus:
    """Fold the collected ``who`` and ``state`` replies into a reachable ``HubStatus``."""
    who = seen.get(MessageType.WHO_SNAPSHOT, {})
    roster = who.get("online_agents", [])
    names = (
        [str(agent) for agent in roster if str(agent) != probe] if isinstance(roster, list) else []
    )
    agents, waiters = split_roster(names)
    state = seen.get(MessageType.STATE_SNAPSHOT, {})
    snapshot = state.get("snapshot", {})
    claims = _len_of(snapshot.get("active_claims")) if isinstance(snapshot, dict) else 0
    resources = _len_of(snapshot.get("resources")) if isinstance(snapshot, dict) else 0
    return HubStatus(
        reachable=True,
        online=len(agents),
        claims=claims,
        resources=resources,
        waiters=len(waiters),
        observed_peers=observed_peers,
    )


def _len_of(value: object) -> int:
    """Return ``len(value)`` for a sized reply field, or ``0`` when it is absent."""
    return len(value) if isinstance(value, (list, dict)) else 0


def status_to_json(status: HubStatus) -> dict[str, object]:
    """Return the status counts as a stable JSON-compatible object."""
    return {
        "reachable": status.reachable,
        "online": status.online,
        "claims": status.claims,
        "resources": status.resources,
        "waiters": status.waiters,
        "observed_peers": observed_peers_to_dict(status.observed_peers),
        "observed_claims": observed_claim_count(status.observed_peers),
        "observed_max_lag": observed_max_lag(status.observed_peers),
    }


async def watch_status(
    *,
    uri: str,
    name: str = "USER",
    token: str | None = None,
    ready_timeout: float = 5.0,
    interval: float = 2.0,
    count: int = 0,
    as_json: bool = False,
    plain: bool = False,
    agent_factory: AgentFactory = SynapseAgent,
    out: TextIO | None = None,
    observed_peers: tuple[ObservedPeerSpec, ...] = (),
    observed_token: str | None = None,
    observed_timeout: float = 10.0,
) -> int:
    """Refresh the status every ``interval`` seconds — a watch-style dashboard.

    Each refresh opens its own probe connection (exactly one :func:`query_status`
    call), so a hub restart between refreshes shows up as an honest offline line
    rather than a stale connection error. On a TTY the line rewrites in place;
    piped output appends one line per refresh so a consumer can stream it, and
    ``--json`` emits one JSON object per line (NDJSON) either way. ``count``
    bounds the refreshes (``0`` runs until interrupted). The exit code reports
    the LAST observed state: ``0`` reachable, ``1`` down.

    Parameters
    ----------
    uri, name, token, ready_timeout
        Passed through to :func:`query_status` unchanged.
    interval : float, optional
        Seconds between refreshes.
    count : int, optional
        Refreshes to run; ``0`` means until interrupted.
    as_json : bool, optional
        Emit NDJSON instead of the human line.
    plain : bool, optional
        ASCII-only rendering for the human line.
    agent_factory : AgentFactory, optional
        Factory for the probe agent; injectable for testing.
    out : typing.TextIO or None, optional
        Output stream; defaults to ``sys.stdout``.

    Returns
    -------
    int
        ``0`` when the last refresh saw a reachable hub, ``1`` otherwise.
    """
    stream = sys.stdout if out is None else out
    in_place = stream.isatty() and not as_json
    status = HubStatus(reachable=False)
    refreshes = 0
    try:
        while True:
            status = await query_status(
                uri=uri,
                name=name,
                agent_factory=agent_factory,
                token=token,
                ready_timeout=ready_timeout,
                observed_peers=observed_peers,
                observed_token=observed_token,
                observed_timeout=observed_timeout,
            )
            if as_json:
                stream.write(json.dumps(status_to_json(status), sort_keys=True) + "\n")
            elif in_place:
                stream.write("\r\x1b[2K" + render_status_line(status, plain=plain))
            else:
                stream.write(render_status_line(status, plain=plain) + "\n")
            stream.flush()
            refreshes += 1
            if count and refreshes >= count:
                break
            await asyncio.sleep(interval)
    finally:
        if in_place:
            stream.write("\n")
            stream.flush()
    return 0 if status.reachable else 1


def _cmd_status(args: argparse.Namespace) -> int:
    """Dispatch ``status``: print the line, exit ``0`` if reachable else ``1``.

    With ``--watch`` the line refreshes every ``--interval`` seconds until
    ``--count`` refreshes ran or the operator interrupts; Ctrl-C is the normal
    way to stop a watch, so it exits ``0`` rather than tracing.
    """
    if args.watch:
        if args.interval <= 0:
            print("--interval must be positive", file=sys.stderr)
            return 2
        try:
            return asyncio.run(
                watch_status(
                    uri=args.uri,
                    name=args.name,
                    token=args.token,
                    ready_timeout=args.ready_timeout,
                    interval=args.interval,
                    count=args.count,
                    as_json=args.json,
                    plain=args.plain,
                    observed_peers=tuple(getattr(args, "observed_peers", ())),
                    observed_token=getattr(args, "observed_token", None),
                    observed_timeout=float(getattr(args, "observed_timeout", 10.0)),
                )
            )
        except KeyboardInterrupt:
            return 0
    status = asyncio.run(
        query_status(
            uri=args.uri,
            name=args.name,
            token=args.token,
            ready_timeout=args.ready_timeout,
            observed_peers=tuple(getattr(args, "observed_peers", ())),
            observed_token=getattr(args, "observed_token", None),
            observed_timeout=float(getattr(args, "observed_timeout", 10.0)),
        )
    )
    if args.json:
        print(json.dumps(status_to_json(status), sort_keys=True))
    else:
        print(render_status_line(status, plain=args.plain))
    return 0 if status.reachable else 1


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``status`` subparser."""
    status = subparsers.add_parser(
        "status",
        help="Print a one-line hub summary for shell prompts and tmux status bars.",
    )
    status.add_argument("--uri", default=default_hub_uri())
    status.add_argument("--name", default="USER")
    status.add_argument(
        "--plain",
        action="store_true",
        help="Emit ASCII only (no liveness glyph or divider) for plain prompts.",
    )
    status.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    status.add_argument(
        "--ready-timeout", type=float, default=5.0, help="Seconds to await hub readiness."
    )
    status.add_argument(
        "--json",
        action="store_true",
        help="Emit the counts as JSON for monitoring scripts instead of the line.",
    )
    status.add_argument(
        "--watch",
        action="store_true",
        help="Refresh the line every --interval seconds until interrupted (Ctrl-C).",
    )
    status.add_argument(
        "--interval",
        type=float,
        default=2.0,
        help="Seconds between --watch refreshes.",
    )
    status.add_argument(
        "--count",
        type=int,
        default=0,
        help="Stop after this many --watch refreshes (0 = until interrupted).",
    )
    status.add_argument(
        "--observed-peer",
        action="append",
        default=[],
        type=parse_observed_peer,
        dest="observed_peers",
        metavar="HUB=URI",
        help=("Fetch a peer hub's multi-hub event log and append observed@HUB advisory counts."),
    )
    status.add_argument(
        "--observed-token",
        default=None,
        help="Shared-secret token used for every --observed-peer pull.",
    )
    status.add_argument(
        "--observed-timeout",
        type=float,
        default=10.0,
        help="Seconds to wait for each observed peer pull.",
    )
    status.set_defaults(func=_cmd_status)
