# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — autonomy levels per scope, the approver's kind, and the level line
"""A scope's level is a record with an epoch; an operation's is the lowest; a person is a grant."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.core.journal import EventKind
from synapse_channel.core.operation_attempt_ledger import AttemptLedger
from synapse_channel.core.operation_attempts import (
    APPROVAL_STALE,
    APPROVED_FRESH,
    ApprovalFact,
    AttemptFacts,
    AttemptRequest,
    OperationFact,
    decide,
)
from synapse_channel.core.operation_levels import (
    LEVELS,
    LevelRequest,
    OperationLevelError,
    approver_kind,
    decode_level_request,
    lowest,
    rank,
)
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.role_grants import RoleGrants

EFFECTS = ("file", "git_publication", "deployment", "migration")
WORKER = "P/worker"
NOW = 1_800_000_000.0
DIGEST = "d" * 64


def test_a_level_request_decodes_whole_and_a_malformed_one_says_why() -> None:
    frame = {"scope": "project:P", "level": "A2", "reason": "trusted", "idem_key": "k"}
    assert decode_level_request(frame, EFFECTS) == LevelRequest("project:P", "A2", "trusted", "k")
    cases: list[tuple[dict[str, Any], str]] = [
        ({"scope": "nowhere"}, "scope must be installation"),
        ({"level": "A6"}, "level must be one of"),
        ({"level": None}, "level must be one of"),
        ({"reason": ""}, "reason must be text of 1 to 512 characters"),
        ({"reason": "a\nb"}, "reason cannot contain control characters"),
        ({"idem_key": "k" * 257}, "idem_key must be text of 1 to 256 characters"),
    ]
    for changes, message in cases:
        with pytest.raises(OperationLevelError) as refused:
            decode_level_request({**frame, **changes}, EFFECTS)
        assert str(refused.value).startswith(message)
        assert refused.value.code == "operation_level"


def test_levels_rank_and_the_lowest_wins() -> None:
    assert LEVELS == ("A1", "A2", "A3", "A4", "A5")
    assert [rank(level) for level in LEVELS] == [0, 1, 2, 3, 4]
    assert rank("Z9") == 0
    assert lowest(["A3", "A2", "A5"]) == "A2"
    assert lowest([]) == "A1"


def test_a_person_is_whoever_the_role_grants_allow_its_project_s_person_role() -> None:
    grants = RoleGrants({"P/person": frozenset({"P/lead"}), "Q/person": frozenset({"Q/*"})})
    assert approver_kind(grants, "P/lead") == "person"
    assert approver_kind(grants, "P/approver") == "automated"
    assert approver_kind(grants, "Q/anyone") == "person"
    assert approver_kind(grants, "R/x") == "automated"
    assert approver_kind(None, "P/lead") == "automated"


def _fact(declarer: str = WORKER, effect: str = "file") -> OperationFact:
    return OperationFact("T1", declarer, DIGEST, None, NOW - 60, effect)


def test_the_ledger_keeps_each_scope_s_level_and_an_operation_s_lowest_and_line() -> None:
    ledger = AttemptLedger(None)
    ledger.note_declaration("op-1", _fact())
    assert ledger.level_of("project:P") == ("A1", 0)
    assert ledger.level_facts("op-1") == ("A1", None)
    ledger.note_level("operation:op-1", 10, level="A3", epoch=1)
    assert ledger.level_facts("op-1") == ("A1", ("operation:op-1", 10))
    ledger.note_level("project:P", 12, level="A2", epoch=1)
    ledger.note_level("effect:file", 14, level="A4", epoch=1)
    ledger.note_level("installation", 16, level="A5", epoch=1)
    assert ledger.level_facts("op-1") == ("A2", ("installation", 16))
    assert ledger.level_facts("undeclared") == ("A5", ("installation", 16))
    assert ledger.level_of("project:P") == ("A2", 1)


def test_a_scope_is_contained_by_its_wider_scopes_and_a_stop_within_is_found() -> None:
    ledger = AttemptLedger(None)
    ledger.note_declaration("op-1", _fact())
    assert ledger.scopes_containing("installation") == ("installation",)
    assert ledger.scopes_containing("project:P") == ("project:P", "installation")
    assert ledger.scopes_containing("operation:op-1") == (
        "operation:op-1",
        "effect:file",
        "project:P",
        "installation",
    )
    assert ledger.scopes_containing("operation:none") == ("operation:none", "installation")
    assert ledger.stopped_within("operation:op-1") is None
    ledger.note_stop("project:P", 20, epoch=1, listed=())
    assert ledger.stopped_within("operation:op-1") == ("project:P", 20)
    assert ledger.stopped_within("effect:file") is None


def test_the_levels_are_restored_and_a_malformed_row_is_skipped(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        first = store.append(
            EventKind.OPERATION_LEVEL, {"scope": "project:P", "level": "A2", "epoch": 1}
        )
        store.append(EventKind.OPERATION_LEVEL, {"scope": "project:P", "level": "A9", "epoch": 2})
        store.append(
            EventKind.OPERATION_LEVEL, {"scope": "project:P", "level": "A3", "epoch": True}
        )
        store.append(EventKind.OPERATION_LEVEL, {"level": "A3", "epoch": 2})
        restored = AttemptLedger(store)
    finally:
        store.close()
    assert restored.level_of("project:P") == ("A2", 1)
    assert restored.levels["project:P"].line == first


REQUEST = AttemptRequest("T1", "op-1", 3, DIGEST, "key-1")
APPROVAL = ApprovalFact(41, "P/approver", False, ())
FACTS = AttemptFacts(
    claim_refusal=None,
    operation=OperationFact("T1", WORKER, DIGEST, int(NOW) + 600, NOW - 60),
    gate_enabled=True,
    approval=APPROVAL,
    closed=False,
    attempt_open=False,
    approval_consumed=False,
    unreconciled=False,
    now=NOW,
)


def test_an_automated_approval_dies_with_a_level_change_and_a_person_s_does_not() -> None:
    later_level = replace(FACTS, level_line=("project:P", 42))
    stale = decide(REQUEST, later_level, requester=WORKER)
    assert (stale.answer, stale.detail) == (
        APPROVAL_STALE,
        "an automated approval older than the latest level change of scope project:P "
        "(journal seq 42)",
    )
    person = replace(later_level, approval=replace(APPROVAL, kind="person"))
    assert decide(REQUEST, person, requester=WORKER).answer == APPROVED_FRESH
    earlier_level = replace(FACTS, level_line=("project:P", 40))
    assert decide(REQUEST, earlier_level, requester=WORKER).answer == APPROVED_FRESH
    stopped_line = replace(person, stop_line=("project:P", 43))
    assert decide(REQUEST, stopped_line, requester=WORKER).detail.startswith(
        "the approval is older than the latest stop or resume"
    )
