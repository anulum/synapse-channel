# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — one operation's chain, read from what a real hub journalled
"""Every state of an operation's report comes from rows that a real hub wrote."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from pathlib import Path

import pytest

import operation_attempt_scenario as scene
from hub_e2e_helpers import AgentHandle, running_hub
from operation_report_store import connected_seats, declared, journal_every_state, policy
from synapse_channel import cli
from synapse_channel.core.aef_canonical import canonical_json
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.operation_report import (
    build_operation_report,
    operation_report_to_json,
    render_lines,
    run_operation_report,
    run_operation_reports,
)
from synapse_channel.core.persistence import EventStore


@pytest.fixture(scope="module")
def store_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One real hub journal holding an operation in every state the report gives."""
    path = tmp_path_factory.mktemp("operation-report") / "hub.db"
    journal_every_state(path)
    return path


@pytest.mark.parametrize(
    ("operation", "state"),
    [
        ("op-verified", "verified"),
        ("op-sealed", "sealed"),
        ("op-ended", "ended, unreconciled"),
        ("op-unknown", "outcome reported, unknown"),
        ("op-open", "attempt open"),
        ("op-reconciled", "reconciled"),
        ("op-refused", "refused: not_approved"),
        ("op-refused-then-approved", "approved, no attempt"),
        ("op-awaiting", "awaiting approval"),
        ("op-approved", "approved, no attempt"),
        ("op-rejected", "approval rejected"),
        ("op-declared", "declared"),
        ("op-never", "not recorded"),
    ],
)
def test_each_state_is_read_from_the_rows_the_hub_wrote(
    store_path: Path, operation: str, state: str
) -> None:
    report = run_operation_report(store_path, operation)

    assert report.state == state
    assert report.custody == "not recorded here"
    if state == "not recorded":
        assert (report.task_id, report.chain, report.attempts) == ("", (), ())
    else:
        assert report.task_id == scene.TASK
        assert report.chain[0].kind == "declared"
        assert [link.seq for link in report.chain] == sorted(link.seq for link in report.chain)


def test_an_open_attempt_is_not_hidden_by_a_later_refusal_which_is_still_listed(
    store_path: Path,
) -> None:
    report = run_operation_report(store_path, "op-open")

    answers = [link.detail["answer"] for link in report.chain if link.kind == "answer"]
    assert answers == ["approved_fresh", "attempt_exists"]
    assert [attempt.state for attempt in report.attempts] == ["attempt open"]


def test_the_chain_names_each_link_its_evidence_and_the_kept_digest_is_checked(
    store_path: Path,
) -> None:
    report = run_operation_report(store_path, "op-verified")
    kinds = [link.kind for link in report.chain]

    assert kinds == ["declared", "approval", "answer", "outcome", "verification"]
    assert report.digest_check == "matches"
    assert report.chain[3].actor == scene.WORKER
    assert report.chain[4].actor == scene.CHECKER
    assert report.chain[4].detail == {"effect": "observed", "result": "verified"}
    assert run_operation_report(store_path, "op-declared").digest_check == "no intent"


def test_the_kept_intent_is_given_field_by_field_as_the_bytes_its_digest_covers(
    store_path: Path,
) -> None:
    report = run_operation_report(store_path, "op-awaiting")
    joined = "{" + ",".join(f"{json.dumps(key)}:{text}" for key, text in report.intent_fields) + "}"

    assert [key for key, _text in report.intent_fields] == sorted(
        key for key, _text in report.intent_fields
    )
    assert "tool" in dict(report.intent_fields)
    assert dict(report.intent_fields)["egress"] == "[]"
    assert hashlib.sha256(joined.encode("utf-8")).hexdigest() == report.intent_sha256
    assert report.recomputed_sha256 == report.intent_sha256
    assert operation_report_to_json(report)["intent_fields"] == [
        {"field": key, "canonical": text} for key, text in report.intent_fields
    ]
    bare = run_operation_report(store_path, "op-declared")
    assert (bare.recomputed_sha256, bare.intent_fields) == ("", ())


def test_a_kept_intent_that_does_not_give_its_kept_digest_is_reported_as_differing(
    store_path: Path,
) -> None:
    store = EventStore(store_path)
    try:
        events = list(store.read_all())
    finally:
        store.close()
    changed = []
    for event in events:
        if (
            event.kind == EventKind.OPERATION_DECLARED
            and event.payload["operation_id"] == "op-verified"
        ):
            payload = dict(event.payload)
            payload["intent"] = {**payload["intent"], "budget": "a different budget"}
            event = event._replace(payload=payload)
        changed.append(event)

    report = build_operation_report("op-verified", changed)
    kept = next(
        event.payload["intent"]
        for event in changed
        if event.kind == EventKind.OPERATION_DECLARED
        and event.payload["operation_id"] == "op-verified"
    )

    assert report.digest_check == "differs"
    assert report.recomputed_sha256 == hashlib.sha256(canonical_json(kept)).hexdigest()
    assert report.recomputed_sha256 != report.intent_sha256
    assert operation_report_to_json(report)["recomputed_sha256"] == report.recomputed_sha256
    assert dict(report.intent_fields)["budget"] == '"a different budget"'
    assert f"intent recomputed: {report.recomputed_sha256}" in render_lines(report)


