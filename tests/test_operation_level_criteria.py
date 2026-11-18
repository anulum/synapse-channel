# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — a raise's criteria, the count the hub computes, and its window
"""The criteria name only countable items; the count reads the ledger after its window."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from synapse_channel.core.journal import EventKind
from synapse_channel.core.operation_attempt_ledger import (
    AttemptLedger,
    LevelCriteria,
    LevelJudgement,
)
from synapse_channel.core.operation_attempts import OperationFact
from synapse_channel.core.operation_level_criteria import (
    CriteriaItem,
    CriteriaRequest,
    JudgementRequest,
    LevelCriteriaError,
    RaiseRequest,
    criteria_digest,
    decode_criteria_request,
    decode_judgement_request,
    decode_raise_request,
    unmet,
)
from synapse_channel.core.persistence import EventStore

EFFECTS = ("file", "git_publication", "deployment", "migration")
DECLARER = "P/worker"
EXECUTOR = "P/executor"
CHECKER = "Q/checker"
NOW = 1_800_000_000.0
DIGEST = "d" * 64
VERIFIED = "verified_effects:file"
ITEMS = [
    {"item": VERIFIED, "min": 3},
    {"item": "incorrect_acceptances", "max": 0},
    {"item": "unknown_outcomes", "max": 0},
    {"item": "stops_at_rest", "min": 1},
]


def _declare(
    ledger: AttemptLedger,
    operation_id: str,
    *,
    declarer: str = DECLARER,
    deadline: int | None = None,
) -> None:
    ledger.note_declaration(
        operation_id,
        OperationFact(
            task_id="T1",
            declarer=declarer,
            intent_sha256=DIGEST,
            deadline=deadline,
            declared_at=NOW,
            effect="file",
            assurance="cooperative",
        ),
    )


def _verify(ledger: AttemptLedger, operation_id: str, result: str, seq: int, sender: str) -> None:
    ledger.note_verification(operation_id, sender, effect="observed", result=result, seq=seq)


def test_criteria_decode_whole_and_a_malformed_one_says_why() -> None:
    frame: dict[str, Any] = {"scope": "project:P", "target": "A2", "items": ITEMS, "idem_key": "k"}
    request = decode_criteria_request(frame, EFFECTS)
    assert request == CriteriaRequest(
        "project:P",
        "A2",
        (
            CriteriaItem(VERIFIED, 3, False),
            CriteriaItem("incorrect_acceptances", 0, True),
            CriteriaItem("unknown_outcomes", 0, True),
            CriteriaItem("stops_at_rest", 1, False),
        ),
        "k",
    )
    assert [item.to_json() for item in request.items] == ITEMS
    named = "names exactly item and a"
    cases: list[tuple[dict[str, Any], str]] = [
        ({"scope": "nowhere"}, "scope must be installation"),
        ({"target": "A6"}, "target must be one of"),
        ({"items": []}, "items must list 1 to 16 countable items"),
        ({"items": [ITEMS[0]] * 17}, "items must list 1 to 16 countable items"),
        ({"items": [VERIFIED]}, "each item is an object"),
        ({"items": [{"item": "verified_effects:rocket", "min": 1}]}, "an item is not in the"),
        ({"items": [{"item": "conformance_cases", "min": 1}]}, "an item is not in the"),
        ({"items": [{"item": "incorrect_acceptances", "min": 0}]}, "item incorrect_acceptances"),
        ({"items": [{"item": "stops_at_rest", "min": True}]}, f"item stops_at_rest {named}"),
        ({"items": [{"item": "stops_at_rest", "min": -1}]}, f"item stops_at_rest {named}"),
        ({"items": [{"item": "stops_at_rest", "min": 1, "why": "x"}]}, "item stops_at_rest"),
        ({"items": [ITEMS[3], {"item": "stops_at_rest", "min": 2}]}, "each item is counted once"),
        ({"idem_key": ""}, "idem_key must be text of 1 to 256 characters"),
    ]
    for changes, message in cases:
        with pytest.raises(LevelCriteriaError) as refused:
            decode_criteria_request({**frame, **changes}, EFFECTS)
        assert str(refused.value).startswith(message), changes
        assert refused.value.code == "level_criteria"


def test_a_judgement_and_a_raise_decode_and_a_malformed_one_says_why() -> None:
    digest = "a" * 64
    judged = {"scope": "project:P", "digest": digest, "verdict": "not_met", "idem_key": "k"}
    assert decode_judgement_request(judged, EFFECTS) == JudgementRequest(
        "project:P", digest, "not_met", "k"
    )
    wrong: list[tuple[dict[str, Any], str]] = [
        ({"scope": None}, "scope must be installation"),
        ({"digest": "A" * 64}, "digest must be 64 lower-case hexadecimal characters"),
        ({"digest": "a" * 63}, "digest must be 64 lower-case hexadecimal characters"),
        ({"digest": 7}, "digest must be 64 lower-case hexadecimal characters"),
        ({"verdict": "maybe"}, "verdict must be one of"),
        ({"idem_key": None}, "idem_key must be text"),
    ]
    for changes, message in wrong:
        with pytest.raises(LevelCriteriaError) as refused:
            decode_judgement_request({**judged, **changes}, EFFECTS)
        assert str(refused.value).startswith(message), changes
    frame = {"scope": "project:P", "target": "A2", "digest": digest, "judgement_seq": 7}
    raised = decode_raise_request({**frame, "idem_key": "k"}, EFFECTS)
    assert raised == RaiseRequest("project:P", "A2", digest, 7, "k")
    for seq in (0, -1, True, "7", 2**53, None):
        with pytest.raises(LevelCriteriaError, match="judgement_seq must be a journal sequence"):
            decode_raise_request({**frame, "judgement_seq": seq, "idem_key": "k"}, EFFECTS)
    with pytest.raises(LevelCriteriaError, match="target must be one of"):
        decode_raise_request({**frame, "target": "A0", "idem_key": "k"}, EFFECTS)


def test_the_digest_is_of_the_canonical_criteria_and_unmet_names_each_shortfall() -> None:
    items = (CriteriaItem(VERIFIED, 2, False), CriteriaItem("unknown_outcomes", 0, True))
    first = criteria_digest("project:P", "A2", items)
    assert first == criteria_digest("project:P", "A2", items)
    others = {
        criteria_digest("project:P", "A3", items),
        criteria_digest("project:Q", "A2", items),
        criteria_digest("project:P", "A2", items[:1]),
        criteria_digest("project:P", "A2", (CriteriaItem(VERIFIED, 3, False), items[1])),
    }
    assert first not in others and len(others) == 4
    assert unmet(items, {VERIFIED: 2, "unknown_outcomes": 0}) == ()
    assert unmet(items, {"unknown_outcomes": 1}) == (
        "verified_effects:file: 0, needs at least 2",
        "unknown_outcomes: 1, needs at most 0",
    )


def test_only_what_closed_inside_the_window_counts_and_never_the_actor_s_own_word() -> None:
    """G3 and G7: evidence before the criteria, a declarer's verification, an outcome report."""
    ledger = AttemptLedger(None)
    for name in ("op-before", "op-after", "op-self", "op-reported"):
        _declare(ledger, name)
        ledger.open_attempt(name, f"{name}-a", executor=EXECUTOR, approval_seq=2)
    _verify(ledger, "op-before", "verified", 5, CHECKER)
    _verify(ledger, "op-after", "verified", 12, CHECKER)
    _verify(ledger, "op-self", "verified", 13, DECLARER)
    ledger.note_outcome("op-reported", EXECUTOR, "confirmed_done", seq=14)
    assert ledger.level_counts("project:P", 10, NOW) == {
        VERIFIED: 1,
        "incorrect_acceptances": 0,
        "unknown_outcomes": 0,
        "stops_at_rest": 0,
    }
    assert ledger.level_counts("project:P", 0, NOW)[VERIFIED] == 2
    assert VERIFIED not in ledger.level_counts("project:Q", 0, NOW)
    assert ledger.level_counts("effect:file", 10, NOW)[VERIFIED] == 1


