# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — set a scope's autonomy level by hand on the live hub
"""``synapse operation level``: set a scope's autonomy level by hand.

A scope (``installation``, ``project:<namespace>``, ``effect:<kind>`` or
``operation:<id>``) has a level from ``A1`` to ``A5``; without a record it is
``A1``. Setting a level needs ``operation-level`` on the scope, also on an open
hub. A raise is refused while the scope, or one containing it, is stopped. A
change of level makes every automated approval from before it stale; a
person's approval stands.

Every call carries a request key. Without ``--idem-key`` the command makes a
new one and prints it, so that a request whose answer was lost can be asked
again with the same key and receive the stored answer.

Exit codes: 0 the level is recorded or already in force; 1 any other answer;
2 no verdict (hub unreachable or silent); 4 the hub does not know the request.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from collections.abc import Callable
from typing import Any

from synapse_channel.cli_hub_request import ask_hub
from synapse_channel.cli_messaging_types import AgentFactory
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.operation_levels import ALREADY_AT_LEVEL, LEVEL_RECORDED, LEVELS
from synapse_channel.core.protocol import MessageType
from synapse_channel.terminal_text import terminal_text

EXIT_RECORDED = 0
EXIT_REFUSED = 1
EXIT_NO_VERDICT = 2
EXIT_UNSUPPORTED = 4

_UNKNOWN_TYPE_MARKERS = ("unknown message type", "unsupported message type")


async def request_level(
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
    """Send one ``operation_level`` as ``actor``, print the answer and return the exit code."""
    outcome = await ask_hub(
        uri=uri,
        actor=actor,
        message_type=MessageType.OPERATION_LEVEL,
        fields=fields,
        answers=lambda data: (
            data.get("type") == MessageType.OPERATION_LEVEL_RESULT
            and data.get("request_key") == fields["idem_key"]
        ),
        token=token,
        ready_timeout=ready_timeout,
        result_timeout=result_timeout,
        agent_factory=agent_factory,
    )
    if outcome.reply is None:
        print(
            terminal_text(outcome.failure)
            if outcome.failure
            else "operation level failed: the hub returned no answer"
        )
        print(f"request key: {fields['idem_key']}")
        return EXIT_NO_VERDICT
    result = outcome.reply
    detail = str(result.get("payload") or "")
    if result.get("type") == MessageType.ERROR:
        unsupported = any(marker in detail.lower() for marker in _UNKNOWN_TYPE_MARKERS)
        answer = (
            "unsupported_by_hub" if unsupported else str(result.get("error_code") or "hub_error")
        )
    else:
        answer = str(result.get("answer") or "")
    rendered = {
        "answer": answer,
        "detail": detail,
        "scope": fields["scope"],
        "level": fields["level"],
        "seq": result.get("seq"),
        "request_key": fields["idem_key"],
    }
    if json_output:
        print(json.dumps(rendered, indent=2, sort_keys=True))
    else:
        print(
            f"operation level {answer} for {terminal_text(fields['scope'])}: "
            f"{terminal_text(detail)}"
        )
        print(f"request key: {fields['idem_key']}")
    if answer == "unsupported_by_hub":
        return EXIT_UNSUPPORTED
    return EXIT_RECORDED if answer in (LEVEL_RECORDED, ALREADY_AT_LEVEL) else EXIT_REFUSED


def cmd_level(args: argparse.Namespace) -> int:
    """Dispatch ``synapse operation level``."""
    fields = {
        "scope": args.scope,
        "level": args.level,
        "reason": args.reason,
        "idem_key": args.idem_key or uuid.uuid4().hex,
    }
    return asyncio.run(
        request_level(
            uri=args.uri,
            actor=args.name,
            fields=fields,
            token=args.token,
            ready_timeout=args.ready_timeout,
            result_timeout=args.timeout,
            json_output=args.json,
        )
    )


def add_level_parser(
    actions: argparse._SubParsersAction[argparse.ArgumentParser],
    add_connection: Callable[[argparse.ArgumentParser], None],
) -> None:
    """Register ``synapse operation level`` with the identity and connection arguments."""
    parser = actions.add_parser(
        "level", help="Set a scope's autonomy level by hand; needs operation-level on it."
    )
    add_connection(parser)
    parser.add_argument(
        "--scope",
        required=True,
        help="installation, project:<namespace>, effect:<kind> or operation:<id>.",
    )
    parser.add_argument("--level", required=True, choices=LEVELS, help="The level to set.")
    parser.add_argument("--reason", required=True, help="Why, in 1 to 512 characters.")
    parser.add_argument(
        "--idem-key",
        default=None,
        help="Repeat a request whose answer was lost; by default a new key is made.",
    )
    parser.set_defaults(func=cmd_level)
