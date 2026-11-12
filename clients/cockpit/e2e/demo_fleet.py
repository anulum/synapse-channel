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

With ``--long-names`` the same fleet joins under long names that differ only at
their ends, as the names of a large installation do; a journey uses it to check
that a narrow column still shows which seat and which task a row is about.

Usage: ``python demo_fleet.py HUB_URI [--long-names]``
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

LONG_PROJECTS = {
    "harbour": "harbour-coastal-monitoring-programme",
    "lighthouse": "lighthouse-navigation-services-group",
}
"""The long form of each invented project."""

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


def long_name(name: str) -> str:
    """Return the long form of a participant, a project or a task name.

    Parameters
    ----------
    name : str
        A name of the picture's fleet: ``project/seat``, a project, or a task
        such as ``HARBOUR-101``.

    Returns
    -------
    str
        A name of the same kind that keeps what distinguishes it, the seat or
        the task number, at its very end.
    """
    if "/" in name:
        project, seat = name.split("/", 1)
        return f"{LONG_PROJECTS[project]}/autonomous-operations-seat-{seat}"
    if name in LONG_PROJECTS:
        return LONG_PROJECTS[name]
    prefix, number = name.rsplit("-", 1)
    return f"{prefix}-COASTAL-OPERATIONS-WORK-PACKAGE-{number}"


def _confirms(kind: str, task_id: str) -> Callable[[Message], bool]:
    """Return a predicate for the hub's confirmation of one task step."""

    def matches(message: Message) -> bool:
        if message.get("type") != kind:
            return False
        task = message.get("task")
        nested = task.get("task_id") if isinstance(task, dict) else None
        return task_id in (message.get("task_id"), nested)

    return matches


async def _seed(
    agents: dict[str, SynapseAgent], inbox: Inbox, rename: Callable[[str], str]
) -> None:
    """Post the plan, set its statuses, take the claims, talk and record the finding."""
    planner = agents["harbour/planner"]
    for task_id, title, depends_on in TASKS:
        project = "harbour" if task_id.startswith("HARBOUR") else "lighthouse"
        await planner.post_task(
            rename(task_id),
            title,
            depends_on=[rename(needed) for needed in depends_on],
            project=rename(project),
        )
        await inbox.wait_for(_confirms("ledger_task_posted", rename(task_id)))
    for task_id, status in BOARD_STATUSES:
        await planner.update_ledger_task(rename(task_id), status=status)
        await inbox.wait_for(_confirms("ledger_task_updated", rename(task_id)))
    for owner, task_id, paths in CLAIMS:
        await agents[owner].claim(rename(task_id), paths=list(paths))
        await inbox.wait_for(_confirms("claim_granted", rename(task_id)))
        await agents[owner].update_task(rename(task_id), status="working")
        await inbox.wait_for(_confirms("task_updated", rename(task_id)))
    for sender, target, text in MESSAGES:
        for task_id, _title, _needs in TASKS:
            text = text.replace(task_id, rename(task_id))
        await agents[sender].chat(text, target=target if target == "all" else rename(target))
    await agents["harbour/reviewer"].record_finding(
        FINDING, subkind="decision", project=rename("harbour")
    )
    await inbox.wait_for(lambda message: message.get("type") == "finding_recorded")


async def run_fleet(
    uri: str,
    *,
    rename: Callable[[str], str] = str,
    join_seconds: float = JOIN_SECONDS,
    hold_seconds: float = HOLD_SECONDS,
) -> int:
    """Connect the fleet, seed it, report readiness and hold until stopped.

    Parameters
    ----------
    uri : str
        WebSocket address of the running hub.
    rename : Callable[[str], str], optional
        Maps each name of the picture's fleet to the name used on the hub; the
        default keeps the names, :func:`long_name` gives the long ones.
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
        name: SynapseAgent(rename(name), inbox, uri=uri, heartbeat_interval=20.0, verbose=False)
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
                raise TimeoutError(f"{rename(name)} did not join the hub")
        await _seed(agents, inbox, rename)
        ready = {
            "ready": True,
            "participants": [rename(name) for name in PARTICIPANTS],
            "tasks": [rename(task_id) for task_id, _title, _needs in TASKS],
            "claims": [rename(task_id) for _owner, task_id, _paths in CLAIMS],
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
        Zero after the fleet has disconnected; two unless exactly one hub
        address is given.
    """
    addresses = [argument for argument in sys.argv[1:] if argument != "--long-names"]
    if len(addresses) != 1:
        print("usage: python demo_fleet.py HUB_URI [--long-names]", file=sys.stderr)
        return 2
    rename = long_name if "--long-names" in sys.argv[1:] else str
    return asyncio.run(run_fleet(addresses[0], rename=rename))


if __name__ == "__main__":
    raise SystemExit(main())
