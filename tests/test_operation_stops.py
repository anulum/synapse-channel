# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the scopes of a stop, its requests, and the ledger's stops
"""A stop names one scope; the ledger keeps each scope's stop, epoch and line, and restores them."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.core.event_description import EventWords, describe_event
from synapse_channel.core.journal import EventKind
from synapse_channel.core.operation_attempt_ledger import AttemptLedger, OpenAttempt
from synapse_channel.core.operation_attempts import OperationFact
from synapse_channel.core.operation_stops import (
    CASE_ONLY_PERSON,
    MAX_REASON,
    RESUME_ANSWERS,
    STOP_ANSWERS,
    OperationStopError,
    ResumeRequest,
    StopRequest,
    decode_resume_request,
    decode_stop_request,
    parse_scope,
    scopes_of,
)
from synapse_channel.core.persistence import EventStore

EFFECTS = ("file", "git_publication", "deployment", "migration")
WORKER = "P/worker"
GIT = OperationFact("T1", WORKER, "d" * 64, 2_000_000_000, 1_800_000_000.0, "git_publication")
FILE = OperationFact("T2", "Q/worker", "e" * 64, 2_000_000_000, 1_800_000_000.0, "file")


@pytest.mark.parametrize(
    "scope",
    ["installation", "project:P", "project:a.b-c_d", "effect:git_publication", "operation:op:1"],
)
def test_each_scope_form_is_accepted_as_written(scope: str) -> None:
    assert parse_scope(scope, EFFECTS) == scope


@pytest.mark.parametrize(
    "scope",
    [None, 7, "", "all", "project:", "project:a/b", "effect:teleport", "operation:", "x:y"],
)
def test_anything_else_is_not_a_scope_and_is_not_echoed(scope: object) -> None:
    with pytest.raises(OperationStopError) as refused:
        parse_scope(scope, EFFECTS)
    assert str(refused.value) == (
        "scope must be installation, project:<namespace>, effect:<kind> or operation:<id>"
    )
    assert refused.value.code == "operation_stop"


def test_a_stop_and_a_resume_decode_whole() -> None:
    stop = {"scope": "project:P", "reason": "a deploy went wrong", "idem_key": "k1"}
    assert decode_stop_request(stop, EFFECTS) == StopRequest(
        "project:P", "a deploy went wrong", "k1"
    )
    resume = {**stop, "case": CASE_ONLY_PERSON}
    assert decode_resume_request(resume, EFFECTS) == ResumeRequest(
        "project:P", "a deploy went wrong", CASE_ONLY_PERSON, "k1"
    )
    assert STOP_ANSWERS == ("stop_recorded", "already_stopped")
    assert RESUME_ANSWERS == ("resumed", "not_stopped", "not_permitted", "actor_of_scope")


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"reason": ""}, "reason must be text of 1 to 512 characters"),
        ({"reason": "x" * (MAX_REASON + 1)}, "reason must be text of 1 to 512 characters"),
        ({"reason": "line\nbreak"}, "reason cannot contain control characters"),
        ({"reason": "bell\x7f"}, "reason cannot contain control characters"),
        ({"idem_key": None}, "idem_key must be text of 1 to 256 characters"),
        ({"case": "someone"}, "case must be another_person or only_person"),
        ({"case": None}, "case must be another_person or only_person"),
    ],
)
def test_a_malformed_resume_is_refused_by_field(changes: dict[str, Any], message: str) -> None:
    frame = {
        "scope": "installation",
        "reason": "ok",
        "case": "another_person",
        "idem_key": "k",
        **changes,
    }
    with pytest.raises(OperationStopError, match=f"^{message}$"):
        decode_resume_request(frame, EFFECTS)


def test_an_operation_is_in_its_own_effect_project_and_the_installation_narrowest_first() -> None:
    assert scopes_of("op-1", WORKER, "git_publication") == (
        "operation:op-1",
        "effect:git_publication",
        "project:P",
        "installation",
    )
    assert scopes_of("op-1", "solo", None) == ("operation:op-1", "project:solo", "installation")
    assert scopes_of("op-9", None, None) == ("installation",)


def _ledger() -> AttemptLedger:
    ledger = AttemptLedger(None)
    ledger.note_declaration("git-op", GIT)
    ledger.note_declaration("file-op", FILE)
    ledger.open_attempt("git-op", "a1", executor=WORKER, approval_seq=7)
    ledger.open_attempt("file-op", "b1", executor="Q/worker", approval_seq=8)
    ledger.note_outcome("file-op", "Q/worker", "confirmed_done")
    ledger.open_attempt("file-op", "b2", executor="Q/worker", approval_seq=9)
    return ledger


def test_a_stop_covers_the_operations_its_scope_contains_and_names_the_narrowest() -> None:
    ledger = _ledger()
    assert ledger.stop_facts("git-op") == (None, None)
    ledger.note_stop("project:P", 20, epoch=1, listed=(("git-op", "a1"),))
    ledger.note_stop("effect:git_publication", 21, epoch=1, listed=(("git-op", "a1"),))
    assert ledger.stop_facts("git-op") == (
        ("effect:git_publication", 21),
        ("effect:git_publication", 21),
    )
    assert ledger.stop_facts("file-op") == (None, None)
    ledger.note_resume("effect:git_publication", 30)
    assert ledger.stop_facts("git-op") == (("project:P", 20), ("effect:git_publication", 30))
    ledger.note_resume("project:P", 31)
    assert ledger.stop_facts("git-op") == (None, ("project:P", 31))
    assert ledger.standing_stop("project:P") is None
    ledger.note_stop("installation", 40, epoch=1, listed=())
    assert ledger.stop_facts("undeclared") == (("installation", 40), ("installation", 40))
    assert ledger.standing_stop("installation") == (40, 1)


def test_a_resume_of_an_operation_leaves_its_project_stop_standing() -> None:
    ledger = _ledger()
    ledger.note_stop("project:P", 20, epoch=1, listed=())
    ledger.note_stop("operation:git-op", 21, epoch=1, listed=())
    ledger.note_resume("operation:git-op", 22)
    assert ledger.stop_facts("git-op")[0] == ("project:P", 20)


def test_an_intent_is_stopped_when_an_operation_declaring_it_is() -> None:
    ledger = _ledger()
    assert ledger.stop_of_intent("e" * 64) is None
    ledger.note_stop("project:Q", 30, epoch=1, listed=(("file-op", "b2"),))
    assert ledger.stop_of_intent("e" * 64) == ("project:Q", 30)
    assert ledger.stop_of_intent("d" * 64) is None
    assert ledger.stop_of_intent("f" * 64) is None


def test_the_open_attempts_of_a_scope_and_its_actors() -> None:
    ledger = _ledger()
    assert ledger.open_in("project:P") == (OpenAttempt("git-op", "a1", "T1", WORKER),)
    assert ledger.open_in("effect:file") == (
        OpenAttempt("file-op", "b1", "T2", "Q/worker"),
        OpenAttempt("file-op", "b2", "T2", "Q/worker"),
    )
    assert {item.attempt_id for item in ledger.open_in("installation")} == {"a1", "b1", "b2"}
    assert ledger.open_in("operation:missing") == ()
    assert ledger.actors("project:P") == frozenset({WORKER})
    assert ledger.actors("project:Q") == frozenset({"Q/worker"})
    ledger.note_verification("file-op", WORKER, effect="absent", result="not_verified")
    assert ledger.open_in("effect:file") == (OpenAttempt("file-op", "b2", "T2", "Q/worker"),)
    ledger.note_stop("installation", 50, epoch=1, listed=(("git-op", "a1"), ("file-op", "b2")))
    ledger.note_seal("git-op", "a1")
    assert ledger.open_in("project:P") == ()
    ledger.note_outcome("file-op", "Q/worker", "confirmed_done")
    assert ledger.actors("installation") == frozenset({WORKER, "Q/worker"})
    assert ledger.actors("project:none") == frozenset()
    ledger.open_attempt("file-op", "b3", executor="Q/worker", approval_seq=10)
    ledger.note_verification("file-op", WORKER, effect="observed", result="verified")
    assert ledger.open_in("effect:file") == ()


def test_the_epoch_counts_the_stops_of_a_scope() -> None:
    ledger = AttemptLedger(None)
    assert ledger.epoch("project:P") == 0
    ledger.note_stop("project:P", 5, epoch=1, listed=())
    ledger.note_resume("project:P", 6)
    assert ledger.epoch("project:P") == 1
    ledger.note_stop("project:P", 7, epoch=2, listed=())
    assert ledger.epoch("project:P") == 2


def test_a_published_candidate_carries_its_stops() -> None:
    ledger = _ledger()
    candidate = copy.deepcopy(ledger)
    candidate.note_stop("project:P", 20, epoch=1, listed=(("git-op", "a1"),))
    assert ledger.standing_stop("project:P") is None
    ledger.publish_from(candidate)
    assert ledger.standing_stop("project:P") == (20, 1)


def test_the_stops_are_restored_from_the_journal_and_a_malformed_row_is_skipped(
    tmp_path: Path,
) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        base = {"task_id": "T1", "sender": WORKER, "recorded_at": 1_800_000_000.0}
        store.append(EventKind.OPERATION_DECLARED, {**base, "operation_id": "git-op"})
        stop = {"scope": "project:P", "stopper": "P/person", "epoch": 1}
        listed = [
            {"operation_id": "git-op", "attempt_id": "a1"},
            {"operation_id": "git-op"},
            {"operation_id": 7, "attempt_id": "a2"},
            "not a row",
        ]
        stopped = store.append(EventKind.OPERATION_STOPPED, {**stop, "listed": listed})
        store.append(EventKind.OPERATION_STOPPED, {"stopper": "P/person"})
        store.append(EventKind.OPERATION_STOPPED, {"scope": "effect:file", "epoch": True})
        resumed = store.append(EventKind.OPERATION_RESUMED, {"scope": "effect:file"})
        restored = AttemptLedger(store)
    finally:
        store.close()
    assert restored.standing_stop("project:P") == (stopped, 1)
    assert restored.actors("project:P") == frozenset({WORKER})
    assert restored.standing_stop("effect:file") is None
    assert restored.epoch("effect:file") == 0
    assert restored.stop_facts("git-op") == (("project:P", stopped), ("project:P", stopped))
    assert resumed > stopped
    assert restored.operation("git-op") == OperationFact(
        "T1", WORKER, None, None, 1_800_000_000.0, None
    )


def test_the_stop_rows_have_words() -> None:
    stopped = {"scope": "project:P", "stopper": "A/b"}
    assert describe_event(EventKind.OPERATION_STOPPED, stopped) == EventWords(
        "A/b", "scope project:P stopped"
    )
    resumed = {"scope": "project:P", "resumer": "C/d"}
    assert describe_event(EventKind.OPERATION_RESUMED, resumed) == EventWords(
        "C/d", "scope project:P resumed"
    )
    answered = {"sender": "E/f", "request": "resume", "scope": "project:P", "answer": "not_stopped"}
    assert describe_event(EventKind.OPERATION_STOP_ANSWERED, answered) == EventWords(
        "E/f", "resume of project:P: not_stopped"
    )
