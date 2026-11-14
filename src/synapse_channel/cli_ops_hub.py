# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the opt-in hub path of ops run: declare, then one approved attempt
"""The opt-in hub path of ``synapse ops run``.

With ``--hub``, a run goes through the hub's governed operations in two calls:

1. ``--declare`` plans the row, builds its authorisation intent from the plan
   (:mod:`synapse_channel.operations.hub_intent`) and declares the operation on
   the seat's claimed task. It prints the hub's answer with the intent's digest,
   which another identity of the project approves. Nothing runs.
2. ``--intent-sha256`` plans again and composes the whole intent locally. A digest
   other than the approved one is refused before the hub is asked. The local
   grants are checked, so a missing permission never consumes an approval. Then
   the hub is asked for one attempt of that exact intent, and the run starts only
   when the attempt is open. After the run, its outcome is published to the hub,
   which ends the attempt.

Both authorities are required: the local grants file and the hub's attempt. The
hub path only narrows what the operator allows on this host.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from synapse_channel.cli_hub_request import HubReply, ask_hub
from synapse_channel.core.authorisation_intent import compose_intent, parse_intent_statement
from synapse_channel.core.execution_outcome import ExecutionOutcome, OutcomeState
from synapse_channel.core.protocol import MessageType
from synapse_channel.operations.hub_intent import HubIntent, hub_intent
from synapse_channel.operations.runner import (
    OperationRunRefused,
    RunPlan,
    RunReport,
    Verifier,
    authorised,
    execute,
    load_grants,
)

HUB_AUTHORITY = "hub_attempt"
"""How a run's report names the hub's attempt beside the local grants file."""

_READY_SECONDS = 10.0
_ANSWER_SECONDS = 10.0


def file_state(path: Path) -> str:
    """Return the content SHA-256 of the file at ``path``, or ``absent`` when there is none.

    Raises
    ------
    OperationRunRefused
        If something exists at ``path`` that cannot be read as a file.
    """
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return "absent"
    except OSError as exc:
        raise OperationRunRefused(f"the file {path} cannot be read") from exc
    return hashlib.sha256(data).hexdigest()


def _actor(args: argparse.Namespace) -> str:
    """Return the identity the run acts as on the hub: ``--name``, or the grants subject."""
    return str(args.name or args.subject)


def _intent(args: argparse.Namespace, run: RunPlan) -> HubIntent:
    """Build the run's intent from the plan and the file as it is now."""
    if args.verify_file is None or args.verify_sha256 is None or args.intent_deadline is None:
        raise OperationRunRefused(
            "the hub path needs --verify-file, --verify-sha256 and --intent-deadline"
        )
    path = Path(os.path.abspath(args.verify_file))
    return hub_intent(
        run,
        path=str(path),
        before=file_state(path),
        after_sha256=args.verify_sha256,
        deadline=args.intent_deadline,
    )


def _ask(
    args: argparse.Namespace,
    message_type: str,
    fields: dict[str, Any],
    answers: Callable[[dict[str, Any]], bool],
) -> HubReply:
    """Send one request to the hub as the run's identity."""
    return asyncio.run(
        ask_hub(
            uri=args.hub,
            actor=_actor(args),
            message_type=message_type,
            fields=fields,
            answers=answers,
            token=args.token,
            ready_timeout=_READY_SECONDS,
            result_timeout=_ANSWER_SECONDS,
        )
    )


def _print(document: dict[str, Any]) -> None:
    """Print one JSON object, the command's only output on stdout."""
    print(json.dumps(document, sort_keys=True))


def declare(args: argparse.Namespace, run: RunPlan) -> int:
    """Declare the planned run with its intent; print the hub's answer and run nothing.

    Returns 0 when the hub kept the declaration and its intent, 1 for any other
    answer, and 2 when no answer came.
    """
    intent = _intent(args, run)
    request_id = uuid.uuid4().hex
    fields = {
        "record": "declare",
        "task_id": args.task,
        "operation_id": args.operation_id,
        "epoch": args.epoch,
        "effect": "file",
        "effect_target": intent.effect_target,
        "intent": intent.statement,
        "request_id": request_id,
    }
    outcome = _ask(
        args,
        MessageType.OPERATION_RECORD,
        fields,
        lambda data: (
            data.get("type") == MessageType.OPERATION_RECORD_RESULT
            and data.get("request_id") == request_id
        ),
    )
    reply = outcome.reply or {}
    answer = {
        "step": "declare",
        "status": str(
            reply.get("status") or ("no_verdict" if outcome.reply is None else "refused")
        ),
        "reason": str(reply.get("reason") or outcome.failure),
        "intent_sha256": str(reply.get("intent_sha256") or ""),
        "task_id": args.task,
        "operation_id": args.operation_id,
    }
    _print(answer)
    if outcome.reply is None:
        return 2
    return 0 if answer["status"] == "accepted" and answer["intent_sha256"] else 1


def _composed_digest(args: argparse.Namespace, intent: HubIntent) -> str:
    """Return the digest the hub composes for this intent, task, operation and identity."""
    return compose_intent(
        parse_intent_statement(intent.statement, "file"),
        task_id=args.task,
        operation_id=args.operation_id,
        effect="file",
        target=intent.effect_target,
        actor=_actor(args),
    ).sha256


