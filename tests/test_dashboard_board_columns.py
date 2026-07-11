# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — board-column projection contract regressions

from __future__ import annotations

from collections.abc import Mapping

from synapse_channel.core.ledger import Blackboard
from synapse_channel.dashboard_board_columns import (
    COLUMN_SPECS,
    TRUST_BOUNDARY,
    build_board_columns,
)


def _columns(projection: Mapping[str, object]) -> list[Mapping[str, object]]:
    value = projection["columns"]
    assert isinstance(value, list)
    assert all(isinstance(item, Mapping) for item in value)
    return value


def _cards(projection: Mapping[str, object]) -> dict[str, Mapping[str, object]]:
    cards: dict[str, Mapping[str, object]] = {}
    for column in _columns(projection):
        rows = column["tasks"]
        assert isinstance(rows, list)
        for row in rows:
            assert isinstance(row, Mapping)
            cards[str(row["task_id"])] = row
    return cards


def _column_members(projection: Mapping[str, object]) -> dict[str, list[str]]:
    members: dict[str, list[str]] = {}
    for column in _columns(projection):
        rows = column["tasks"]
        assert isinstance(rows, list)
        members[str(column["id"])] = [
            str(row["task_id"]) for row in rows if isinstance(row, Mapping)
        ]
    return members


def test_exact_board_and_claim_statuses_drive_stable_columns() -> None:
    board: dict[str, object] = {
        "tasks": [
            {"task_id": "01-open", "title": "ready", "status": "open"},
            {
                "task_id": "02-waiting",
                "title": "waiting",
                "status": "open",
                "depends_on": ["missing", "closed", "cancelled"],
            },
            {"task_id": "03-claimed", "title": "claimed", "status": "open"},
            {"task_id": "04-working", "title": "working", "status": "in_progress"},
            {"task_id": "05-input", "title": "input", "status": "in_progress"},
            {"task_id": "06-blocked", "title": "blocked", "status": "blocked"},
            {"task_id": "07-done", "title": "done", "status": "done"},
            {"task_id": "08-cancelled", "title": "cancelled", "status": "cancelled"},
            {"task_id": "09-failed", "title": "failed", "status": "open"},
            {"task_id": "10-claim-done", "title": "closed claim", "status": "blocked"},
            {"task_id": "11-board-working", "title": "board work", "status": "in_progress"},
            {"task_id": "12-board-future", "title": "future board", "status": "review"},
            {"task_id": "13-claim-future", "title": "future claim", "status": "open"},
            {"task_id": "closed", "title": "dependency", "status": "done"},
            {"task_id": "cancelled", "title": "dependency", "status": "cancelled"},
        ],
        "ready": ["01-open", "03-claimed", "01-open"],
    }
    state: dict[str, object] = {
        "generated_at": 100.0,
        "active_claims": [
            {"task_id": "03-claimed", "owner": "a", "status": "claimed"},
            {"task_id": "04-working", "owner": "b", "status": "working"},
            {"task_id": "05-input", "owner": "c", "status": "input_required"},
            {"task_id": "06-blocked", "owner": "d", "status": "claimed"},
            {"task_id": "07-done", "owner": "e", "status": "working"},
            {"task_id": "09-failed", "owner": "f", "status": "failed"},
            {"task_id": "10-claim-done", "owner": "g", "status": "done"},
            {"task_id": "13-claim-future", "owner": "h", "status": "review"},
            {
                "task_id": "14-ad-hoc",
                "owner": "i",
                "note": "undeclared claim",
                "status": "claimed",
            },
            {"task_id": "15-empty-ad-hoc", "owner": "j", "status": ""},
        ],
    }

    projection = build_board_columns(board, state)

    assert [column["id"] for column in _columns(projection)] == [
        column_id for column_id, _label in COLUMN_SPECS
    ]
    assert _column_members(projection) == {
        "open": ["01-open", "02-waiting"],
        "claimed": ["03-claimed", "14-ad-hoc"],
        "working": ["04-working", "11-board-working"],
        "input_required": ["05-input"],
        "blocked": ["06-blocked"],
        "closed": [
            "07-done",
            "08-cancelled",
            "09-failed",
            "10-claim-done",
            "cancelled",
            "closed",
        ],
        "other": ["12-board-future", "13-claim-future", "15-empty-ad-hoc"],
    }
    cards = _cards(projection)
    assert cards["01-open"]["ready"] is True
    assert cards["02-waiting"]["blocked_by"] == ["missing"]
    assert cards["02-waiting"]["depends_on"] == ["cancelled", "closed", "missing"]
    assert cards["06-blocked"]["status_source"] == "board"
    assert cards["07-done"]["status_source"] == "board"
    assert cards["09-failed"]["status_source"] == "claim"
    assert cards["10-claim-done"]["status_source"] == "claim"
    assert cards["13-claim-future"]["status_source"] == "claim"
    assert cards["14-ad-hoc"]["declared"] is False
    assert cards["14-ad-hoc"]["title"] == "undeclared claim"
    assert cards["15-empty-ad-hoc"]["status_source"] == "none"
    assert projection["declared_tasks"] == 15
    assert projection["ad_hoc_claims"] == 2
    assert projection["total_cards"] == 17
    assert projection["trust_boundary"] == TRUST_BOUNDARY