def test_a_declaration_the_hubs_reader_skips_gives_no_attempt_rather_than_a_failure(
    store_path: Path,
) -> None:
    store = EventStore(store_path)
    try:
        events = list(store.read_all())
    finally:
        store.close()
    broken = [
        event._replace(payload={**event.payload, "recorded_at": "not a time"})
        if event.kind == EventKind.OPERATION_DECLARED
        and event.payload["operation_id"] == "op-ended"
        else event
        for event in events
    ]

    report = build_operation_report("op-ended", broken)

    # The hub's own reader skips a declaration without a time, so no attempt is claimed for it.
    assert report.attempts == ()
    assert report.state == "approved, no attempt"


async def test_an_open_attempt_past_its_deadline_is_silent_and_never_ended(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=store, acl_policy=policy())) as (_hub, uri):
            async with connected_seats(uri) as seats:
                epoch = await scene.claim(seats[scene.WORKER])
                deadline = int(time.time()) + 3
                await _opened_with_deadline(seats, epoch, deadline)
                await asyncio.sleep(max(0.0, deadline - time.time()) + 1.0)
                await scene.note(seats[scene.CHECKER], "note", "the hub's clock moves on")
        report = build_operation_report("op-silent", list(store.read_all()))
    finally:
        store.close()

    assert report.as_of >= deadline
    assert report.state == "attempt open, silent"
    assert [attempt.state for attempt in report.attempts] == ["attempt open, silent"]


async def _opened_with_deadline(seats: dict[str, AgentHandle], epoch: int, deadline: int) -> None:
    digest = await declared(seats, epoch, "op-silent", deadline=deadline)
    await scene.approve(seats[scene.APPROVER], digest)
    answer = await scene.attempt(seats[scene.WORKER], epoch, digest, "q1", operation_id="op-silent")
    assert answer["answer"] == "approved_fresh", answer


def test_the_command_prints_the_chain_escaped_and_exits_by_what_is_recorded(
    store_path: Path, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    base = ["operation", "report", str(store_path), "--operation-id"]

    assert cli.main([*base, "op-refused-then-approved"]) == 0
    text = capsys.readouterr().out
    assert "operation op-refused-then-approved: approved, no attempt" in text
    # The note keeps no newline, and a control character from the journal is escaped, not sent.
    assert "reason=line one forged \\x1b[31mred" in text
    assert "\x1b" not in text
    assert cli.main([*base, "op-verified"]) == 0
    verified = capsys.readouterr().out
    assert '  intent.budget = "not_applicable"' in verified
    assert "intent recomputed" not in verified
    assert cli.main([*base, "op-verified", "--json"]) == 0
    document = json.loads(capsys.readouterr().out)
    assert document == operation_report_to_json(run_operation_report(store_path, "op-verified"))
    assert cli.main([*base, "op-never"]) == 1
    capsys.readouterr()
    assert (
        cli.main(["operation", "report", str(tmp_path / "absent.db"), "--operation-id", "x"]) == 2
    )
    assert "missing event store" in capsys.readouterr().out


def test_an_outcome_ended_attempt_sealed_by_stop_read_back_reports_sealed(tmp_path: Path) -> None:
    """When stop_readback recomputes sealed after an outcome, the report matches the hub."""
    from synapse_channel.core.attempt_markers import AttemptMarker

    op = "op-readback-sealed"
    attempt = "a" * 32
    digest = "d" * 64
    marker = AttemptMarker(op, attempt, digest)
    path = tmp_path / "hub.db"
    store = EventStore(path)
    try:
        store.append(
            EventKind.OPERATION_DECLARED,
            {
                "operation_id": op,
                "task_id": "T1",
                "sender": scene.WORKER,
                "recorded_at": 10.0,
                "effect": "git_publication",
                "intent_sha256": digest,
            },
        )
        store.append(
            EventKind.OPERATION_ATTEMPT_ANSWERED,
            {
                "operation_id": op,
                "sender": scene.WORKER,
                "answer": "approved_fresh",
                "attempt_id": attempt,
                "approval_seq": 1,
                "request_key": "k1",
            },
        )
        store.append(
            EventKind.OPERATION_OUTCOME,
            {
                "operation_id": op,
                "sender": scene.WORKER,
                "state": "not_started",
                "boundary": "worker_process",
                "external_effect": "unknown",
            },
        )
        store.append(
            EventKind.OPERATION_STOP_READBACK,
            {
                "operation_id": op,
                "sender": "R/recovery",
                "stop_seq": 99,
                "attempt_id": attempt,
                "result": "sealed",
                "marker_ref": marker.ref,
                "object_format": "sha1",
                "marker_object": marker.objects("sha1").sealed,
            },
        )
        assert run_operation_report(path, op).state == "ended, unreconciled"
        assert [item.state for item in run_operation_reports(path)] == ["ended, unreconciled"]

        stop_seq = store.append(
            EventKind.OPERATION_STOPPED,
            {
                "scope": "project:P",
                "epoch": 1,
                "listed": [{"operation_id": op, "attempt_id": attempt}],
            },
        )
        store.append(
            EventKind.OPERATION_STOP_READBACK,
            {
                "operation_id": op,
                "sender": "R/recovery",
                "stop_seq": stop_seq,
                "attempt_id": attempt,
                "result": "sealed",
                "marker_ref": marker.ref,
                "object_format": "sha1",
                "marker_object": marker.objects("sha1").sealed,
            },
        )
    finally:
        store.close()
    report = run_operation_report(path, op)
    assert report.state == "sealed"
    assert [item.state for item in report.attempts] == ["sealed"]
    assert report.chain[-1].kind == "stop_readback"
    assert report.chain[-1].detail == {
        "stop_seq": stop_seq,
        "attempt_id": attempt,
        "result": "sealed",
    }
    assert [item.state for item in run_operation_reports(path)] == ["sealed"]
