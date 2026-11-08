# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — declare an operation and publish its evidence to the live hub
"""``synapse operation declare``, ``outcome`` and ``verification``.

Each command connects to the hub as ``--name`` and sends one
``operation_record``. ``declare`` comes from the holder of the live claim,
before the effect, and names the effect's exact identifiers. ``outcome``
publishes a ``synapse.execution-outcome.v1`` report, for example the
``--outcome-file`` of ``synapse worker-session`` or the output of its
``--read-outcome``. ``verification`` publishes a
``synapse.result-verification.v1`` record printed by ``synapse verify-result``.
Both evidence commands need the ``operation-evidence`` grant on the hub.

The command never changes the local file; publishing is separate from
producing the record. Exit codes: 0 accepted, 1 refused, 2 no verdict
(hub unreachable or silent), 4 the hub does not know operation records,
5 the local record could not be read.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import uuid
from pathlib import Path
from typing import Any

from synapse_channel.cli_messaging_types import AgentFactory
from synapse_channel.client.agent import SynapseAgent, default_hub_uri
from synapse_channel.connect_failures import closed_after_ready, describe_connect_failure
from synapse_channel.core.protocol import MessageType
from synapse_channel.terminal_text import terminal_text
from synapse_channel.worker_outcome import OPERATION_ID_ENV

EXIT_ACCEPTED = 0
EXIT_REFUSED = 1
EXIT_NO_VERDICT = 2
EXIT_UNSUPPORTED = 4
EXIT_UNREADABLE = 5

_MAX_RECORD_BYTES = 64 * 1024
_UNKNOWN_TYPE_MARKERS = ("unknown message type", "unsupported message type")


async def publish_operation_record(
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
    """Send one operation record as ``actor`` and print the hub's verdict.

    Parameters
    ----------
    uri, actor : str
        Hub URI and the identity to connect as.
    fields : dict[str, Any]
        The ``operation_record`` fields; a ``request_id`` is added.
    token : str or None
        Shared-secret token for a secured hub.
    ready_timeout, result_timeout : float
        Seconds to await readiness and the verdict.
    json_output : bool
        Print the verdict as JSON.
    agent_factory : AgentFactory, optional
        Client factory; injectable for testing.

    Returns
    -------
    int
        The command's exit code.
    """
    request_id = uuid.uuid4().hex
    replies: list[dict[str, Any]] = []

    async def collect(data: dict[str, Any]) -> None:
        if data.get("type") == MessageType.ERROR or (
            data.get("type") == MessageType.OPERATION_RECORD_RESULT
            and data.get("request_id") == request_id
        ):
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
            return EXIT_NO_VERDICT
        await agent.send_message(
            MessageType.OPERATION_RECORD, target="System", request_id=request_id, **fields
        )
        result = await _await_result(replies, timeout=result_timeout)
        if result is None:
            print("operation record failed: the hub returned no verdict")
            return EXIT_NO_VERDICT
        return _report(result, fields, json_output=json_output)
    finally:
        agent.running = False
        connection.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await connection


async def _await_result(replies: list[dict[str, Any]], *, timeout: float) -> dict[str, Any] | None:
    """Return the first matching verdict or hub error before ``timeout``."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(float(timeout), 0.0)
    while loop.time() <= deadline:
        if replies:
            return replies[0]
        await asyncio.sleep(0.01)
    return None


def _report(result: dict[str, Any], fields: dict[str, Any], *, json_output: bool) -> int:
    """Print one verdict and return the exit code."""
    detail = str(result.get("payload") or "")
    if result.get("type") == MessageType.ERROR:
        unsupported = any(marker in detail.lower() for marker in _UNKNOWN_TYPE_MARKERS)
        rendered: dict[str, Any] = {
            "status": "refused",
            "reason": "unsupported_by_hub" if unsupported else "hub_error",
            "detail": detail or "the hub refused the request",
        }
    else:
        rendered = {
            "status": str(result.get("status") or "refused"),
            "reason": str(result.get("reason") or ""),
            "seq": result.get("seq"),
            "detail": detail,
        }
    rendered.update(
        record=fields["record"], task_id=fields["task_id"], operation_id=fields["operation_id"]
    )
    if json_output:
        print(json.dumps(rendered, indent=2, sort_keys=True))
    elif rendered["status"] == "accepted":
        print(
            f"{rendered['record']} recorded for {rendered['operation_id']} at seq {rendered['seq']}"
        )
    else:
        print(
            f"operation record refused ({rendered['reason']}): {terminal_text(rendered['detail'])}"
        )
    if rendered["status"] == "accepted":
        return EXIT_ACCEPTED
    return EXIT_UNSUPPORTED if rendered["reason"] == "unsupported_by_hub" else EXIT_REFUSED


def _read_record(path: Path) -> dict[str, Any]:
    """Read one JSON record file (or ``-`` is not supported) without changing it."""
    with path.open("rb") as handle:
        data = handle.read(_MAX_RECORD_BYTES + 1)
    if len(data) > _MAX_RECORD_BYTES:
        raise ValueError(f"the record is larger than {_MAX_RECORD_BYTES} bytes")
    document = json.loads(data.decode("utf-8"))
    if not isinstance(document, dict):
        raise ValueError("the record is not a JSON object")
    return document


def _effect_target(args: argparse.Namespace) -> dict[str, str]:
    """Return the declared effect's identifiers from the parsed arguments."""
    names = {
        "file": ("path", "sha256"),
        "git_publication": ("repository", "branch", "revision"),
        "deployment": ("report", "revision"),
        "migration": ("database", "migration", "schema_sha256"),
    }[args.effect]
    missing = [name for name in names if getattr(args, name) is None]
    if missing:
        flags = ", ".join("--" + name.replace("_", "-") for name in missing)
        raise ValueError(f"a {args.effect} declaration needs {flags}")
    return {name: str(getattr(args, name)) for name in names}


