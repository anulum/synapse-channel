# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — a live hub journals operation records for a claimed task
"""A real hub accepts a declaration from the claim holder and evidence from a granted checker."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from hub_e2e_helpers import AgentHandle, close_agents, connect_agent, running_hub
from synapse_channel.core.acl import CLAIM, MESSAGE, OPERATION_EVIDENCE, AclPolicy, AclRule
from synapse_channel.core.execution_outcome import ExecutionOutcome, OutcomeState
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.result_verification_record import ResultVerification

WORKER = "P/worker"
CHECKER = "Q/checker"
SHA = "c" * 64


def _policy() -> AclPolicy:
    return AclPolicy(
        [
            AclRule(CLAIM, "claim", "*", "", "anyone may claim"),
            AclRule(MESSAGE, "agent", "*", "", "anyone may message"),
            AclRule(OPERATION_EVIDENCE, "claim", "T*", "Q", "checker project records evidence"),
        ]
    )


async def _record(handle: AgentHandle, **fields: Any) -> dict[str, Any]:
    """Send one operation record and return the result frame that answers it."""
    seen = len(handle.recorder.messages)
    await handle.agent.send_message(MessageType.OPERATION_RECORD, **fields)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 3.0
    while loop.time() < deadline:
        for frame in handle.recorder.messages[seen:]:
            if frame.get("type") == MessageType.OPERATION_RECORD_RESULT:
                return frame
        await asyncio.sleep(0.01)
    raise TimeoutError("hub returned no operation_record_result")


async def _claim(handle: AgentHandle, task_id: str) -> int:
    """Claim ``task_id`` and return the granted epoch."""
    await handle.agent.claim(task_id, note="operation record test")
    grant = await handle.recorder.wait_for(
        lambda frame: (
            frame.get("type") == MessageType.CLAIM_GRANTED and frame.get("task_id") == task_id
        )
    )
    epoch = grant["epoch"]
    assert isinstance(epoch, int)
    return epoch


def _declaration(epoch: int, operation_id: str = "op-1", task_id: str = "T1") -> dict[str, Any]:
    return {
        "record": "declare",
        "task_id": task_id,
        "operation_id": operation_id,
        "epoch": epoch,
        "effect": "file",
        "effect_target": {"path": "out/result.txt", "sha256": SHA},
        "request_id": f"req-{operation_id}",
    }


def _outcome(operation_id: str = "op-1") -> dict[str, Any]:
    report = ExecutionOutcome(
        operation_id, "worker_process", OutcomeState.CONFIRMED_DONE, "provider_process_exited"
    ).to_json()
    return {"record": "outcome", "task_id": "T1", "operation_id": operation_id, "report": report}


def _verification(verifier: str = "file_change", operation_id: str = "op-1") -> dict[str, Any]:
    record = ResultVerification(
        operation_id, verifier, "confirmed_done", "observed", "verified", "content_matches"
    ).to_json()
    return {
        "record": "verification",
        "task_id": "T1",
        "operation_id": operation_id,
        "verification": record,
    }


async def test_the_claim_holder_declares_and_a_granted_checker_records_evidence(
    tmp_path: Path,
) -> None:
    """Accepted records are journalled once; every refusal names its reason."""
    store = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=store, acl_policy=_policy())) as (_hub, uri):
            worker = await connect_agent(WORKER, uri)
            checker = await connect_agent(CHECKER, uri)
            try:
                epoch = await _claim(worker, "T1")
                stale = await _record(worker, **_declaration(epoch + 99))
                assert (stale["status"], stale["reason"]) == ("refused", "stale_epoch")
                foreign = await _record(checker, **_declaration(epoch))
                assert (foreign["status"], foreign["reason"]) == ("refused", "not_claim_holder")
                accepted = await _record(worker, **_declaration(epoch))
                assert (accepted["status"], accepted["record"]) == ("accepted", "declare")
                assert accepted["request_id"] == "req-op-1" and isinstance(accepted["seq"], int)
                again = await _record(worker, **_declaration(epoch))
                assert (again["status"], again["reason"]) == ("refused", "duplicate_operation")

                unauthorised = await _record(worker, **_outcome())
                assert (unauthorised["status"], unauthorised["reason"]) == (
                    "refused",
                    "not_authorised",
                )
                done = await _record(checker, **_outcome())
                assert done["status"] == "accepted"
                mismatch = await _record(checker, **_verification("git_publication"))
                assert (mismatch["status"], mismatch["reason"]) == ("refused", "verifier_mismatch")
                unknown = await _record(checker, **_verification(operation_id="op-9"))
                assert (unknown["status"], unknown["reason"]) == ("refused", "unknown_operation")
                verified = await _record(checker, **_verification())
                assert verified["status"] == "accepted"
                malformed = await _record(checker, record="declare", task_id="T1")
                assert (malformed["status"], malformed["reason"]) == ("refused", "invalid_request")
            finally:
                await close_agents(worker, checker)
        kinds = (
            EventKind.OPERATION_DECLARED,
            EventKind.OPERATION_OUTCOME,
            EventKind.OPERATION_VERIFIED,
        )
        rows = store.read_since(0, kinds=kinds)
        assert [row.kind for row in rows] == list(kinds)
        assert rows[0].payload["snapshot_epoch"] == epoch
        assert rows[2].payload["sender"] == CHECKER
    finally:
        store.close()


async def test_a_restarted_hub_still_knows_the_declared_operation(tmp_path: Path) -> None:
    """The ledger is rebuilt from the journal, so an id is never declared twice."""
    path = tmp_path / "hub.db"
    store = EventStore(path)
    try:
        async with running_hub(SynapseHub(journal=store, acl_policy=_policy())) as (_hub, uri):
            worker = await connect_agent(WORKER, uri)
            try:
                epoch = await _claim(worker, "T1")
                assert (await _record(worker, **_declaration(epoch)))["status"] == "accepted"
            finally:
                await close_agents(worker)
    finally:
        store.close()
    reopened = EventStore(path)
    try:
        async with running_hub(SynapseHub(journal=reopened, acl_policy=_policy())) as (_hub, uri):
            checker = await connect_agent(CHECKER, uri)
            worker = await connect_agent(WORKER, uri)
            try:
                assert (await _record(checker, **_outcome()))["status"] == "accepted"
                epoch = await _claim(worker, "T2")
                repeat = await _record(worker, **_declaration(epoch, task_id="T2"))
                assert (repeat["status"], repeat["reason"]) == ("refused", "duplicate_operation")
            finally:
                await close_agents(checker, worker)
    finally:
        reopened.close()


async def test_a_hub_without_a_journal_or_policy_keeps_no_records() -> None:
    """No journal refuses everything; no policy refuses third-party evidence."""
    async with running_hub(SynapseHub(journal=None)) as (_hub, uri):
        worker = await connect_agent(WORKER, uri)
        try:
            epoch = await _claim(worker, "T1")
            result = await _record(worker, **_declaration(epoch))
            assert (result["status"], result["reason"]) == ("refused", "no_journal")
        finally:
            await close_agents(worker)


async def test_an_open_hub_keeps_declarations_but_no_unauthorised_evidence(tmp_path: Path) -> None:
    """Without a policy nobody holds operation-evidence, even on an open hub."""
    store = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=store)) as (_hub, uri):
            worker = await connect_agent(WORKER, uri)
            try:
                epoch = await _claim(worker, "T1")
                assert (await _record(worker, **_declaration(epoch)))["status"] == "accepted"
                evidence = await _record(worker, **_outcome())
                assert (evidence["status"], evidence["reason"]) == ("refused", "not_authorised")
            finally:
                await close_agents(worker)
    finally:
        store.close()
