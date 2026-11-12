# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — demonstration fleet for the cockpit's public picture
"""Connect a small demonstration fleet to a running hub and hold it there.

Six participants of two invented projects join the hub through the public
client, post a shared plan, take and hold claims, exchange a few messages and
record one finding. Every name is invented for the picture; none is a real
seat. No participant arms a waiter, so the cockpit marks each one as it would
any seat that cannot be woken. The script prints one JSON line naming the
participants, tasks and held claims when the fleet is in place, then waits
until it is stopped or its deadline passes.

Usage: ``python demo_fleet.py HUB_URI``
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import signal
import sys
from collections.abc import Callable
from typing import Any

from synapse_channel.client.agent import SynapseAgent

Message = dict[str, Any]

PARTICIPANTS = (
    "harbour/planner",
    "harbour/api",
    "harbour/web",
    "harbour/reviewer",
    "lighthouse/docs",
    "lighthouse/release",
)
"""The demonstration participants, as ``project/seat``."""

TASKS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("HARBOUR-101", "Add rate limiting to the upload endpoint", ()),
    ("HARBOUR-102", "Show upload progress in the web client", ("HARBOUR-101",)),
    ("HARBOUR-103", "Review the upload limits", ("HARBOUR-101",)),
    ("HARBOUR-104", "Paginate the audit list", ()),
    ("HARBOUR-105", "Fix the flaky session test", ()),
    ("LIGHT-201", "Document the upload limits", ()),
    ("LIGHT-202", "Prepare the release notes", ("HARBOUR-101", "HARBOUR-104")),
    ("LIGHT-203", "Publish the changelog", ()),
    ("LIGHT-204", "Update the quick start", ()),
)
"""The shared plan: task id, title and the ids it depends on."""

BOARD_STATUSES = (
    ("HARBOUR-101", "in_progress"),
    ("HARBOUR-103", "blocked"),
    ("HARBOUR-104", "in_progress"),
    ("HARBOUR-105", "done"),
    ("LIGHT-203", "done"),
    ("LIGHT-204", "in_progress"),
)
"""Plan statuses set after the tasks are posted; the rest stay open."""

CLAIMS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("harbour/api", "HARBOUR-101", ("src/api/upload.py", "tests/test_upload.py")),
    ("harbour/web", "HARBOUR-104", ("src/web/audit/",)),
    ("lighthouse/docs", "LIGHT-204", ("docs/quickstart.md",)),
)
"""Held claims: owner, task id and the paths in scope."""

MESSAGES = (
    ("harbour/planner", "harbour/api", "HARBOUR-101 is yours; the limit is in the design note."),
    ("harbour/api", "harbour/reviewer", "HARBOUR-101 is ready for a first read."),
    ("harbour/reviewer", "harbour/api", "Read. One question on the retry header."),
    ("lighthouse/docs", "all", "The quick start is being updated; hold doc edits there."),
)
"""Chat traffic: sender, recipient and text."""

FINDING = "Upload limits are enforced by the API, not by the web client."
"""The one finding the reviewer records."""

JOIN_SECONDS = 10.0
"""How long each participant may take to join the hub."""

HOLD_SECONDS = 180.0
"""How long the fleet stays connected when nothing stops it."""


class Inbox:
    """Collect every message the fleet receives and wait for one of a kind."""

    def __init__(self) -> None:
        self._messages: list[Message] = []
        self._arrived = asyncio.Event()

    async def __call__(self, message: Message) -> None:
        """Record one inbound message."""
        self._messages.append(message)
        self._arrived.set()

    async def wait_for(self, matches: Callable[[Message], bool], timeout: float = 5.0) -> None:
        """Return once a recorded message satisfies ``matches``.

        Parameters
        ----------
        matches : Callable[[Message], bool]
            Predicate over one inbound message.
        timeout : float, optional
            Seconds to wait before giving up.

        Raises
        ------
        TimeoutError
            If no matching message arrives in time.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        seen = 0
        while True:
            if any(matches(message) for message in self._messages[seen:]):
                return
            seen = len(self._messages)
            self._arrived.clear()
            remaining = deadline - loop.time()
            if remaining <= 0:
                refusals = [
                    str(m.get("payload")) for m in self._messages if m.get("type") == "error"
                ]
                reason = refusals[-1] if refusals else "no refusal was received"
                raise TimeoutError(f"the hub did not confirm a demonstration step: {reason}")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._arrived.wait(), timeout=remaining)


