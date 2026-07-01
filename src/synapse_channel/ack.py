# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — `syn ack` task-evidence completion command
"""Evidence-first task completion for the ergonomic ``syn`` command.

``syn ack`` is a short, identity-correct wrapper for a common coordination closeout:
post the evidence or artifact that proves a task is complete, then move the shared
blackboard task to ``done``. It deliberately reuses the existing hub protocol
(``ledger_progress`` plus ``ledger_task_update``) rather than adding a second task
completion model. The command only reports success after both hub confirmations
arrive, so supervisors can trust the printed acknowledgement as a real board
mutation rather than a local echo.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import sys
from collections.abc import Callable, Sequence
from typing import Any, Protocol

from synapse_channel.client.agent import SynapseAgent, default_hub_uri
from synapse_channel.connect_failures import describe_connect_failure
from synapse_channel.core.protocol import MessageType

ACK_PROGRESS_KIND = "assessment"
"""Progress-note kind used for completion evidence."""

DEFAULT_CONFIRMATION_ATTEMPTS = 60
"""Default 50 ms polling attempts for each hub confirmation."""


class AckIdentity(Protocol):
    """Identity contract supplied by :mod:`synapse_channel.ergonomics`.

    Attributes
    ----------
    project : str
        Bare project name for the coordination scope.
    identity : str
        Full sender identity used to author the progress note and task update.
    """

    @property
    def project(self) -> str:  # pragma: no cover
        """Return the bare project name."""
        ...

    @property
    def identity(self) -> str:  # pragma: no cover
        """Return the full sender identity."""
        ...


class AckAgent(Protocol):
    """Client methods required by :func:`ack_task`.

    Attributes
    ----------
    running : bool
        Set to ``False`` during cleanup to stop the connection loop.
    """

    running: bool
    last_close_code: int | None
    last_close_reason: str

    async def connect(self) -> None:
        """Connect to the hub and process inbound messages."""

    async def wait_until_ready(self, timeout: float = 5.0) -> bool:
        """Return whether the hub welcome arrived before ``timeout``."""

    async def post_progress(self, task_id: str, text: str, *, kind: str = "note") -> None:
        """Append a progress note for ``task_id``."""

    async def update_ledger_task(
        self,
        task_id: str,
        *,
        status: str | None = None,
        suggested_owner: str | None = None,
    ) -> None:
        """Update the shared-board task status or suggested owner."""


AgentFactory = Callable[..., AckAgent]
"""Factory type for constructing the acknowledgement client."""


def _clean_values(values: Sequence[str]) -> tuple[str, ...]:
    """Return stripped non-empty strings from a repeatable CLI option."""
    return tuple(value.strip() for value in values if value.strip())


def build_ack_text(
    *,
    evidence: Sequence[str],
    artifacts: Sequence[str],
    note: str,
) -> str:
    """Build the progress-note text for a task acknowledgement.

    Parameters
    ----------
    evidence : Sequence[str]
        Commands, checks, or human-verifiable facts that prove completion.
    artifacts : Sequence[str]
        Files, URLs, or generated outputs that preserve the completion evidence.
    note : str
        Optional human note appended after the evidence/artifact sections.

    Returns
    -------
    str
        Multi-line progress text suitable for a hub ``assessment`` note.

    Raises
    ------
    ValueError
        If neither evidence nor artifact text is supplied.
    """
    clean_evidence = _clean_values(evidence)
    clean_artifacts = _clean_values(artifacts)
    clean_note = note.strip()
    if not clean_evidence and not clean_artifacts:
        raise ValueError("syn ack requires at least one --evidence or --artifact value.")

    lines: list[str] = []
    if clean_evidence:
        lines.append(f"ack evidence: {'; '.join(clean_evidence)}")
    if clean_artifacts:
        lines.append(f"ack artifacts: {'; '.join(clean_artifacts)}")
    if clean_note:
        lines.append(f"ack note: {clean_note}")
    return "\n".join(lines)


async def _wait_for(
    messages: list[dict[str, Any]],
    predicate: Callable[[dict[str, Any]], bool],
    *,
    attempts: int,
) -> dict[str, Any] | None:
    """Poll collected hub messages until one matches ``predicate``."""
    for _ in range(max(attempts, 1)):
        for message in list(messages):
            if predicate(message):
                return message
        await asyncio.sleep(0.05)
    return None


def _is_error_for(message: dict[str, Any], identity: AckIdentity) -> bool:
    """Return whether a hub error message targets this acknowledgement sender."""
    return message.get("type") == MessageType.ERROR and message.get("target") in {
        identity.identity,
        identity.project,
        "all",
        None,
    }


def _message_text(message: dict[str, Any]) -> str:
    """Return the human text carried by a hub message."""
    payload = message.get("payload")
    text = message.get("text")
    if isinstance(payload, str) and payload:
        return payload
    if isinstance(text, str) and text:
        return text
    return str(message)


async def ack_task(
    identity: AckIdentity,
    *,
    task_id: str,
    evidence: Sequence[str],
    artifacts: Sequence[str],
    note: str,
    uri: str,
    token: str | None = None,
    ready_timeout: float = 5.0,
    attempts: int = DEFAULT_CONFIRMATION_ATTEMPTS,
    agent_factory: AgentFactory = SynapseAgent,
) -> int:
    """Post completion evidence for ``task_id`` and mark the task ``done``.

    Parameters
    ----------
    identity : AckIdentity
        Resolved ``syn`` identity used as the hub author.
    task_id : str
        Shared-board task id to acknowledge.
    evidence : Sequence[str]
        Repeatable evidence values recorded in an ``assessment`` progress note.
    artifacts : Sequence[str]
        Repeatable artifact paths or URLs recorded beside the evidence.
    note : str
        Optional human note appended to the progress text.
    uri : str
        Hub WebSocket URI.
    token : str or None, optional
        Shared-secret token for a secured hub.
    ready_timeout : float, optional
        Seconds to wait for hub readiness before reporting unreachable.
    attempts : int, optional
        50 ms polling attempts for each hub confirmation.
    agent_factory : AgentFactory, optional
        Client factory for tests and embedding code.

    Returns
    -------
    int
        ``0`` after progress and ``done`` confirmations; ``1`` on hub failure or
        missing confirmation.
    """
    task = task_id.strip()
    if not task:
        print("syn ack: task id is required.", file=sys.stderr)
        return 2
    progress_text = build_ack_text(evidence=evidence, artifacts=artifacts, note=note)
    messages: list[dict[str, Any]] = []

    async def collect(data: dict[str, Any]) -> None:
        if data.get("type") in {
            MessageType.LEDGER_PROGRESS_POSTED,
            MessageType.LEDGER_TASK_UPDATED,
            MessageType.ERROR,
        }:
            messages.append(data)

    agent = agent_factory(identity.identity, collect, uri=uri, verbose=False, token=token)
    connection_task = asyncio.create_task(agent.connect())
    try:
        if not await agent.wait_until_ready(timeout=ready_timeout):
            print(
                describe_connect_failure(
                    identity.identity,
                    uri,
                    close_code=agent.last_close_code,
                    close_reason=agent.last_close_reason,
                )
            )
            return 1

        await agent.post_progress(task, progress_text, kind=ACK_PROGRESS_KIND)
        progress = await _wait_for(
            messages,
            lambda data: (
                (
                    data.get("type") == MessageType.LEDGER_PROGRESS_POSTED
                    and isinstance(data.get("note"), dict)
                    and data["note"].get("task_id") == task
                    and data["note"].get("author") == identity.identity
                )
                or _is_error_for(data, identity)
            ),
            attempts=attempts,
        )
        if progress is None:
            print(f"ack failed for {task}: no progress confirmation from hub.", file=sys.stderr)
            return 1
        if progress.get("type") == MessageType.ERROR:
            print(f"ack failed for {task}: {_message_text(progress)}", file=sys.stderr)
            return 1

        await agent.update_ledger_task(task, status="done")
        updated = await _wait_for(
            messages,
            lambda data: (
                (
                    data.get("type") == MessageType.LEDGER_TASK_UPDATED
                    and isinstance(data.get("task"), dict)
                    and data["task"].get("task_id") == task
                    and data["task"].get("status") == "done"
                )
                or _is_error_for(data, identity)
            ),
            attempts=attempts,
        )
        if updated is None:
            print(f"ack failed for {task}: no done confirmation from hub.", file=sys.stderr)
            return 1
        if updated.get("type") == MessageType.ERROR:
            print(f"ack failed for {task}: {_message_text(updated)}", file=sys.stderr)
            return 1

        print(f"acked {task} -> status=done")
        return 0
    finally:
        agent.running = False
        connection_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await connection_task


def build_parser() -> argparse.ArgumentParser:
    """Build the ``syn ack`` parser."""
    parser = argparse.ArgumentParser(
        prog="syn ack",
        description="Post task completion evidence and mark the board task done.",
        allow_abbrev=False,
    )
    parser.add_argument("task_id", help="Shared-board task id to acknowledge.")
    parser.add_argument(
        "--evidence",
        action="append",
        default=[],
        help="Completion evidence such as a test command or verifier output (repeatable).",
    )
    parser.add_argument(
        "--artifact",
        dest="artifacts",
        action="append",
        default=[],
        help="Evidence artifact path or URL (repeatable).",
    )
    parser.add_argument("--note", default="", help="Optional note appended to the evidence.")
    parser.add_argument("--uri", default=default_hub_uri(), help="Hub WebSocket URI.")
    parser.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    parser.add_argument(
        "--ready-timeout",
        type=float,
        default=5.0,
        help="Seconds to wait for the hub handshake.",
    )
    return parser


def main(identity: AckIdentity, argv: Sequence[str] | None = None) -> int:
    """Parse and run ``syn ack`` for a resolved ergonomic identity."""
    args = build_parser().parse_args(sys.argv[1:] if argv is None else list(argv))
    try:
        build_ack_text(evidence=args.evidence, artifacts=args.artifacts, note=args.note)
    except ValueError as exc:
        print(f"syn ack: {exc}", file=sys.stderr)
        return 2
    return asyncio.run(
        ack_task(
            identity,
            task_id=args.task_id,
            evidence=args.evidence,
            artifacts=args.artifacts,
            note=args.note,
            uri=args.uri,
            token=args.token,
            ready_timeout=args.ready_timeout,
        )
    )