def _check_before_attempt(args: argparse.Namespace, run: RunPlan) -> None:
    """Refuse, before the hub is asked, a changed plan or a run the grants do not cover."""
    if _composed_digest(args, _intent(args, run)) != args.intent_sha256:
        raise OperationRunRefused(
            "the plan, the file or the identity changed since the intent was declared; "
            "declare again"
        )
    grants = load_grants(args.grants)
    if not authorised(grants, subject=args.subject, row=run.row, now=time.time()):
        raise OperationRunRefused(f"{args.subject} holds no unexpired grant for this run")


def _attempt(args: argparse.Namespace, key: str) -> tuple[dict[str, Any], int | None]:
    """Ask the hub for one attempt; return the answer and the exit status when it is not open."""
    outcome = _ask(
        args,
        MessageType.OPERATION_ATTEMPT,
        {
            "task_id": args.task,
            "operation_id": args.operation_id,
            "epoch": args.epoch,
            "intent_sha256": args.intent_sha256,
            "idem_key": key,
        },
        lambda data: (
            data.get("type") == MessageType.OPERATION_ATTEMPT_RESULT
            and data.get("request_key") == key
        ),
    )
    reply = outcome.reply or {}
    live = reply.get("live") if isinstance(reply.get("live"), dict) else {}
    answer = {
        "step": "attempt",
        "answer": str(reply.get("answer") or ("no_answer" if outcome.reply is None else "refused")),
        "detail": str(reply.get("payload") or outcome.failure),
        "attempt_id": str(reply.get("attempt_id") or ""),
        "attempt_state": str((live or {}).get("attempt_state") or "none"),
        "request_key": key,
    }
    if outcome.reply is None:
        return answer, 3
    if answer["answer"] != "approved_fresh" or answer["attempt_state"] != "open":
        return answer, 1
    return answer, None


def outcome_document(report: RunReport) -> dict[str, object]:
    """Return the run as the ``synapse.execution-outcome.v1`` report its executor publishes.

    The effect is ``confirmed_done`` only when the run's own verifier saw it; the
    hub still counts the executor's own observation as no verification.
    """
    if report.state == "not_started":
        effect = OutcomeState.NOT_STARTED
    elif report.verification is not None and report.verification.verified:
        effect = OutcomeState.CONFIRMED_DONE
    else:
        effect = OutcomeState.UNKNOWN
    observation = (report.observation or report.state)[:512]
    return ExecutionOutcome(
        report.operation_id,
        "worker_process",
        OutcomeState(report.state),
        observation,
        external_effect=effect,
    ).to_json()


def _publish(args: argparse.Namespace, report: RunReport) -> dict[str, str]:
    """Publish the run's outcome on its task, which ends the attempt; return the hub's answer."""
    request_id = uuid.uuid4().hex
    outcome = _ask(
        args,
        MessageType.OPERATION_RECORD,
        {
            "record": "outcome",
            "task_id": args.task,
            "operation_id": args.operation_id,
            "report": outcome_document(report),
            "request_id": request_id,
        },
        lambda data: (
            data.get("type") == MessageType.OPERATION_RECORD_RESULT
            and data.get("request_id") == request_id
        ),
    )
    reply = outcome.reply or {}
    return {
        "status": str(
            reply.get("status") or ("no_verdict" if outcome.reply is None else "refused")
        ),
        "reason": str(reply.get("reason") or outcome.failure),
    }


def run_attempt(
    args: argparse.Namespace,
    run: RunPlan,
    verifier: Verifier | None,
    *,
    journal: Path,
    status: Callable[[RunReport], int],
) -> int:
    """Check, ask for one attempt, run inside it, and publish the outcome.

    Returns the run's own exit status; 3 when the hub did not take the outcome,
    because the attempt then stays open until the outcome is published or the
    attempt is sealed; 2 when the run was refused; the attempt's status when it
    did not open.

    Raises
    ------
    OperationRunRefused
        Before the hub is asked: a changed plan, file or identity, or grants that
        do not cover the run.
    """
    _check_before_attempt(args, run)
    answer, refused = _attempt(args, args.idem_key or uuid.uuid4().hex)
    if refused is not None:
        _print({"attempt": answer})
        return refused
    return _run_inside(args, run, verifier, answer, journal=journal, status=status)


def _run_inside(
    args: argparse.Namespace,
    run: RunPlan,
    verifier: Verifier | None,
    answer: dict[str, Any],
    *,
    journal: Path,
    status: Callable[[RunReport], int],
) -> int:
    """Run inside the open attempt, publish the outcome, and print the one report."""
    try:
        report = execute(
            run,
            operation_id=args.operation_id,
            subject=args.subject,
            grants_path=args.grants,
            journal=journal,
            timeout=args.timeout,
            verifier=verifier,
        )
    except (OperationRunRefused, ValueError) as exc:
        report = RunReport(
            args.operation_id, "not_started", f"refused after the attempt opened: {exc}"
        )
        published = _publish(args, report)
        _print({"attempt": answer, "refused": str(exc), "outcome_published": published})
        return 2
    published = _publish(args, report)
    _print(
        {
            **report.to_json(),
            "authority": f"{report.authority}; {HUB_AUTHORITY}:{answer['attempt_id']}",
            "hub_governed": True,
            "attempt": answer,
            "outcome_published": published,
        }
    )
    return status(report) if published["status"] == "accepted" else 3
