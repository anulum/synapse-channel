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

The fleet plays two acts. The first leaves the standing state a cockpit finds:
everyone joined, the plan posted and all but one claim held. The second is what
an operator watches: the last claim is taken and its task begun, the messages
are exchanged, the finding is recorded. With ``--two-acts`` the script prints a
line after each act and pauses between them until a line arrives on its
standard input or the input ends, so that a cockpit can be opened in between.

Usage: ``python demo_fleet.py HUB_URI [--long-names] [--two-acts]``
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import signal
import sys
import threading
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
    ("LIGHT-202", "Prepare the release notes", ("HARBOUR-101", "HARBOUR-104", "HARBOUR-110")),
    ("LIGHT-203", "Publish the changelog", ()),
    ("LIGHT-204", "Update the quick start", ()),
)
"""The shared plan: task id, title and the ids it depends on.

One prerequisite, ``HARBOUR-110``, is a task nobody posts: the plan's one
mistake, which the cockpit tells apart from a prerequisite that is merely
unfinished.
"""

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

LATE_TASK = "LIGHT-204"
"""The task that is claimed and begun in the second act."""

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


async def _take(agent: SynapseAgent, inbox: Inbox, task_id: str, paths: tuple[str, ...]) -> None:
    """Claim one task with its paths and report it as being worked on."""
    await agent.claim(task_id, paths=list(paths))
    await inbox.wait_for(_confirms("claim_granted", task_id))
    await agent.update_task(task_id, status="working")
    await inbox.wait_for(_confirms("task_updated", task_id))


async def _first_act(
    agents: dict[str, SynapseAgent], inbox: Inbox, rename: Callable[[str], str]
) -> None:
    """Post the plan, set its statuses and take its claims, all but the late task's."""
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
        if task_id != LATE_TASK:
            await planner.update_ledger_task(rename(task_id), status=status)
            await inbox.wait_for(_confirms("ledger_task_updated", rename(task_id)))
    for owner, task_id, paths in CLAIMS:
        if task_id != LATE_TASK:
            await _take(agents[owner], inbox, rename(task_id), paths)


async def _second_act(
    agents: dict[str, SynapseAgent], inbox: Inbox, rename: Callable[[str], str]
) -> None:
    """Take and begin the late task, exchange the messages and record the finding."""
    for owner, task_id, paths in CLAIMS:
        if task_id == LATE_TASK:
            await _take(agents[owner], inbox, rename(task_id), paths)
    for task_id, status in BOARD_STATUSES:
        if task_id == LATE_TASK:
            await agents["harbour/planner"].update_ledger_task(rename(task_id), status=status)
            await inbox.wait_for(_confirms("ledger_task_updated", rename(task_id)))
    for sender, target, text in MESSAGES:
        for task_id, _title, _needs in TASKS:
            text = text.replace(task_id, rename(task_id))
        await agents[sender].chat(text, target=target if target == "all" else rename(target))
    await agents["harbour/reviewer"].record_finding(
        FINDING, subkind="decision", project=rename("harbour")
    )
    await inbox.wait_for(lambda message: message.get("type") == "finding_recorded")


def _report(rename: Callable[[str], str], *, late_claim: bool, act: int | None) -> None:
    """Print one line naming the participants, the tasks and the claims held so far."""
    line: dict[str, object] = {"ready": True}
    if act is not None:
        line["act"] = act
    line["participants"] = [rename(name) for name in PARTICIPANTS]
    line["tasks"] = [rename(task_id) for task_id, _title, _needs in TASKS]
    line["claims"] = [
        rename(task_id) for _owner, task_id, _paths in CLAIMS if late_claim or task_id != LATE_TASK
    ]
    print(json.dumps(line), flush=True)


def _await_input(loop: asyncio.AbstractEventLoop, begin: asyncio.Event) -> None:
    """Set ``begin`` when a line arrives on standard input or the input ends.

    Runs in a daemon thread, so a read that never returns does not keep the
    process alive after the fleet has left.
    """
    sys.stdin.readline()
    with contextlib.suppress(RuntimeError):
        loop.call_soon_threadsafe(begin.set)


async def _either(first: asyncio.Event, second: asyncio.Event, seconds: float) -> None:
    """Return when either event is set or the time is over."""
    waits = [asyncio.ensure_future(first.wait()), asyncio.ensure_future(second.wait())]
    await asyncio.wait(waits, timeout=max(0.0, seconds), return_when=asyncio.FIRST_COMPLETED)
    for wait in waits:
        wait.cancel()
    await asyncio.gather(*waits, return_exceptions=True)


async def run_fleet(
    uri: str,
    *,
    rename: Callable[[str], str] = str,
    two_acts: bool = False,
    join_seconds: float = JOIN_SECONDS,
    hold_seconds: float = HOLD_SECONDS,
) -> int:
    """Connect the fleet, play its two acts, report and hold until stopped.

    Parameters
    ----------
    uri : str
        WebSocket address of the running hub.
    rename : Callable[[str], str], optional
        Maps each name of the picture's fleet to the name used on the hub; the
        default keeps the names, :func:`long_name` gives the long ones.
    two_acts : bool, optional
        Pause after the first act, print a line for it, and begin the second
        act when a line arrives on standard input or the input ends. A stop or
        the hold's end during the pause ends the fleet without a second act.
        By default the acts follow each other and one line is printed.
    join_seconds : float, optional
        Seconds each participant may take to join.
    hold_seconds : float, optional
        Seconds the fleet stays connected after its first act when no signal
        stops it.

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
    begin = asyncio.Event()
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
        await _first_act(agents, inbox, rename)
        leave_at = loop.time() + hold_seconds
        if two_acts:
            _report(rename, late_claim=False, act=1)
            threading.Thread(target=_await_input, args=(loop, begin), daemon=True).start()
            await _either(begin, stop, leave_at - loop.time())
        else:
            begin.set()
        if begin.is_set() and not stop.is_set():
            await _second_act(agents, inbox, rename)
            _report(rename, late_claim=True, act=2 if two_acts else None)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=max(0.0, leave_at - loop.time()))
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
    options = ("--long-names", "--two-acts")
    addresses = [argument for argument in sys.argv[1:] if argument not in options]
    if len(addresses) != 1:
        print("usage: python demo_fleet.py HUB_URI [--long-names] [--two-acts]", file=sys.stderr)
        return 2
    rename = long_name if "--long-names" in sys.argv[1:] else str
    return asyncio.run(
        run_fleet(addresses[0], rename=rename, two_acts="--two-acts" in sys.argv[1:])
    )


if __name__ == "__main__":
    raise SystemExit(main())