def _run(args: argparse.Namespace, fields: dict[str, Any]) -> int:
    """Run one request with the shared connection options."""
    return asyncio.run(
        publish_operation_record(
            uri=args.uri,
            actor=args.name,
            fields=fields,
            token=args.token,
            ready_timeout=args.ready_timeout,
            result_timeout=args.timeout,
            json_output=args.json,
        )
    )


def _cmd_declare(args: argparse.Namespace) -> int:
    """Dispatch ``synapse operation declare``."""
    try:
        target = _effect_target(args)
    except ValueError as exc:
        print(f"operation declare refused: {exc}")
        return EXIT_UNREADABLE
    operation_id = args.operation_id or os.environ.get(OPERATION_ID_ENV, "")
    if not operation_id:
        print(f"operation declare refused: name --operation-id or set {OPERATION_ID_ENV}")
        return EXIT_UNREADABLE
    fields: dict[str, Any] = {
        "record": "declare",
        "task_id": args.task,
        "operation_id": operation_id,
        "epoch": args.epoch,
        "effect": args.effect,
        "effect_target": target,
    }
    if args.budget is not None:
        fields["budget_remaining"] = args.budget
    return _run(args, fields)


def _cmd_evidence(args: argparse.Namespace, record: str, key: str) -> int:
    """Publish one outcome report or verification record file."""
    try:
        document = _read_record(args.file)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        print(f"operation {record} refused: cannot read {args.file}: {type(exc).__name__}")
        return EXIT_UNREADABLE
    operation_id = args.operation_id or document.get("operation_id")
    if not isinstance(operation_id, str) or not operation_id:
        print(f"operation {record} refused: the record names no operation_id")
        return EXIT_UNREADABLE
    fields = {"record": record, "task_id": args.task, "operation_id": operation_id, key: document}
    return _run(args, fields)


def _cmd_outcome(args: argparse.Namespace) -> int:
    """Dispatch ``synapse operation outcome``."""
    return _cmd_evidence(args, "outcome", "report")


def _cmd_verification(args: argparse.Namespace) -> int:
    """Dispatch ``synapse operation verification``."""
    return _cmd_evidence(args, "verification", "verification")


def _non_negative(text: str) -> int:
    """Parse a non-negative integer."""
    try:
        value = int(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{text!r} is not an integer") from exc
    if value < 0:
        raise argparse.ArgumentTypeError("the value must not be negative")
    return value


def _add_common(parser: argparse.ArgumentParser) -> None:
    """Add the arguments every action shares."""
    parser.add_argument("--task", required=True, help="The claimed task the operation belongs to.")
    parser.add_argument("--name", required=True, help="Identity to act as.")
    parser.add_argument("--uri", default=default_hub_uri())
    parser.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    parser.add_argument(
        "--ready-timeout", type=float, default=5.0, help="Seconds to await hub readiness."
    )
    parser.add_argument(
        "--timeout", type=float, default=10.0, help="Seconds to await the hub's verdict."
    )
    parser.add_argument("--json", action="store_true", help="Print the verdict as JSON.")


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``synapse operation declare``, ``outcome`` and ``verification``.

    Parameters
    ----------
    subparsers : argparse._SubParsersAction
        The top-level ``synapse`` subcommand group.
    """
    group = subparsers.add_parser(
        "operation",
        help="Declare an operation on a claimed task and publish its outcome and verification.",
    )
    actions = group.add_subparsers(dest="operation_command", required=True)

    declare = actions.add_parser("declare", help="Declare an operation before its effect.")
    _add_common(declare)
    declare.add_argument(
        "--operation-id",
        default=None,
        help=f"Unique operation identity; defaults to ${OPERATION_ID_ENV}, which "
        "worker-session sets to the id its outcome report carries.",
    )
    declare.add_argument(
        "--epoch", required=True, type=int, help="The claim's epoch or ownership epoch."
    )
    declare.add_argument(
        "--effect",
        required=True,
        choices=("file", "git_publication", "deployment", "migration"),
        help="The effect the operation will have.",
    )
    for flag, help_text in (
        ("--path", "file: the file the work produces"),
        ("--sha256", "file: the requested content SHA-256"),
        ("--repository", "git_publication: remote name or URL, without credentials"),
        ("--branch", "git_publication: the branch on the remote"),
        ("--revision", "git_publication: full commit; deployment: the deployed revision"),
        ("--report", "deployment: the health report the service writes"),
        ("--database", "migration: the database"),
        ("--migration", "migration: the migration identity"),
        ("--schema-sha256", "migration: SHA-256 of the requested schema text"),
    ):
        declare.add_argument(flag, default=None, help=help_text)
    declare.add_argument(
        "--budget", type=_non_negative, default=None, help="Remaining budget, if one applies."
    )
    declare.set_defaults(func=_cmd_declare)

    for name, help_text, func in (
        ("outcome", "Publish an execution-outcome report file.", _cmd_outcome),
        ("verification", "Publish a result-verification record file.", _cmd_verification),
    ):
        evidence = actions.add_parser(name, help=help_text)
        _add_common(evidence)
        evidence.add_argument("file", type=Path, help="The JSON record; it is not changed.")
        evidence.add_argument(
            "--operation-id", default=None, help="Defaults to the record's own operation_id."
        )
        evidence.set_defaults(func=func)