def _confirms(kind: str, task_id: str) -> Callable[[Message], bool]:
    """Return a predicate for the hub's confirmation of one task step."""

    def matches(message: Message) -> bool:
        if message.get("type") != kind:
            return False
        task = message.get("task")
        nested = task.get("task_id") if isinstance(task, dict) else None
        return task_id in (message.get("task_id"), nested)

    return matches


async def _seed(agents: dict[str, SynapseAgent], inbox: Inbox) -> None:
    """Post the plan, set its statuses, take the claims, talk and record the finding."""
    planner = agents["harbour/planner"]
    for task_id, title, depends_on in TASKS:
        project = "harbour" if task_id.startswith("HARBOUR") else "lighthouse"
        await planner.post_task(task_id, title, depends_on=list(depends_on), project=project)
        await inbox.wait_for(_confirms("ledger_task_posted", task_id))
    for task_id, status in BOARD_STATUSES:
        await planner.update_ledger_task(task_id, status=status)
        await inbox.wait_for(_confirms("ledger_task_updated", task_id))
    for owner, task_id, paths in CLAIMS:
        await agents[owner].claim(task_id, paths=list(paths))
        await inbox.wait_for(_confirms("claim_granted", task_id))
        await agents[owner].update_task(task_id, status="working")
        await inbox.wait_for(_confirms("task_updated", task_id))
    for sender, target, text in MESSAGES:
        await agents[sender].chat(text, target=target)
    await agents["harbour/reviewer"].record_finding(FINDING, subkind="decision", project="harbour")
    await inbox.wait_for(lambda message: message.get("type") == "finding_recorded")


async def run_fleet(
    uri: str, *, join_seconds: float = JOIN_SECONDS, hold_seconds: float = HOLD_SECONDS
) -> int:
    """Connect the fleet, seed it, report readiness and hold until stopped.

    Parameters
    ----------
    uri : str
        WebSocket address of the running hub.
    join_seconds : float, optional
        Seconds each participant may take to join.
    hold_seconds : float, optional
        Seconds the fleet stays connected when no signal stops it.

    Returns
    -------
    int
        Zero after every participant has disconnected.

    Raises
    ------
    TimeoutError
        If a participant does not join, or the hub does not confirm a step.
    """
    inbox = Inbox()
    agents = {
        name: SynapseAgent(name, inbox, uri=uri, heartbeat_interval=20.0, verbose=False)
        for name in PARTICIPANTS
    }
    connections = [asyncio.create_task(agent.connect()) for agent in agents.values()]
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    handled: list[signal.Signals] = []
    for signum in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(signum, stop.set)
            handled.append(signum)
    try:
        for name, agent in agents.items():
            if not await agent.wait_until_ready(join_seconds):
                raise TimeoutError(f"{name} did not join the hub")
        await _seed(agents, inbox)
        ready = {
            "ready": True,
            "participants": list(PARTICIPANTS),
            "tasks": [task_id for task_id, _title, _needs in TASKS],
            "claims": [task_id for _owner, task_id, _paths in CLAIMS],
        }
        print(json.dumps(ready), flush=True)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=hold_seconds)
    finally:
        for signum in handled:
            loop.remove_signal_handler(signum)
        for connection in connections:
            connection.cancel()
        await asyncio.gather(*connections, return_exceptions=True)
    return 0


def main() -> int:
    """Run the demonstration fleet against the hub named on the command line.

    Returns
    -------
    int
        Zero after the fleet has disconnected; two for a missing argument.
    """
    if len(sys.argv) != 2:
        print("usage: python demo_fleet.py HUB_URI", file=sys.stderr)
        return 2
    return asyncio.run(run_fleet(sys.argv[1]))


if __name__ == "__main__":
    raise SystemExit(main())