def test_card_metadata_and_lease_freshness_preserve_source_facts() -> None:
    board: dict[str, object] = {
        "tasks": [
            {
                "task_id": "task",
                "title": "  exact title  ",
                "status": "open",
                "depends_on": [" b ", "a", "a", "", None, 7, True],
                "suggested_owner": "  reviewer  ",
            }
        ],
        "ready": ["task"],
    }
    state: dict[str, object] = {
        "generated_at": "20",
        "active_claims": [
            {
                "task_id": "task",
                "owner": "  operator  ",
                "status": "working",
                "lease_expires_at": "20",
                "paths": ["tests/", "src/", "tests/", "", None, 7, True],
            }
        ],
    }

    card = _cards(build_board_columns(board, state))["task"]

    assert card["title"] == "exact title"
    assert card["suggested_owner"] == "reviewer"
    assert card["owner"] == "operator"
    assert card["depends_on"] == ["a", "b"]
    assert card["blocked_by"] == ["a", "b"]
    assert card["paths"] == ["src/", "tests/"]
    assert card["lease_expires_at"] == 20.0
    assert card["lease_stale"] is True
    assert _cards(build_board_columns(board, state, now=19.0))["task"]["lease_stale"] is False


def test_malformed_rows_and_scalars_degrade_without_fabricating_counts() -> None:
    board: dict[str, object] = {
        "tasks": [
            "not-a-row",
            {"task_id": "", "status": "open"},
            {"task_id": 7, "title": "not a string id", "status": "open"},
            {"task_id": "same", "title": "first", "status": "open"},
            {"task_id": "same", "title": "second", "status": "blocked"},
        ],
        "ready": "same",
        "total_tasks": True,
        "truncated": 1,
    }
    state: dict[str, object] = {
        "generated_at": float("inf"),
        "active_claims": [
            None,
            {"task_id": "same", "owner": "first", "status": "claimed"},
            {"task_id": "same", "owner": "second", "status": "working"},
            {
                "task_id": "bad-lease",
                "status": "claimed",
                "lease_expires_at": True,
                "paths": "src/",
            },
            {
                "task_id": "nan-lease",
                "status": "claimed",
                "lease_expires_at": "nan",
            },
            {
                "task_id": "text-lease",
                "status": "claimed",
                "lease_expires_at": "later",
            },
            {
                "task_id": "bad-scalars",
                "owner": 7,
                "note": True,
                "status": False,
                "paths": [None, 7, True],
            },
        ],
    }

    projection = build_board_columns(board, state, now=float("inf"))
    cards = _cards(projection)

    assert cards["same"]["title"] == "first"
    assert cards["same"]["owner"] == "first"
    assert cards["same"]["ready"] is False
    assert cards["bad-lease"]["lease_expires_at"] is None
    assert cards["nan-lease"]["lease_expires_at"] is None
    assert cards["text-lease"]["lease_expires_at"] is None
    assert cards["bad-scalars"]["title"] == ""
    assert cards["bad-scalars"]["owner"] == ""
    assert cards["bad-scalars"]["claim_status"] == ""
    assert cards["bad-scalars"]["paths"] == []
    assert projection["declared_tasks"] == 1
    assert projection["ad_hoc_claims"] == 4
    assert projection["total_declared_tasks"] == 1
    assert projection["source_truncated"] is False


def test_source_completeness_metadata_is_bounded_and_fail_visible() -> None:
    task = {"task_id": "only", "title": "only", "status": "open"}

    smaller = build_board_columns(
        {"tasks": [task], "total_tasks": -3, "truncated": True},
        {"active_claims": [], "generated_at": 1},
    )
    larger = build_board_columns(
        {"tasks": [task], "total_tasks": 9},
        {"active_claims": "invalid", "generated_at": object()},
    )
    empty = build_board_columns({"tasks": "invalid"}, {"active_claims": None})

    assert smaller["total_declared_tasks"] == 1
    assert smaller["source_truncated"] is True
    assert larger["total_declared_tasks"] == 9
    assert empty["total_cards"] == 0
    assert all(column["tasks"] == [] for column in _columns(empty))


def test_capped_blackboard_snapshot_never_fabricates_dependency_evidence() -> None:
    satisfied = Blackboard()
    satisfied.post_task(task_id="done", title="Done", author="P", now=1.0)
    satisfied.update_task("done", status="done", now=2.0)
    satisfied.post_task(task_id="ready", title="Ready", author="P", depends_on=["done"], now=3.0)
    ready_projection = build_board_columns(
        satisfied.snapshot(task_cap=1), {"active_claims": [], "generated_at": 4.0}
    )
    ready_card = _cards(ready_projection)["ready"]
    assert ready_card["ready"] is True
    assert ready_card["blocked_by"] == []
    assert ready_card["unknown_dependencies"] == []

    unresolved = Blackboard()
    unresolved.post_task(task_id="open", title="Open", author="P", now=1.0)
    unresolved.post_task(
        task_id="waiting", title="Waiting", author="P", depends_on=["open"], now=2.0
    )
    waiting_projection = build_board_columns(
        unresolved.snapshot(task_cap=1), {"active_claims": [], "generated_at": 3.0}
    )
    waiting_card = _cards(waiting_projection)["waiting"]
    assert waiting_card["ready"] is False
    assert waiting_card["blocked_by"] == []
    assert waiting_card["unknown_dependencies"] == ["open"]