def test_unknown_outcomes_and_incorrect_acceptances_count_inside_the_window() -> None:
    ledger = AttemptLedger(None)
    _declare(ledger, "op-unknown")
    ledger.open_attempt("op-unknown", "a1", executor=EXECUTOR, approval_seq=11)
    ledger.note_outcome("op-unknown", EXECUTOR, "unknown", seq=12)
    ledger.note_outcome("op-unknown", CHECKER, "unknown", seq=13)
    ledger.note_outcome("op-unknown", EXECUTOR, "unknown", seq=4)
    _declare(ledger, "op-late", deadline=int(NOW) - 1)
    ledger.open_attempt("op-late", "a2", executor=EXECUTOR, approval_seq=15)
    _declare(ledger, "op-late-early", deadline=int(NOW) - 1)
    ledger.open_attempt("op-late-early", "a3", executor=EXECUTOR, approval_seq=6)
    _declare(ledger, "op-late-closed", deadline=int(NOW) - 1)
    ledger.open_attempt("op-late-closed", "a4", executor=EXECUTOR, approval_seq=16)
    _verify(ledger, "op-late-closed", "verified", 17, CHECKER)
    _declare(ledger, "op-wrong")
    _verify(ledger, "op-wrong", "not_verified", 9, CHECKER)
    _verify(ledger, "op-wrong", "verified", 18, CHECKER)
    _verify(ledger, "op-wrong", "not_verified", 19, DECLARER)
    _verify(ledger, "op-wrong", "not_verified", 20, CHECKER)
    _verify(ledger, "op-wrong", "not_verified", 21, CHECKER)
    _declare(ledger, "op-wrong-early")
    _verify(ledger, "op-wrong-early", "verified", 3, CHECKER)
    _verify(ledger, "op-wrong-early", "not_verified", 8, CHECKER)
    assert ledger.level_counts("project:P", 10, NOW) == {
        VERIFIED: 2,
        "incorrect_acceptances": 1,
        "unknown_outcomes": 2,
        "stops_at_rest": 0,
    }
    assert ledger.level_counts("project:P", 10, NOW - 10)["unknown_outcomes"] == 1
    assert ledger.level_counts("project:P", 0, NOW)["incorrect_acceptances"] == 2


