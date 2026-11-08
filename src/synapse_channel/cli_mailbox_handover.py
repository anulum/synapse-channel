# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — hand a mailbox's pending mail on, or close it, through the live hub
"""``synapse mailbox handover`` and ``synapse mailbox close``.

Both connect to the hub as ``--name`` and send one ``mailbox_handover`` request.
The hub decides: the name itself, its custodian and an operator granted
``mailbox-handover`` may act. ``handover`` re-addresses the pending mail to
``--to``; ``close`` advances the mailbox with the reason and delivers nothing.
One request covers at most a bounded number of messages; when more remain the
command says so and exits 3, and running it again continues.

Exit codes: 0 applied or already applied, 1 refused, 2 no verdict, 3 applied
with more mail remaining.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from typing import Any

from synapse_channel.cli_messaging_types import AgentFactory
from synapse_channel.client.agent import SynapseAgent, default_hub_uri
from synapse_channel.connect_failures import closed_after_ready, describe_connect_failure
from synapse_channel.core.protocol import MessageType
from synapse_channel.terminal_text import terminal_text

EXIT_APPLIED = 0
EXIT_REFUSED = 1
EXIT_NO_VERDICT = 2
EXIT_MORE_REMAINS = 3


class HandoverConnection:
    """One live hub connection that sends handover requests and awaits each verdict."""

    def __init__(self, agent: SynapseAgent, replies: list[dict[str, Any]]) -> None:
        self._agent = agent
        self._replies = replies

    async def request(self, fields: dict[str, Any], *, timeout: float) -> dict[str, Any] | None:
        """Send one ``mailbox_handover`` request and return its verdict or hub error.

        Parameters
        ----------
        fields : dict[str, Any]
            The request's ``from``, ``reason`` and optional ``to`` and ``through_seq``.
        timeout : float
            Seconds to wait for the answer.

        Returns
        -------
        dict[str, Any] or None
            The ``mailbox_handover_result`` or ``error`` frame, or ``None`` when the
            hub answered nothing in time.
        """
        self._replies.clear()
        await self._agent.send_message(MessageType.MAILBOX_HANDOVER, target="System", **fields)
        return await _await_result(self._replies, timeout=timeout)


@contextlib.asynccontextmanager
async def handover_connection(
    *,
    uri: str,
    actor: str,
    token: str | None,
    ready_timeout: float,
    agent_factory: AgentFactory = SynapseAgent,
) -> AsyncIterator[HandoverConnection | None]:
    """Connect as ``actor`` for handover requests; yield ``None`` after a printed failure."""
    replies: list[dict[str, Any]] = []

    async def collect(data: dict[str, Any]) -> None:
        if data.get("type") in {MessageType.MAILBOX_HANDOVER_RESULT, MessageType.ERROR}:
            replies.append(data)

    agent = agent_factory(actor, collect, uri=uri, verbose=False, token=token)
    connection = asyncio.create_task(agent.connect())
    try:
        if not await agent.wait_until_ready(timeout=ready_timeout) or await closed_after_ready(
            agent
        ):
            print(
                terminal_text(
                    describe_connect_failure(
                        actor,
                        uri,
                        close_code=agent.last_close_code,
                        close_reason=agent.last_close_reason,
                    )
                )
            )
            yield None
            return
        yield HandoverConnection(agent, replies)
    finally:
        agent.running = False
        connection.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await connection


async def mailbox_handover(
    *,
    uri: str,
    actor: str,
    fields: dict[str, Any],
    token: str | None,
    ready_timeout: float,
    result_timeout: float,
    json_output: bool,
    agent_factory: AgentFactory = SynapseAgent,
) -> int:
    """Send one handover or close request as ``actor`` and print the hub's verdict."""
    async with handover_connection(
        uri=uri, actor=actor, token=token, ready_timeout=ready_timeout, agent_factory=agent_factory
    ) as connection:
        if connection is None:
            return EXIT_NO_VERDICT
        result = await connection.request(fields, timeout=result_timeout)
        if result is None:
            print("mailbox handover failed: the hub returned no verdict")
            return EXIT_NO_VERDICT
        return _report(result, fields, json_output=json_output)


