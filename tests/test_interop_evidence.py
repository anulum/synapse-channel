# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the interop lane's evidence record, journal oracle and step bookkeeping
"""Check the interop record, the journal oracle and the journey judge on real stores."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from tools.interop_journey import (
    StepFailure,
    Steps,
    expectation_met,
    journey_record,
    judge,
    read_client_report,
)
from tools.interop_oracle import JournalOracle, OracleFailure
from tools.interop_record import (
    InteropRecordError,
    JourneyRecord,
    StepRecord,
    build_record,
    checked_run_url,
    journal_digest,
    key_shape,
    write_record,
)

from synapse_channel.core.journal import (
    EventKind,
    record_chat,
    record_claim_denial,
    record_release,
)
from synapse_channel.core.persistence import EventStore

COMMIT = "0123456789abcdef0123456789abcdef01234567"
TASK = "INTEROP-UNIT-1"


def _store(path: Path, *, denial_reason: str = "LEASE_LIVE", release_between: bool = False) -> Path:
    """Write a real journal: grant, optional release, denial, chat, release."""
    store = EventStore(path)
    try:
        store.append(EventKind.CLAIM, {"task_id": TASK, "owner": "unit/alpha"}, durable=True)
        if release_between:
            record_release(store, TASK)
        record_claim_denial(
            store,
            claimant="unit/beta",
            task_id=TASK,
            reason_code=denial_reason,
            worktree="",
            paths=[],
        )
        record_chat(
            store,
            {
                "sender": "unit/alpha",
                "target": "unit/beta",
                "payload": "marker",
                "metadata": {"a2aTaskId": "task-1"},
            },
        )
        record_release(store, TASK)
    finally:
        store.close()
    return path


def _assert_all(oracle: JournalOracle) -> None:
    granted = oracle.claim_granted(TASK, "unit/alpha")
    denied = oracle.claim_denied(TASK, "unit/beta", after=granted)
    oracle.no_release_between(TASK, granted, denied)
    oracle.chat_recorded("unit/alpha", "unit/beta", "marker", a2a_task_id="task-1")
    oracle.released(TASK, after=denied)


def _journey(failed_step: str = "") -> JourneyRecord:
    return JourneyRecord(
        client="unit-client",
        version="1.0",
        integrity="sha512-abc",
        transport="stdio",
        journey="unit",
        steps=[StepRecord("claim-granted", "tools/call synapse_claim", "ok", ["task_id"], [])],
        journal_sha256=journal_digest([]),
        failed_step=failed_step,
    )


def test_key_shape_keeps_names_only() -> None:
    assert key_shape({"token": "secret-value", "a": 1}) == ["a", "token"]
    assert key_shape(["not", "a", "mapping"]) == []
    assert key_shape(None) == []


def test_a_step_outcome_must_be_a_short_code() -> None:
    with pytest.raises(InteropRecordError, match="short code"):
        StepRecord("claim", "tools/call synapse_claim", "refused: Authorization: Bearer abc")


def test_journal_digest_is_canonical() -> None:
    rows = [{"seq": 1, "kind": "claim", "payload": {"b": 2, "a": 1}}]
    reordered = [{"payload": {"a": 1, "b": 2}, "kind": "claim", "seq": 1}]
    assert journal_digest(rows) == journal_digest(reordered)
    assert journal_digest([]) == hashlib.sha256(b"[]").hexdigest()


@pytest.mark.parametrize(
    "url",
    [
        "https://token@github.com/anulum/synapse-channel/actions/runs/1",
        "https://example.com/anulum/synapse-channel/actions/runs/1",
        "https://github.com/anulum/synapse-channel/actions/runs/1?token=abc",
        "http://github.com/anulum/synapse-channel/actions/runs/1",
    ],
)
def test_only_a_plain_actions_run_url_is_accepted(url: str) -> None:
    with pytest.raises(InteropRecordError, match="run URL"):
        checked_run_url(url)


def test_a_record_names_its_runs_and_never_a_secret(tmp_path: Path) -> None:
    run_url = "https://github.com/anulum/synapse-channel/actions/runs/42"
    assert checked_run_url("") == ""
    text = build_record(
        [_journey(), _journey("journal-claim-granted")],
        commit=COMMIT,
        run_url=run_url,
        secrets=["hub-token-value", ""],
    )
    document = json.loads(text)
    assert document["format"] == "synapse-interop-record-v1"
    assert document["run_url"] == run_url
    assert [journey["passed"] for journey in document["journeys"]] == [True, False]
    out = tmp_path / "record.json"
    write_record(out, text)
    assert json.loads(out.read_text(encoding="utf-8")) == document
    with pytest.raises(FileExistsError):
        write_record(out, text)


def test_a_record_with_a_secret_anywhere_is_refused() -> None:
    leaking = JourneyRecord(
        client="unit-client",
        version="1.0",
        integrity="",
        transport="stdio",
        journey="unit",
        steps=[StepRecord("claim", "tools/call Bearer eyJ-leaked", "ok")],
        journal_sha256="",
    )
    with pytest.raises(InteropRecordError, match="secret"):
        build_record([leaking], commit=COMMIT, run_url="", secrets=["eyJ-leaked"])
    with pytest.raises(InteropRecordError, match="commit"):
        build_record([], commit="HEAD", run_url="", secrets=[])


def test_the_oracle_accepts_a_complete_journal(tmp_path: Path) -> None:
    oracle = JournalOracle.read(_store(tmp_path / "hub.db"))
    _assert_all(oracle)
    assert [row["kind"] for row in oracle.asserted] == [
        EventKind.CLAIM,
        EventKind.CLAIM_DENIAL,
        EventKind.CHAT,
        EventKind.RELEASE,
    ]


@pytest.mark.parametrize(
    ("change", "step"),
    [
        ("owner", "journal-claim-granted"),
        ("claimant", "journal-claim-denied"),
        ("reason", "journal-claim-denied"),
        ("release-between", "journal-no-release-between"),
        ("chat-task", "journal-chat"),
        ("no-release", "journal-released"),
    ],
)
def test_each_missing_record_fails_its_own_step(tmp_path: Path, change: str, step: str) -> None:
    db = _store(
        tmp_path / "hub.db",
        denial_reason="SCOPE_CONFLICT" if change == "reason" else "LEASE_LIVE",
        release_between=change == "release-between",
    )
    oracle = JournalOracle.read(db)
    with pytest.raises(OracleFailure) as failure:
        granted = oracle.claim_granted(TASK, "unit/other" if change == "owner" else "unit/alpha")
        denied = oracle.claim_denied(
            TASK, "unit/other" if change == "claimant" else "unit/beta", after=granted
        )
        oracle.no_release_between(TASK, granted, denied)
        oracle.chat_recorded(
            "unit/alpha",
            "unit/beta",
            "marker",
            a2a_task_id="task-2" if change == "chat-task" else "task-1",
        )
        oracle.released(TASK, after=10_000 if change == "no-release" else denied)
    assert failure.value.step == step


def test_the_oracle_refuses_a_missing_store(tmp_path: Path) -> None:
    with pytest.raises(OracleFailure) as failure:
        JournalOracle.read(tmp_path / "absent.db")
    assert failure.value.step == "journal-read"


def test_judge_passes_a_real_journal_and_the_oracle_control_fails_first(tmp_path: Path) -> None:
    db = _store(tmp_path / "hub.db")
    steps = Steps()
    assert (
        judge(steps, db, control="none", unrelated=tmp_path / "u1.db", assertions=_assert_all)[0]
        == ""
    )
    assert steps.records[-1].name == "journal-checked"
    control = Steps()
    failed, digest = judge(
        control, db, control="journal-oracle", unrelated=tmp_path / "u2.db", assertions=_assert_all
    )
    assert failed == "journal-claim-granted"
    assert digest == journal_digest([])
    assert control.records[-1] == StepRecord("journal-claim-granted", "journal", "missing")


def test_steps_record_key_shapes_and_failures() -> None:
    steps = Steps()
    steps.passed("claim", "tools/call synapse_claim", request={"task_id": "x"}, response=None)
    failure = steps.failed("conflict", "tools/call synapse_claim", "unexpected-result")
    assert isinstance(failure, StepFailure)
    assert failure.step == "conflict" and failure.outcome == "unexpected-result"
    assert steps.records[0].request_keys == ["task_id"]
    record = journey_record(
        client="c",
        version="1",
        integrity="",
        transport="stdio",
        journey="j",
        steps=steps,
        failed_step="conflict",
        journal_sha256="",
    )
    assert record.steps == steps.records and record.failed_step == "conflict"


def test_a_separate_clients_report_is_read_or_recorded_as_a_failed_run() -> None:
    steps = Steps()
    report = read_client_report(
        json.dumps(
            {
                "failed_step": "",
                "client_version": "1.2.3",
                "steps": [
                    {
                        "name": "card-resolved",
                        "route": "GET agent card",
                        "outcome": "ok",
                        "request_keys": [],
                        "response_keys": ["name"],
                    }
                ],
            }
        ),
        0,
        steps,
    )
    assert report is not None and report["client_version"] == "1.2.3"
    assert steps.records[0].response_keys == ["name"]
    for stdout in ("Traceback: not json", json.dumps({"steps": []}), json.dumps({"steps": [{}]})):
        broken = Steps()
        assert read_client_report(stdout, 3, broken) is None
        assert broken.records == [StepRecord("client-run", "client process", "exit-3")]


@pytest.mark.parametrize(
    ("control", "failed_step", "met"),
    [
        ("none", "", True),
        ("none", "claim-granted", False),
        ("surface-disabled", "initialize-first", True),
        ("surface-disabled", "", False),
        ("surface-disabled", "journal-chat", False),
        ("journal-oracle", "journal-claim-granted", True),
        ("journal-oracle", "journal-released", False),
        ("journal-oracle", "", False),
    ],
)
def test_each_mode_requires_its_own_ending(control: str, failed_step: str, met: bool) -> None:
    assert expectation_met(_journey(failed_step), control, "journal-claim-granted") is met


def test_a_client_that_ran_another_version_fails_its_row() -> None:
    from tools.interop_journey import version_failure

    steps = Steps()
    assert version_failure(steps, "a2a-sdk", "1.2.3", "1.2.3") == ""
    assert steps.records == []
    assert version_failure(steps, "a2a-sdk", "1.3.0", "1.2.3") == "client-version"
    assert steps.records == [StepRecord("client-version", "pin check a2a-sdk", "mismatch")]