def test_stops_at_rest_of_containing_scopes_count_and_a_step_down_moves_the_window() -> None:
    ledger = AttemptLedger(None)
    _declare(ledger, "op-1")
    ledger.open_attempt("op-1", "a1", executor=EXECUTOR, approval_seq=3)
    ledger.note_stop("project:P", 5, epoch=1, listed=())
    ledger.note_stop("project:P", 11, epoch=2, listed=())
    ledger.note_stop("installation", 12, epoch=1, listed=())
    ledger.note_stop("project:Q", 13, epoch=1, listed=())
    ledger.note_stop("operation:op-1", 14, epoch=1, listed=(("op-1", "a1"),))
    assert ledger.level_counts("project:P", 10, NOW)["stops_at_rest"] == 2
    assert ledger.level_counts("operation:op-1", 10, NOW)["stops_at_rest"] == 2
    assert ledger.level_counts("installation", 10, NOW)["stops_at_rest"] == 1
    assert ledger.window_start("project:P", 10) == 10
    ledger.note_level("project:P", 20, level="A3", epoch=1)
    assert ledger.window_start("project:P", 10) == 10
    ledger.note_level("project:P", 30, level="A2", epoch=2)
    assert (ledger.window_start("project:P", 10), ledger.window_start("project:P", 40)) == (30, 40)
    ledger.note_level("project:P", 50, level="A3", epoch=3)
    assert ledger.window_start("project:P", 10) == 30
    assert ledger.window_start("project:Q", 10) == 10


def test_the_actors_are_the_declarers_and_executors_of_what_the_window_counts() -> None:
    ledger = AttemptLedger(None)
    _declare(ledger, "op-old", declarer="P/old")
    ledger.open_attempt("op-old", "a1", executor="P/old-executor", approval_seq=3)
    _verify(ledger, "op-old", "verified", 4, CHECKER)
    _declare(ledger, "op-new")
    ledger.open_attempt("op-new", "a2", executor=EXECUTOR, approval_seq=12)
    _declare(ledger, "op-closed", declarer="P/second")
    ledger.open_attempt("op-closed", "a3", executor="P/second-executor", approval_seq=3)
    _verify(ledger, "op-closed", "verified", 15, CHECKER)
    _declare(ledger, "op-unknown", declarer="P/third")
    ledger.note_outcome("op-unknown", "P/third", "unknown", seq=16)
    _declare(ledger, "op-contradicted", declarer="P/fourth")
    _verify(ledger, "op-contradicted", "verified", 2, CHECKER)
    _verify(ledger, "op-contradicted", "not_verified", 17, CHECKER)
    assert ledger.level_actors("project:P", 10) == {
        DECLARER,
        EXECUTOR,
        "P/second",
        "P/second-executor",
        "P/fourth",
    }
    assert ledger.level_actors("project:Q", 0) == frozenset()


def test_criteria_and_judgements_are_restored_and_a_malformed_row_is_skipped(
    tmp_path: Path,
) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        criteria = store.append(
            EventKind.LEVEL_CRITERIA,
            {
                "scope": "project:P",
                "target": "A2",
                "items": [*ITEMS[:2], {"item": "stops_at_rest", "min": "x"}, "junk"],
                "digest": DIGEST,
                "declared_by": "H/operator",
            },
        )
        store.append(EventKind.LEVEL_CRITERIA, {"scope": "project:P", "digest": "e" * 64})
        store.append(EventKind.LEVEL_CRITERIA, {"scope": "project:P", "target": "A2"})
        judged = store.append(
            EventKind.LEVEL_JUDGEMENT, {"digest": DIGEST, "verdict": "met", "judge": "R/judge"}
        )
        store.append(EventKind.LEVEL_JUDGEMENT, {"digest": DIGEST, "verdict": "met"})
        ledger = AttemptLedger(store)
    finally:
        store.close()
    items = (
        CriteriaItem(VERIFIED, 3, False),
        CriteriaItem("incorrect_acceptances", 0, True),
        CriteriaItem("stops_at_rest", 0, False),
    )
    assert ledger.criteria == {
        DIGEST: LevelCriteria(criteria, "project:P", "A2", items, "H/operator")
    }
    assert ledger.judgements == {judged: LevelJudgement(DIGEST, "met", "R/judge")}
    candidate = AttemptLedger(None)
    candidate.note_criteria(DIGEST, LevelCriteria(99, "project:Q", "A3", (), "H/other"))
    candidate.publish_from(ledger)
    assert candidate.criteria[DIGEST].seq == criteria
