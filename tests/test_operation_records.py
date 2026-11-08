# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — operation record requests, the ledger and the journal rows
"""Decode operation records exactly, bind them to one task, and journal only what was checked."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from synapse_channel.core.acl import CLAIM, OPERATION_EVIDENCE, Target
from synapse_channel.core.execution_outcome import ExecutionOutcome, OutcomeState
from synapse_channel.core.handlers import operation_records as handler
from synapse_channel.core.journal import EventKind
from synapse_channel.core.operation_records import (
    MAX_RECORDS_PER_OPERATION,
    RECORD_DECLARE,
    REFUSED_INVALID,
    REFUSED_NOT_HOLDER,
    REFUSED_OPERATION_MISMATCH,
    REFUSED_RECORD_LIMIT,
    REFUSED_STALE_EPOCH,
    REFUSED_TASK_MISMATCH,
    REFUSED_UNKNOWN,
    REFUSED_VERIFIER_MISMATCH,
    OperationLedger,
    OperationRecordError,
    OperationRecordRequest,
    declaration_refusal,
    decode_operation_record,
    evidence_refusal,
    record_operation,
)
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.result_verification_record import ResultVerification
from synapse_channel.core.state_models import TaskClaim

SHA = "a" * 64
REV = "b" * 40


def _declare(**changes: Any) -> dict[str, Any]:
    frame: dict[str, Any] = {
        "record": "declare",
        "task_id": "T",
        "operation_id": "op-1",
        "epoch": 3,
        "effect": "file",
        "effect_target": {"path": "out/result.txt", "sha256": SHA},
    }
    frame.update(changes)
    return frame


def _outcome_report(operation_id: str = "op-1") -> dict[str, object]:
    return ExecutionOutcome(
        operation_id, "worker_process", OutcomeState.CONFIRMED_DONE, "provider_process_exited"
    ).to_json()


def _verification(operation_id: str = "op-1", verifier: str = "file_change") -> dict[str, object]:
    return ResultVerification(
        operation_id,
        verifier,
        "confirmed_done",
        "observed",
        "verified",
        "content_matches",
        {"sha256": SHA, "path": "out/result.txt"},
    ).to_json()


def _claim(**changes: Any) -> TaskClaim:
    fields: dict[str, Any] = {
        "task_id": "T",
        "owner": "P/worker",
        "note": "",
        "claimed_at": 100.0,
        "lease_expires_at": 200.0,
        "epoch": 3,
        "ownership_epoch": 2,
    }
    fields.update(changes)
    return TaskClaim(**fields)


@pytest.mark.parametrize(
    ("effect", "target"),
    [
        ("file", {"path": "a.txt", "sha256": SHA}),
        (
            "git_publication",
            {"repository": "git@github.com:org/repo.git", "branch": "main", "revision": REV},
        ),
        (
            "git_publication",
            {"repository": "ssh://git@host/repo", "branch": "a/b", "revision": REV},
        ),
        (
            "git_publication",
            {"repository": "https://github.com/org/repo", "branch": "main", "revision": REV},
        ),
        ("deployment", {"report": "/srv/app/health.json", "revision": "v1.2.3"}),
        ("migration", {"database": "/srv/app.db", "migration": "0042", "schema_sha256": SHA}),
    ],
)
def test_each_effect_decodes_its_exact_target(effect: str, target: dict[str, str]) -> None:
    """Every supported effect keeps exactly its own identifiers."""
    request = decode_operation_record(
        _declare(effect=effect, effect_target=target, budget_remaining=0)
    )
    assert (request.record, request.effect, dict(request.target)) == (
        RECORD_DECLARE,
        effect,
        target,
    )
    assert (request.epoch, request.budget_remaining) == (3, 0)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"record": "correction"}, "record must be one of"),
        ({"task_id": ""}, "task_id must be text"),
        ({"task_id": "T\n"}, "control characters"),
        ({"operation_id": "op 1"}, "operation_id has the wrong form"),
        ({"operation_id": "x" * 129}, "operation_id has the wrong form"),
        ({"effect": "email"}, "effect must be one of"),
        ({"epoch": 0}, "epoch must be an integer"),
        ({"epoch": True}, "epoch must be an integer"),
        ({"budget_remaining": -1}, "budget_remaining must be an integer"),
        ({"effect_target": ["a.txt"]}, "effect_target must be an object"),
        ({"effect_target": {"path": "a.txt", "sha256": "A" * 64}}, "sha256 has the wrong form"),
        ({"effect_target": {"path": "a.txt", "sha256": SHA, "extra": "x"}}, "names exactly"),
        (
            {
                "effect": "git_publication",
                "effect_target": {
                    "repository": "https://token@github.com/org/repo",
                    "branch": "main",
                    "revision": REV,
                },
            },
            "cannot carry a credential",
        ),
        (
            {
                "effect": "git_publication",
                "effect_target": {
                    "repository": "ssh://user:secret@host/repo",
                    "branch": "main",
                    "revision": REV,
                },
            },
            "cannot carry a credential",
        ),
        (
            {
                "effect": "git_publication",
                "effect_target": {
                    "repository": "https://github.com/org/repo?access_token=x",
                    "branch": "main",
                    "revision": REV,
                },
            },
            "query or fragment",
        ),
        (
            {
                "effect": "git_publication",
                "effect_target": {"repository": "repo", "branch": "-bad", "revision": REV},
            },
            "branch has the wrong form",
        ),
    ],
)
def test_a_malformed_declaration_is_refused(changes: dict[str, Any], message: str) -> None:
    """Each malformed field is refused without echoing the value."""
    with pytest.raises(OperationRecordError, match=message):
        decode_operation_record(_declare(**changes))


def test_outcome_and_verification_bodies_are_decoded_by_the_core_parsers() -> None:
    """A malformed body is refused as an operation record error."""
    outcome = decode_operation_record(
        {"record": "outcome", "task_id": "T", "operation_id": "op-1", "report": _outcome_report()}
    )
    assert outcome.outcome is not None and outcome.outcome.state is OutcomeState.CONFIRMED_DONE
    verified = decode_operation_record(
        {
            "record": "verification",
            "task_id": "T",
            "operation_id": "op-1",
            "verification": _verification(),
        }
    )
    assert verified.verification is not None and verified.verification.verified
    with pytest.raises(OperationRecordError, match="must be a JSON object"):
        decode_operation_record({"record": "outcome", "task_id": "T", "operation_id": "op-1"})
    with pytest.raises(OperationRecordError, match="not a synapse.result-verification.v1"):
        decode_operation_record(
            {
                "record": "verification",
                "task_id": "T",
                "operation_id": "op-1",
                "verification": {"schema": "x"},
            }
        )


def test_only_the_live_owner_at_its_epoch_or_anchor_may_declare() -> None:
    """The current epoch and the ownership anchor both qualify; nothing else does."""
    claim = _claim()
    assert declaration_refusal(claim, "P/worker", 3, 150.0) is None
    assert declaration_refusal(claim, "P/worker", 2, 150.0) is None
    assert declaration_refusal(claim, "P/worker", 1, 150.0) == REFUSED_STALE_EPOCH
    assert declaration_refusal(claim, "P/other", 3, 150.0) == REFUSED_NOT_HOLDER
    assert declaration_refusal(claim, "P/worker", 3, 200.0) == REFUSED_NOT_HOLDER
    assert declaration_refusal(None, "P/worker", 3, 150.0) == REFUSED_NOT_HOLDER


def _journal_declaration(store: EventStore, ledger: OperationLedger, **changes: Any) -> None:
    request = decode_operation_record(_declare(**changes))
    record_operation(store, request, sender="P/worker", snapshot_epoch=3, now=150.0)
    assert request.effect is not None
    ledger.declare(request.operation_id, request.task_id, request.effect)


def test_evidence_binds_to_its_declared_operation_task_and_verifier(tmp_path: Path) -> None:
    """Unknown, other-task, mismatched and over-limit evidence is refused."""
    store = EventStore(tmp_path / "hub.db")
    try:
        ledger = OperationLedger(store)
        _journal_declaration(store, ledger)

        def check(**frame: Any) -> str | None:
            return evidence_refusal(ledger, decode_operation_record(frame))

        good = {"record": "verification", "task_id": "T", "operation_id": "op-1"}
        assert check(**good, verification=_verification()) is None
        assert check(**{**good, "operation_id": "op-9"}, verification=_verification("op-9")) == (
            REFUSED_UNKNOWN
        )
        assert check(**{**good, "task_id": "U"}, verification=_verification()) == (
            REFUSED_TASK_MISMATCH
        )
        assert check(**good, verification=_verification("op-2")) == REFUSED_OPERATION_MISMATCH
        assert check(**good, verification=_verification(verifier="git_publication")) == (
            REFUSED_VERIFIER_MISMATCH
        )
        assert (
            check(record="outcome", task_id="T", operation_id="op-1", report=_outcome_report())
            is None
        )
        for _ in range(MAX_RECORDS_PER_OPERATION):
            ledger.add_record("op-1")
        assert check(**good, verification=_verification()) == REFUSED_RECORD_LIMIT
    finally:
        store.close()


def test_rows_carry_only_checked_fields_and_the_ledger_restores(tmp_path: Path) -> None:
    """Free-text evidence is dropped; a restarted hub knows the operation and its count."""
    path = tmp_path / "hub.db"
    store = EventStore(path)
    try:
        ledger = OperationLedger(store)
        _journal_declaration(store, ledger, budget_remaining=4)
        outcome = decode_operation_record(
            {
                "record": "outcome",
                "task_id": "T",
                "operation_id": "op-1",
                "report": _outcome_report(),
            }
        )
        record_operation(store, outcome, sender="P/worker", now=151.0)
        verified = decode_operation_record(
            {
                "record": "verification",
                "task_id": "T",
                "operation_id": "op-1",
                "verification": _verification(),
            }
        )
        record_operation(store, verified, sender="P/checker")
        declared, done, checked = store.read_since(
            0,
            kinds=(
                EventKind.OPERATION_DECLARED,
                EventKind.OPERATION_OUTCOME,
                EventKind.OPERATION_VERIFIED,
            ),
        )
        assert declared.payload == {
            "task_id": "T",
            "operation_id": "op-1",
            "sender": "P/worker",
            "recorded_at": 150.0,
            "effect": "file",
            "path": "out/result.txt",
            "sha256": SHA,
            "snapshot_epoch": 3,
            "budget_remaining": 4,
        }
        assert (done.payload["state"], done.payload["boundary"]) == (
            "confirmed_done",
            "worker_process",
        )
        assert checked.payload["evidence"] == {"sha256": SHA}
        assert (checked.payload["sender"], checked.payload["result"]) == ("P/checker", "verified")
    finally:
        store.close()
    reopened = EventStore(path)
    try:
        restored = OperationLedger(reopened)
        assert (restored.task_of("op-1"), restored.effect_of("op-1")) == ("T", "file")
        assert restored.records_of("op-1") == 2
        assert (
            restored.task_of("op-x"),
            restored.effect_of("op-x"),
            restored.records_of("op-x"),
        ) == (
            None,
            None,
            0,
        )
    finally:
        reopened.close()


def test_the_ledger_skips_malformed_rows_and_starts_empty_without_a_journal(tmp_path: Path) -> None:
    """Rows without an id, task or effect do not create operations."""
    assert OperationLedger(None).task_of("op-1") is None
    store = EventStore(tmp_path / "hub.db")
    try:
        store.append(EventKind.OPERATION_DECLARED, {"task_id": "T", "effect": "file"})
        store.append(EventKind.OPERATION_DECLARED, {"operation_id": "op-2", "task_id": 5})
        store.append(EventKind.OPERATION_VERIFIED, {"operation_id": "op-3"})
        ledger = OperationLedger(store)
        assert (ledger.task_of("op-2"), ledger.records_of("op-3")) == (None, 0)
    finally:
        store.close()


def test_requests_built_without_their_fields_are_refused_not_journalled(tmp_path: Path) -> None:
    """A request object missing its body or epoch is never written."""
    store = EventStore(tmp_path / "hub.db")
    try:
        ledger = OperationLedger(store)
        bare = OperationRecordRequest("declare", "T", "op-1")
        verdict = handler._declare(store, ledger, _claim(), "P/worker", bare)
        assert (verdict.status, verdict.reason) == ("refused", REFUSED_INVALID)
        with pytest.raises(OperationRecordError, match="carries an outcome or a verification"):
            record_operation(store, OperationRecordRequest("verification", "T", "op-1"), sender="x")
        assert store.max_seq() == 0
    finally:
        store.close()


def test_the_acl_mapping_names_claim_for_a_declaration_and_evidence_otherwise() -> None:
    """A loaded policy's claim rule governs declarations; evidence uses its own verb."""
    assert handler.operation_record_access({"record": "declare", "task_id": "T"}) == [
        (CLAIM, Target("claim", "T"))
    ]
    assert handler.operation_record_access({"record": "outcome", "task_id": "T"}) == [
        (OPERATION_EVIDENCE, Target("claim", "T"))
    ]
