# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — stop and resume a scope of operations on the live hub
"""``synapse operation stop`` and ``synapse operation resume``.

A stop names one scope (``installation``, ``project:<namespace>``,
``effect:<kind>`` or ``operation:<id>``) and a reason. While the scope is
stopped the hub opens no attempt of an operation it contains and admits no
approval of one. The stop record lists the attempts that were open when it was
made; it does not end them. A resume names the scope, a reason and who resumes:
``another_person`` than the stopper, or the ``only_person`` there is, as the
resumer states it.

Every call carries a request key. Without ``--idem-key`` the command makes a
new one and prints it, so that a request whose answer was lost can be asked
again with the same key and receive the stored answer.

Exit codes: 0 the stop or resume is recorded; 1 any other answer; 2 no verdict
(hub unreachable or silent); 4 the hub does not know the request.
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
from synapse_channel.core.operation_stops import RESUME_CASES, RESUMED, STOP_RECORDED
from synapse_channel.core.protocol import MessageType
from synapse_channel.terminal_text import terminal_text

EXIT_RECORDED = 0
EXIT_REFUSED = 1
EXIT_NO_VERDICT = 2
EXIT_UNSUPPORTED = 4

_UNKNOWN_TYPE_MARKERS = ("unknown message type", "unsupported message type")
_RECORDED = (STOP_RECORDED, RESUMED)


async def request_stop(
    *,
    uri: str,
    actor: str,
    message_type: str,
    fields: dict[str, Any],
    token: str | None,
    ready_timeout: float,
    result_timeout: float,
    json_output: bool,
    agent_factory: AgentFactory = SynapseAgent,
) -> int:
    """Send one ``operation_stop`` or ``operation_resume`` as ``actor`` and print the answer.

    Parameters
    ----------
    uri, actor : str
        Hub URI and the identity to connect as.
    message_type : str
        ``operation_stop`` or ``operation_resume``.
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
    outcome = await ask_hub(
        uri=uri,
        actor=actor,
        message_type=message_type,
        fields=fields,
        answers=lambda data: (
            data.get("type") == MessageType.OPERATION_STOP_RESULT
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
            else "operation stop failed: the hub returned no answer"
        )
        print(f"request key: {fields['idem_key']}")
        return EXIT_NO_VERDICT
    return _report(outcome.reply, fields, json_output=json_output)


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
    rendered: dict[str, Any] = {
        "answer": answer,
        "detail": detail,
        "scope": fields["scope"],
        "seq": result.get("seq"),
        "replayed": bool(live.get("replayed", False)),
        "request_key": fields["idem_key"],
    }
    for name in ("epoch", "listed", "lifts_seq", "standing_seq", "resumed_from", "warning"):
        if name in result:
            rendered[name] = result[name]
    return rendered


def _report(result: dict[str, Any], fields: dict[str, Any], *, json_output: bool) -> int:
    """Print one answer and return the exit code."""
    rendered = _rendered(result, fields)
    if json_output:
        print(json.dumps(rendered, indent=2, sort_keys=True))
    else:
        replay = " (repeated request)" if rendered["replayed"] else ""
        print(
            f"operation stop {rendered['answer']} for {terminal_text(rendered['scope'])}{replay}: "
            f"{terminal_text(rendered['detail'])}"
        )
        if rendered["answer"] in _RECORDED:
            print(f"journal seq: {rendered['seq']}")
        for item in rendered.get("listed") or []:
            print(
                f"listed attempt {terminal_text(str(item.get('attempt_id')))} of "
                f"{terminal_text(str(item.get('operation_id')))} "
                f"by {terminal_text(str(item.get('executor')))}"
            )
        if rendered.get("warning"):
            print(f"warning: {terminal_text(str(rendered['warning']))}")
        print(f"request key: {rendered['request_key']}")
    if rendered["answer"] == "unsupported_by_hub":
        return EXIT_UNSUPPORTED
    return EXIT_RECORDED if rendered["answer"] in _RECORDED else EXIT_REFUSED


def _run(args: argparse.Namespace, message_type: str, fields: dict[str, Any]) -> int:
    fields["idem_key"] = args.idem_key or uuid.uuid4().hex
    return asyncio.run(
        request_stop(
            uri=args.uri,
            actor=args.name,
            message_type=message_type,
            fields=fields,
            token=args.token,
            ready_timeout=args.ready_timeout,
            result_timeout=args.timeout,
            json_output=args.json,
        )
    )


def cmd_stop(args: argparse.Namespace) -> int:
    """Dispatch ``synapse operation stop``."""
    return _run(args, MessageType.OPERATION_STOP, {"scope": args.scope, "reason": args.reason})


def cmd_resume(args: argparse.Namespace) -> int:
    """Dispatch ``synapse operation resume``."""
    fields = {"scope": args.scope, "reason": args.reason, "case": args.case}
    return _run(args, MessageType.OPERATION_RESUME, fields)


def add_stop_parsers(
    actions: argparse._SubParsersAction[argparse.ArgumentParser],
    add_connection: Callable[[argparse.ArgumentParser], None],
) -> None:
    """Register ``synapse operation stop`` and ``resume`` with the connection arguments.

    Parameters
    ----------
    actions : argparse._SubParsersAction
        The ``synapse operation`` action group.
    add_connection : Callable
        Adds the identity and connection arguments every action shares.
    """
    scope_help = "installation, project:<namespace>, effect:<kind> or operation:<id>."
    stop = actions.add_parser(
        "stop", help="Stop a scope: no new attempt and no new approval until a resume."
    )
    resume = actions.add_parser(
        "resume", help="Lift the stop of a scope; needs operation-resume on it."
    )
    for parser in (stop, resume):
        add_connection(parser)
        parser.add_argument("--scope", required=True, help=scope_help)
        parser.add_argument("--reason", required=True, help="Why, in 1 to 512 characters.")
        parser.add_argument(
            "--idem-key",
            default=None,
            help="Repeat a request whose answer was lost; by default a new key is made.",
        )
    resume.add_argument(
        "--case",
        required=True,
        choices=RESUME_CASES,
        help="Who resumes: another_person than the stopper, or the only_person there is.",
    )
    stop.set_defaults(func=cmd_stop)
    resume.set_defaults(func=cmd_resume)
