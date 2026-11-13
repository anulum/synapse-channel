# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — seal an attempt, remove an applied marker, read an operation's attempts
"""``synapse operation seal``, ``unmark`` and ``state``.

``state`` asks the hub which attempts an operation has and what state each is
in; the hub answers only an identity holding ``operation-evidence`` or
``operation-seal`` on the task.

``seal`` ends an attempt whose executor fell silent, for certain. On one
connection it asks the hub for the attempt's state and refuses unless it is
open; then it creates the attempt's ``sealed`` marker at the system of record
(a ref transaction in ``--repository``, or an atomic push to ``--remote``),
reads it back, and only then sends the ``seal`` record. A seal with no hub
record is safe; a hub record with no seal cannot happen in this order. It needs
``operation-seal`` on the task, which no identity holds by default.

``unmark`` removes one attempt's ``applied`` marker after custody: only when
the hub reports the operation closed, and only while the marker holds this
attempt's ``applied`` blob. A ``sealed`` marker is never removed here.

Exit codes: 0 done; 1 refused (by the hub, or the attempt is not open, or the
effect landed first, or the operation is not closed, or the marker is not
``applied``); 2 no verdict (the hub is unreachable or silent, or the seal did
not reach the repository: send it again); 3 the repository's state is
unknown; 4 the hub does not know ``operation_state``; 5 an input could not be
used (no ``git``, or an identifier of the wrong form).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from synapse_channel.cli_messaging_types import AgentFactory
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.connect_failures import closed_after_ready, describe_connect_failure
from synapse_channel.core.attempt_markers import (
    LANDED,
    LANDED_EARLIER,
    NOT_REACHED,
    OTHER_PARTY_FIRST,
    AttemptMarker,
    AttemptMarkerError,
)
from synapse_channel.core.protocol import MessageType
from synapse_channel.git.attempt_marker_changes import (
    Target,
    object_format,
    remove_applied_marker,
    seal,
)
from synapse_channel.git.git_runtime import GitError, git_executable
from synapse_channel.terminal_text import terminal_text

EXIT_DONE = 0
EXIT_REFUSED = 1
EXIT_NO_VERDICT = 2
EXIT_UNKNOWN = 3
EXIT_UNSUPPORTED = 4
EXIT_UNUSABLE = 5

_UNKNOWN_TYPE_MARKERS = ("unknown message type", "unsupported message type")


class _HubLink:
    """One connected identity sending requests and awaiting their answers."""

    def __init__(self, args: argparse.Namespace, agent_factory: AgentFactory) -> None:
        self._args = args
        self._replies: list[dict[str, Any]] = []
        self._agent = agent_factory(
            args.name, self._collect, uri=args.uri, verbose=False, token=args.token
        )
        self._connection: asyncio.Task[None] | None = None

    async def _collect(self, data: dict[str, Any]) -> None:
        self._replies.append(data)

    async def open(self) -> bool:
        """Connect; print why not and return ``False`` when the hub does not take us."""
        self._connection = asyncio.create_task(self._agent.connect())
        timeout = self._args.ready_timeout
        if await self._agent.wait_until_ready(timeout=timeout) and not await closed_after_ready(
            self._agent, timeout=timeout
        ):
            return True
        failure = describe_connect_failure(
            self._args.name,
            self._args.uri,
            close_code=self._agent.last_close_code,
            close_reason=self._agent.last_close_reason,
        )
        print(terminal_text(failure))
        return False

    async def close(self) -> None:
        """Stop the connection started by :meth:`open`."""
        self._agent.running = False
        assert self._connection is not None, "close() follows open()"
        self._connection.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._connection

    async def ask(self, msg_type: str, reply_type: str, **fields: Any) -> dict[str, Any] | None:
        """Send one request and return its answer or a hub error, or ``None`` when silent."""
        request_id = uuid.uuid4().hex
        mark = len(self._replies)
        await self._agent.send_message(msg_type, target="System", request_id=request_id, **fields)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(float(self._args.timeout), 0.0)
        while loop.time() <= deadline:
            for reply in self._replies[mark:]:
                if reply.get("type") == MessageType.ERROR or (
                    reply.get("type") == reply_type and reply.get("request_id") == request_id
                ):
                    return reply
            await asyncio.sleep(0.01)
        return None


def _hub_refusal(reply: dict[str, Any] | None, what: str) -> int | None:
    """Print why a hub answer is not an accepted one and return its exit code, else ``None``."""
    if reply is None:
        print(f"{what} failed: the hub returned no answer")
        return EXIT_NO_VERDICT
    detail = terminal_text(str(reply.get("payload") or ""))
    if reply.get("type") == MessageType.ERROR:
        if any(marker in detail.lower() for marker in _UNKNOWN_TYPE_MARKERS):
            print(f"{what} refused (unsupported_by_hub): this hub does not know the seal")
            return EXIT_UNSUPPORTED
        print(f"{what} refused ({reply.get('error_code') or 'hub_error'}): {detail}")
        return EXIT_REFUSED
    if reply.get("status") != "accepted":
        print(f"{what} refused ({reply.get('reason') or 'refused'}): {detail}")
        return EXIT_REFUSED
    return None


async def _state(link: _HubLink, args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    """Ask for the operation's attempts; return an exit code, or 0 with the answer."""
    reply = await link.ask(
        MessageType.OPERATION_STATE,
        MessageType.OPERATION_STATE_RESULT,
        task_id=args.task,
        operation_id=args.operation_id,
    )
    refused = _hub_refusal(reply, "operation state")
    if refused is not None:
        return refused, {}
    assert reply is not None, "_hub_refusal refuses a missing answer"
    return EXIT_DONE, reply


