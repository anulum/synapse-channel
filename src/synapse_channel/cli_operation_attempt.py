# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — ask the live hub to open an attempt of an approved operation
"""``synapse operation attempt``: ask before an effect, start only on a fresh approval.

The executor that holds the live claim names the operation, the claim epoch and
the digest of the intent it is about to carry out. The hub answers once, from a
closed set, journals the answer and, for ``approved_fresh``, opens the attempt
and consumes the approval.

Every call carries a request key. Without ``--idem-key`` the command makes a
new one and prints it, also on a refusal, so that a request whose answer was
lost can be asked again with the same key: the hub then returns the stored
answer and the attempt's present state, and opens nothing.

Exit codes: 0 the attempt is open and the effect may start; 1 any other
answer; 2 no verdict (hub unreachable or silent); 4 the hub does not know the
request.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import uuid
from collections.abc import Callable
from typing import Any

from synapse_channel.cli_messaging_types import AgentFactory
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.connect_failures import closed_after_ready, describe_connect_failure
from synapse_channel.core.protocol import MessageType
from synapse_channel.terminal_text import terminal_text

EXIT_OPEN = 0
EXIT_REFUSED = 1
EXIT_NO_VERDICT = 2
EXIT_UNSUPPORTED = 4

_UNKNOWN_TYPE_MARKERS = ("unknown message type", "unsupported message type")


async def request_attempt(
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
    """Send one ``operation_attempt`` as ``actor`` and print the hub's answer.

    Parameters
    ----------
    uri, actor : str
        Hub URI and the identity to connect as.
    fields : dict[str, Any]
        The request fields, ``idem_key`` included.
    token : str or None
        Shared-secret token for a secured hub.
    ready_timeout, result_timeout : float
        Seconds to await readiness and the answer.
    json_output : bool
        Print the answer as JSON.
    agent_factory : AgentFactory, optional
        Client factory; injectable for testing.

    Returns
    -------
    int
        The command's exit code.
    """
    replies: list[dict[str, Any]] = []

    async def collect(data: dict[str, Any]) -> None:
        if data.get("type") == MessageType.ERROR or (
            data.get("type") == MessageType.OPERATION_ATTEMPT_RESULT
            and data.get("request_key") == fields["idem_key"]
        ):
            replies.append(data)

    agent = agent_factory(actor, collect, uri=uri, verbose=False, token=token)
    connection = asyncio.create_task(agent.connect())
    try:
        if not await agent.wait_until_ready(timeout=ready_timeout) or await closed_after_ready(
            agent, timeout=ready_timeout
        ):
            failure = describe_connect_failure(
                actor, uri, close_code=agent.last_close_code, close_reason=agent.last_close_reason
            )
            print(terminal_text(failure))
            print(f"request key: {fields['idem_key']}")
            return EXIT_NO_VERDICT
        await agent.send_message(MessageType.OPERATION_ATTEMPT, target="System", **fields)
        result = await _first_reply(replies, timeout=result_timeout)
        if result is None:
            print("operation attempt failed: the hub returned no answer")
            print(f"request key: {fields['idem_key']}")
            return EXIT_NO_VERDICT
        return _report(result, fields, json_output=json_output)
    finally:
        agent.running = False
        connection.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await connection


async def _first_reply(replies: list[dict[str, Any]], *, timeout: float) -> dict[str, Any] | None:
    """Return the first matching answer or hub error before ``timeout``."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(float(timeout), 0.0)
    while loop.time() <= deadline:
        if replies:
            return replies[0]
        await asyncio.sleep(0.01)
    return None


def _rendered(result: dict[str, Any], fields: dict[str, Any]) -> dict[str, Any]:
    """Return the answer as the command prints it."""
    detail = str(result.get("payload") or "")
    if result.get("type") == MessageType.ERROR:
        unsupported = any(marker in detail.lower() for marker in _UNKNOWN_TYPE_MARKERS)
        answer = (
            "unsupported_by_hub" if unsupported else str(result.get("error_code") or "hub_error")
        )
        live: dict[str, Any] = {}
    else:
        answer = str(result.get("answer") or "")
        raw = result.get("live")
        live = raw if isinstance(raw, dict) else {}
    return {
        "answer": answer,
        "detail": detail,
        "seq": result.get("seq"),
        "attempt_id": str(result.get("attempt_id") or ""),
        "approval_seq": result.get("approval_seq"),
        "attempt_state": str(live.get("attempt_state") or "none"),
        "replayed": bool(live.get("replayed", False)),
        "task_id": fields["task_id"],
        "operation_id": fields["operation_id"],
        "request_key": fields["idem_key"],
    }


def _report(result: dict[str, Any], fields: dict[str, Any], *, json_output: bool) -> int:
    """Print one answer and return the exit code."""
    rendered = _rendered(result, fields)
    if json_output:
        print(json.dumps(rendered, indent=2, sort_keys=True))
    else:
        replay = " (repeated request)" if rendered["replayed"] else ""
        print(
            f"operation attempt {rendered['answer']} for {rendered['operation_id']}{replay}: "
            f"{terminal_text(rendered['detail'])}"
        )
        if rendered["attempt_id"]:
            print(f"attempt {rendered['attempt_id']} is {rendered['attempt_state']}")
        print(f"request key: {rendered['request_key']}")
    if rendered["answer"] == "unsupported_by_hub":
        return EXIT_UNSUPPORTED
    opened = rendered["answer"] == "approved_fresh" and rendered["attempt_state"] == "open"
    return EXIT_OPEN if opened else EXIT_REFUSED


def cmd_attempt(args: argparse.Namespace) -> int:
    """Dispatch ``synapse operation attempt``."""
    fields: dict[str, Any] = {
        "task_id": args.task,
        "operation_id": args.operation_id,
        "epoch": args.epoch,
        "intent_sha256": args.intent_sha256,
        "idem_key": args.idem_key or uuid.uuid4().hex,
    }
    return asyncio.run(
        request_attempt(
            uri=args.uri,
            actor=args.name,
            fields=fields,
            token=args.token,
            ready_timeout=args.ready_timeout,
            result_timeout=args.timeout,
            json_output=args.json,
        )
    )


def add_attempt_parser(
    actions: argparse._SubParsersAction[argparse.ArgumentParser],
    add_common: Callable[[argparse.ArgumentParser], None],
) -> None:
    """Register ``synapse operation attempt`` with the shared connection arguments.

    Parameters
    ----------
    actions : argparse._SubParsersAction
        The ``synapse operation`` action group.
    add_common : Callable
        Adds the arguments every ``synapse operation`` action shares.
    """
    parser = actions.add_parser(
        "attempt", help="Ask the hub to open an attempt of an approved operation before its effect."
    )
    add_common(parser)
    parser.add_argument("--operation-id", required=True, help="The declared operation.")
    parser.add_argument(
        "--epoch", required=True, type=int, help="The claim's epoch or ownership epoch."
    )
    parser.add_argument(
        "--intent-sha256",
        required=True,
        help="The digest of the intent about to be carried out, as the declaration printed it.",
    )
    parser.add_argument(
        "--idem-key",
        default=None,
        help="Repeat a request whose answer was lost; by default a new key is made and printed.",
    )
    parser.set_defaults(func=cmd_attempt)