async def _await_result(replies: list[dict[str, Any]], *, timeout: float) -> dict[str, Any] | None:
    """Return the first verdict or hub error before ``timeout``."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(float(timeout), 0.0)
    while loop.time() <= deadline:
        if replies:
            return replies[0]
        await asyncio.sleep(0.01)
    return None


def render_verdict(result: dict[str, Any], fields: dict[str, Any]) -> dict[str, Any]:
    """Return the normalised verdict of one request: status, reason, counts and detail."""
    if result.get("type") == MessageType.ERROR:
        return {
            "status": "refused",
            "reason": "hub_error",
            "mailbox": fields["from"],
            "detail": str(result.get("payload") or "the hub refused the request"),
        }
    return {
        "status": str(result.get("status") or "refused"),
        "reason": str(result.get("reason") or ""),
        "mailbox": str(result.get("mailbox") or fields["from"]),
        "successor": result.get("successor"),
        "through_seq": result.get("through_seq"),
        "forwarded": int(result.get("forwarded") or 0),
        "remaining": bool(result.get("remaining")),
        "detail": str(result.get("payload") or ""),
    }


def _report(result: dict[str, Any], fields: dict[str, Any], *, json_output: bool) -> int:
    """Print one verdict and return the exit code."""
    rendered = render_verdict(result, fields)
    if json_output:
        print(json.dumps(rendered, indent=2, sort_keys=True))
    elif rendered["status"] == "refused":
        print(
            f"mailbox handover refused ({rendered['reason']}): {terminal_text(rendered['detail'])}"
        )
    else:
        print(terminal_text(rendered["detail"]))
    if rendered["status"] == "refused":
        return EXIT_REFUSED
    return EXIT_MORE_REMAINS if rendered.get("remaining") else EXIT_APPLIED


def _fields(args: argparse.Namespace, *, successor: str | None) -> dict[str, Any]:
    """Build the request fields from parsed arguments."""
    fields: dict[str, Any] = {"from": args.mailbox, "reason": args.reason}
    if successor is not None:
        fields["to"] = successor
    if args.through is not None:
        fields["through_seq"] = args.through
    return fields


def _run(args: argparse.Namespace, fields: dict[str, Any]) -> int:
    """Run one request with the shared connection options."""
    return asyncio.run(
        mailbox_handover(
            uri=args.uri,
            actor=args.name,
            fields=fields,
            token=args.token,
            ready_timeout=args.ready_timeout,
            result_timeout=args.timeout,
            json_output=args.json,
        )
    )


def _cmd_handover(args: argparse.Namespace) -> int:
    """Dispatch ``synapse mailbox handover``."""
    return _run(args, _fields(args, successor=args.to))


def _cmd_close(args: argparse.Namespace) -> int:
    """Dispatch ``synapse mailbox close``."""
    return _run(args, _fields(args, successor=None))


def _positive_seq(text: str) -> int:
    """Parse a positive journal sequence."""
    try:
        value = int(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{text!r} is not a sequence number") from exc
    if value < 1:
        raise argparse.ArgumentTypeError("the sequence must be positive")
    return value


def _add_common(parser: argparse.ArgumentParser) -> None:
    """Add the arguments both actions share."""
    parser.add_argument("mailbox", help="The name whose pending mail this acts on.")
    parser.add_argument(
        "--name",
        required=True,
        help="Identity to act as: the name itself, its custodian or a granted operator.",
    )
    parser.add_argument("--reason", required=True, help="Reason written to the journal.")
    parser.add_argument(
        "--through",
        type=_positive_seq,
        default=None,
        metavar="SEQ",
        help="Cover pending mail up to this journal sequence (default: all of it now).",
    )
    parser.add_argument("--uri", default=default_hub_uri())
    parser.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    parser.add_argument(
        "--ready-timeout", type=float, default=5.0, help="Seconds to await hub readiness."
    )
    parser.add_argument(
        "--timeout", type=float, default=10.0, help="Seconds to await the hub's verdict."
    )
    parser.add_argument("--json", action="store_true", help="Print the verdict as JSON.")


def add_handover_parsers(actions: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``mailbox handover`` and ``mailbox close``."""
    handover = actions.add_parser(
        "handover", help="Re-address a name's pending mail to a successor through the hub."
    )
    _add_common(handover)
    handover.add_argument("--to", required=True, help="The successor name.")
    handover.set_defaults(func=_cmd_handover)
    close = actions.add_parser(
        "close", help="Close a name's pending mail with a reason, delivering nothing."
    )
    _add_common(close)
    close.set_defaults(func=_cmd_close)