def _attempt_state(answer: dict[str, Any], attempt_id: str) -> str:
    """Return the named attempt's state in a state answer, or ``none``."""
    attempts = answer.get("attempts")
    for item in attempts if isinstance(attempts, list) else []:
        if isinstance(item, dict) and item.get("attempt_id") == attempt_id:
            return str(item.get("state") or "none")
    return "none"


async def _seal(link: _HubLink, args: argparse.Namespace, marker: AttemptMarker, git: str) -> int:
    """Refuse unless the attempt is open, seal it in Git, read it back, then record it."""
    code, answer = await _state(link, args)
    if code != EXIT_DONE:
        return code
    state = _attempt_state(answer, marker.attempt_id)
    if state != "open":
        print(f"attempt {marker.attempt_id} is {state}; nothing sealed")
        return EXIT_REFUSED
    target = Target(Path(args.repository), args.branch, args.base, args.remote)
    try:
        outcome = await asyncio.to_thread(seal, target, marker, git=git)
        fmt = await asyncio.to_thread(object_format, target, git=git)
    except GitError as exc:
        print(f"seal state unknown: {terminal_text(str(exc))}")
        return EXIT_UNKNOWN
    if outcome.answer == OTHER_PARTY_FIRST:
        print(f"the effect of attempt {marker.attempt_id} landed first; nothing sealed")
        return EXIT_REFUSED
    if outcome.answer == NOT_REACHED:
        print(f"the seal did not reach the repository; send it again: {outcome.detail}")
        return EXIT_NO_VERDICT
    if outcome.answer not in (LANDED, LANDED_EARLIER) or outcome.marker is None:
        print(f"seal state unknown: the marker holds {outcome.marker}")
        return EXIT_UNKNOWN
    record = {
        "attempt_id": marker.attempt_id,
        "marker_ref": marker.ref,
        "object_format": fmt,
        "marker_object": outcome.marker,
        "observation": outcome.answer,
    }
    reply = await link.ask(
        MessageType.OPERATION_RECORD,
        MessageType.OPERATION_RECORD_RESULT,
        record="seal",
        task_id=args.task,
        operation_id=args.operation_id,
        seal=record,
    )
    refused = _hub_refusal(reply, "seal record")
    if refused is not None:
        print(f"the seal is in place at {marker.ref}; the hub has no record of it yet")
        return refused
    assert reply is not None
    print(f"attempt {marker.attempt_id} sealed at {marker.ref}; recorded at seq {reply.get('seq')}")
    return EXIT_DONE


async def _unmark(link: _HubLink, args: argparse.Namespace, marker: AttemptMarker, git: str) -> int:
    """Remove the attempt's ``applied`` marker once the hub reports the operation closed."""
    code, answer = await _state(link, args)
    if code != EXIT_DONE:
        return code
    if answer.get("closed") is not True:
        print(f"operation {args.operation_id} is not closed; the marker stays")
        return EXIT_REFUSED
    try:
        removal = await asyncio.to_thread(
            remove_applied_marker, Path(args.repository), marker, git=git, remote=args.remote
        )
    except GitError as exc:
        print(f"marker state unknown: {terminal_text(str(exc))}")
        return EXIT_UNKNOWN
    if not removal.removed:
        print(f"marker {marker.ref} not removed: {terminal_text(removal.detail)}")
        return EXIT_REFUSED
    print(f"applied marker {marker.ref} removed")
    return EXIT_DONE


