# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the declare, review, approve and attempt steps on a real hub, for tests
"""Drive one governed operation on a real hub: claim, declare, review, approve, ask.

The worker of project ``P`` claims the task, declares an operation with an
authorisation intent and asks to open attempts. A reviewer of ``P`` records an
accepted verdict on the intent's digest, and an approver of ``P`` approves it.
The checker of project ``Q`` publishes outcomes and verifications. Both ``P``
and ``Q`` hold ``operation-evidence`` on the task, as an executor that ends its
own attempts must.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from hub_e2e_helpers import AgentHandle
from synapse_channel.core.acl import CLAIM, MESSAGE, OPERATION_EVIDENCE, AclPolicy, AclRule
from synapse_channel.core.approvals import APPROVAL_NOTE_KIND, format_approval_note
from synapse_channel.core.authorisation_intent import INTENT_SCHEMA
from synapse_channel.core.execution_outcome import ExecutionOutcome, OutcomeState
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.result_verification_record import ResultVerification
from synapse_channel.core.verdict_records import (
    EVIDENCE_NOTE_KIND,
    VERDICT_NOTE_KIND,
    EvidenceRecord,
    VerdictRecord,
    format_evidence,
    format_verdict,
)

WORKER = "P/worker"
REVIEWER = "P/reviewer"
APPROVER = "P/approver"
CHECKER = "Q/checker"
TASK = "T1"
OPERATION = "op-1"
INPUT = "1" * 64
TARGET_SHA = "c" * 64
WAIT = 15.0
"""Seconds a test waits for one answer; the answer usually comes at once."""


def policy() -> AclPolicy:
    """Return claims and messages for anyone, and operation evidence for ``P`` and ``Q``."""
    return AclPolicy(
        [
            AclRule(CLAIM, "claim", "*", "", "anyone may claim"),
            AclRule(MESSAGE, "agent", "*", "", "anyone may message"),
            AclRule(OPERATION_EVIDENCE, "claim", "T*", "P", "the executor ends its attempts"),
            AclRule(OPERATION_EVIDENCE, "claim", "T*", "Q", "the checker observes"),
        ]
    )


def statement(**changes: Any) -> dict[str, Any]:
    """Return a valid intent statement for a file effect; ``changes`` replace fields."""
    document: dict[str, Any] = {
        "schema": INTENT_SCHEMA,
        "pre_state": "absent",
        "recovery_class": "reversible",
        "deadline": int(time.time()) + 600,
        "budget": "not_applicable",
        "egress": [],
        "tool": {"name": "render-report", "version": "1.0.0"},
        "arguments_sha256": "e" * 64,
        "rule_generation": "prose-rules:2026-11-12T06:16:35Z",
        "required_assurance": "cooperative",
    }
    document.update(changes)
    return document


async def answer(
    handle: AgentHandle,
    send: Callable[[], Awaitable[None]],
    match: Callable[[dict[str, Any]], bool],
) -> dict[str, Any]:
    """Run ``send`` and return the first frame after it that ``match`` accepts."""
    earlier = {id(frame) for frame in handle.recorder.messages}
    await send()
    return await handle.recorder.wait_for(
        lambda frame: id(frame) not in earlier and match(frame), timeout=WAIT
    )


async def claim(handle: AgentHandle, task_id: str = TASK) -> int:
    """Claim ``task_id`` and return the granted epoch."""
    grant = await answer(
        handle,
        lambda: handle.agent.claim(task_id, note="attempt test"),
        lambda frame: (
            frame.get("type") == MessageType.CLAIM_GRANTED and frame.get("task_id") == task_id
        ),
    )
    epoch = grant["epoch"]
    assert isinstance(epoch, int)
    return epoch


async def record(handle: AgentHandle, **fields: Any) -> dict[str, Any]:
    """Send one operation record and return its result."""
    return await answer(
        handle,
        lambda: handle.agent.send_message(MessageType.OPERATION_RECORD, **fields),
        lambda frame: frame.get("type") == MessageType.OPERATION_RECORD_RESULT,
    )


async def declare(
    handle: AgentHandle,
    epoch: int,
    *,
    operation_id: str = OPERATION,
    intent: dict[str, Any] | None = None,
) -> str:
    """Declare a file operation, with ``intent`` when given, and return the kept digest."""
    fields: dict[str, Any] = {
        "record": "declare",
        "task_id": TASK,
        "operation_id": operation_id,
        "epoch": epoch,
        "effect": "file",
        "effect_target": {"path": "out/result.txt", "sha256": TARGET_SHA},
    }
    if intent is not None:
        fields["intent"] = intent
    result = await record(handle, **fields)
    assert result["status"] == "accepted", result
    digest = result["intent_sha256"]
    assert isinstance(digest, str)
    return digest


async def note(handle: AgentHandle, kind: str, text: str) -> None:
    """Post one progress note and wait until the hub has posted it."""
    await answer(
        handle,
        lambda: handle.agent.post_progress(TASK, text, kind=kind),
        lambda frame: (
            frame.get("type") == "ledger_progress_posted" and frame["note"]["text"] == text
        ),
    )


async def review(reviewer: AgentHandle, digest: str, *, project: str = "P") -> None:
    """Declare the input and the intent in ``project`` and record an accepted verdict on it."""
    for item in (INPUT, digest):
        await note(reviewer, EVIDENCE_NOTE_KIND, format_evidence(EvidenceRecord(item, project)))
    author = f"{project}/reviewer"
    verdict = VerdictRecord(project, digest, "accepted", (INPUT,), (), author, 1)
    await note(reviewer, VERDICT_NOTE_KIND, format_verdict(verdict))


async def approve(approver: AgentHandle, digest: str, *, reason: str = "") -> None:
    """Approve the intent ``digest``; ``reason`` tells repeated approvals apart."""
    text = format_approval_note(subject=f"sha256:{digest}", state="approved", reason=reason)
    await note(approver, APPROVAL_NOTE_KIND, text)


async def attempt(
    handle: AgentHandle,
    epoch: int,
    digest: str,
    key: str,
    *,
    operation_id: str = OPERATION,
) -> dict[str, Any]:
    """Ask to open an attempt with request key ``key`` and return the hub's answer."""
    fields: dict[str, Any] = {
        "task_id": TASK,
        "operation_id": operation_id,
        "epoch": epoch,
        "intent_sha256": digest,
        "idem_key": key,
    }
    return await answer(
        handle,
        lambda: handle.agent.send_message(MessageType.OPERATION_ATTEMPT, **fields),
        lambda frame: (
            frame.get("type") == MessageType.OPERATION_ATTEMPT_RESULT
            and frame.get("request_key") == key
        ),
    )


def outcome(state: OutcomeState, operation_id: str = OPERATION) -> dict[str, Any]:
    """Return the fields of an outcome record for ``operation_id`` in ``state``."""
    report = ExecutionOutcome(operation_id, "worker_process", state, "attempt test").to_json()
    return {"record": "outcome", "task_id": TASK, "operation_id": operation_id, "report": report}


def verification(effect: str, result: str, operation_id: str = OPERATION) -> dict[str, Any]:
    """Return the fields of a file verification record with ``effect`` and ``result``."""
    body = ResultVerification(
        operation_id, "file_change", "confirmed_done", effect, result, "attempt test"
    ).to_json()
    return {
        "record": "verification",
        "task_id": TASK,
        "operation_id": operation_id,
        "verification": body,
    }