async def _show_state(link: _HubLink, args: argparse.Namespace) -> int:
    """Print the operation's attempts and their states."""
    code, answer = await _state(link, args)
    if code != EXIT_DONE:
        return code
    listed = answer.get("attempts")
    attempts = (
        [item for item in listed if isinstance(item, dict)] if isinstance(listed, list) else []
    )
    if args.json:
        rendered = {"closed": answer.get("closed") is True, "attempts": attempts}
        print(json.dumps(rendered, indent=2, sort_keys=True))
        return EXIT_DONE
    closed = " (closed)" if answer.get("closed") is True else ""
    print(f"operation {args.operation_id}{closed}: {len(attempts)} attempt(s)")
    for item in attempts:
        print(f"  {item.get('attempt_id')} {item.get('state')} by {item.get('executor')}")
    return EXIT_DONE


Action = Callable[[_HubLink, argparse.Namespace], Awaitable[int]]


async def _run_action(args: argparse.Namespace, action: Action, agent_factory: AgentFactory) -> int:
    """Connect as ``--name``, run one action on that connection, and disconnect."""
    link = _HubLink(args, agent_factory)
    try:
        if not await link.open():
            return EXIT_NO_VERDICT
        return await action(link, args)
    finally:
        await link.close()


def _marker_and_git(args: argparse.Namespace) -> tuple[AttemptMarker, str] | int:
    """Return the attempt's marker and ``git``, or print why not and return exit 5."""
    try:
        marker = AttemptMarker(args.operation_id, args.attempt_id, args.intent_sha256)
        return marker, git_executable()
    except (AttemptMarkerError, GitError) as exc:
        print(f"refused before contacting the hub: {terminal_text(str(exc))}")
        return EXIT_UNUSABLE


async def seal_attempt(args: argparse.Namespace, agent_factory: AgentFactory = SynapseAgent) -> int:
    """Run ``synapse operation seal`` with parsed arguments and return its exit code."""
    prepared = _marker_and_git(args)
    if isinstance(prepared, int):
        return prepared
    marker, git = prepared
    return await _run_action(args, lambda link, a: _seal(link, a, marker, git), agent_factory)


async def unmark_attempt(
    args: argparse.Namespace, agent_factory: AgentFactory = SynapseAgent
) -> int:
    """Run ``synapse operation unmark`` with parsed arguments and return its exit code."""
    prepared = _marker_and_git(args)
    if isinstance(prepared, int):
        return prepared
    marker, git = prepared
    return await _run_action(args, lambda link, a: _unmark(link, a, marker, git), agent_factory)


async def show_state(args: argparse.Namespace, agent_factory: AgentFactory = SynapseAgent) -> int:
    """Run ``synapse operation state`` with parsed arguments and return its exit code."""
    return await _run_action(args, _show_state, agent_factory)


def cmd_seal(args: argparse.Namespace) -> int:
    """Dispatch ``synapse operation seal``."""
    return asyncio.run(seal_attempt(args))


def cmd_unmark(args: argparse.Namespace) -> int:
    """Dispatch ``synapse operation unmark``."""
    return asyncio.run(unmark_attempt(args))


def cmd_state(args: argparse.Namespace) -> int:
    """Dispatch ``synapse operation state``."""
    return asyncio.run(show_state(args))


def add_seal_parsers(
    actions: argparse._SubParsersAction[argparse.ArgumentParser],
    add_common: Callable[[argparse.ArgumentParser], None],
) -> None:
    """Register ``synapse operation seal``, ``unmark`` and ``state``.

    Parameters
    ----------
    actions : argparse._SubParsersAction
        The ``synapse operation`` action group.
    add_common : Callable
        Adds the arguments every ``synapse operation`` action shares.
    """
    state = actions.add_parser("state", help="List an operation's attempts and their states.")
    add_common(state)
    state.add_argument("--operation-id", required=True, help="The declared operation.")
    state.set_defaults(func=cmd_state)
    for name, help_text, func in (
        ("seal", "End an open attempt for certain: seal it at the system of record.", cmd_seal),
        ("unmark", "Remove an attempt's applied marker once the operation is closed.", cmd_unmark),
    ):
        parser = actions.add_parser(name, help=help_text)
        add_common(parser)
        parser.add_argument("--operation-id", required=True, help="The declared operation.")
        parser.add_argument(
            "--attempt-id", required=True, help="The attempt, as the hub issued it."
        )
        parser.add_argument(
            "--intent-sha256", required=True, help="The digest of the operation's intent."
        )
        parser.add_argument(
            "--repository", required=True, help="The repository that holds the marker's objects."
        )
        parser.add_argument(
            "--remote", default=None, help="Push to this remote instead of changing the repository."
        )
        if name == "seal":
            parser.add_argument("--branch", required=True, help="The declared branch.")
            parser.add_argument("--base", required=True, help="The declared base commit.")
        parser.set_defaults(func=func)
