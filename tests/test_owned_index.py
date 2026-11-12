# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — historical assignment index contracts
"""Exercise indexed owner isolation, complete records and rollback generation fences."""

from __future__ import annotations

import hashlib
import json
import time
from copy import deepcopy
from dataclasses import replace
from typing import Any

import pytest

from synapse_channel.core.ledger import Blackboard
from synapse_channel.core.owned_index import OwnedWorkIndex
from synapse_channel.core.owned_pages import read_owned_work
from synapse_channel.core.owned_work import OwnedQuery
from synapse_channel.core.state import SynapseState
from synapse_channel.core.state_transaction import durable_state_transaction
from test_pending_index import claim, task


def test_seek_isolation_moves_and_exact_rollback_revision() -> None:
    index = OwnedWorkIndex("tasks")
    empty = index.revision("ALPHA/worker", "ALPHA")
    index.remove("absent")
    index.set("ALPHA/task", "ALPHA/worker", "ALPHA")
    entry = index.entries["ALPHA/task"]
    before = index.revision("ALPHA/worker", "ALPHA")
    for n in range(1000):
        index.set(f"BETA/{n}", "BETA/worker", "BETA")
    assert index.revision("ALPHA/worker", "ALPHA") == before
    assert index.page("ALPHA/worker", "ALPHA", after_key="", limit=1) == (entry,)
    assert not index.page("ALPHA/worker", "ALPHA", after_key=entry.key, limit=1)
    assert not index.page("ALPHA/worker", "BETA", after_key="", limit=1)
    assert index.get("ALPHA/worker", None, entry.key) == entry
    index.set(entry.task_id, entry.owner, entry.project)
    assert index.revision(entry.owner, entry.project) != before
    index.restore(entry.task_id, entry)
    assert index.revision(entry.owner, entry.project) == before
    index.set(entry.task_id, "ALPHA/other", "ALPHA")
    assert not index.get(entry.owner, entry.project, entry.key)
    index.restore(entry.task_id, entry)
    assert index.revision(entry.owner, entry.project) == before
    for wrong in (replace(entry, task_id="wrong"), replace(entry, kind="claims")):
        with pytest.raises(ValueError, match="identity changed"):
            index.restore(entry.task_id, wrong)
    index.restore(entry.task_id, None)
    assert index.revision(entry.owner, entry.project) == empty
    index.set("none", "", "")
    assert "none" not in index.entries
    assert OwnedWorkIndex("tasks").revision(entry.owner, entry.project) != empty


@pytest.mark.parametrize("kind", ["invalid"])
def test_invalid_index_family_refuses(kind: Any) -> None:
    with pytest.raises(ValueError, match="family"):
        OwnedWorkIndex(kind)


def test_current_board_and_claim_generations_follow_lifecycle_and_publication() -> None:
    board, state = Blackboard(), SynapseState()
    board.restore_task(task())
    old = board.owned_tasks.entries["ALPHA/old"]
    assert board.update_task("ALPHA/old", status="blocked")[0]
    assert board.owned_tasks.entries[old.task_id].version != old.version
    board.publish_from(deepcopy(board))
    assert board.update_task("ALPHA/old", status="done")[0]
    assert not board.owned_tasks.entries
    board.restore_task(task(project=""))
    assert board.owned_tasks.entries[old.task_id].project == ""
    state.restore_claim(claim(lease_expires_at=time.time() + 600))
    state.reindex_leases()
    state.publish_from(deepcopy(state))
    old_claim = state.owned_claims.entries["ALPHA/handoff"]
    before = state.owned_claims.revision("ALPHA/worker", "ALPHA")
    with pytest.raises(RuntimeError, match="durable append refused"):
        with durable_state_transaction(state, old_claim.task_id, enabled=True):
            assert state.save_checkpoint("ALPHA/worker", old_claim.task_id, "next", now=10)[0]
            raise RuntimeError("durable append refused")
    assert state.owned_claims.entries[old_claim.task_id] == old_claim
    assert state.owned_claims.revision("ALPHA/worker", "ALPHA") == before
    state.restore_claim(claim())
    state.restore_release(old_claim.task_id)
    assert not state.owned_claims.entries
    state.restore_claim(claim(status="done"))
    assert not state.owned_claims.entries


def test_pages_and_lossless_unicode_chunks_refuse_stale_or_foreign_reads() -> None:
    board, state = Blackboard(), SynapseState()
    body = "Žiadna strata 🌍 " * 4000
    board.restore_task(task(description=body))
    board.restore_task(task("bare", project=""))
    state.restore_claim(claim(checkpoint=body))
    state.restore_claim(claim(task_id="ALPHA/expired", lease_expires_at=5))
    query = OwnedQuery("ALPHA/worker", project="ALPHA", limit=1)
    pages: list[dict[str, Any]] = []
    page: dict[str, Any]
    while True:
        page = read_owned_work(board, state, query, hub_id="hub", now=10)
        pages.append(page)
        if not page["has_more"]:
            break
        assert isinstance(page["cursor"], str) and isinstance(page["revision"], str)
        query = replace(query, hub_id="hub", after_key=page["cursor"], revision=page["revision"])
    items = [item for page in pages for item in page["items"]]
    assert {x["kind"] for x in items} == {"tasks", "claims"} and len(items) == 2
    assert any(not x["items"] for x in pages)
    unscoped: dict[str, Any] = read_owned_work(
        board, state, OwnedQuery("ALPHA/worker"), hub_id="hub", now=10
    )
    assert len(unscoped["items"]) == 3
    for item in items:
        query = OwnedQuery(
            "ALPHA/worker",
            hub_id="hub",
            project="ALPHA",
            mode="read",
            key=item["key"],
            item_version=item["item_version"],
        )
        chunks: list[str] = []
        first: dict[str, Any] | None = None
        while True:
            page = read_owned_work(board, state, query, hub_id="hub", now=10)
            assert read_owned_work(board, state, query, hub_id="hub", now=10) == page
            assert isinstance(page["content"], str)
            chunks.append(page["content"])
            if first is None:
                first = page
            if not page["has_more"]:
                break
            assert isinstance(page["next_offset"], int) and isinstance(page["content_sha256"], str)
            query = replace(query, offset=page["next_offset"], digest=page["content_sha256"])
        assert first is not None
        joined = "".join(chunks)
        assert hashlib.sha256(joined.encode("ascii")).hexdigest() == first["content_sha256"]
        record = json.loads(joined)
        assert record["description" if item["kind"] == "tasks" else "checkpoint"] == body
        assert "assignment_origin" not in record and "quota_principal" not in record
        for changes in (
            {"identity": "ALPHA/other"},
            {"item_version": "0" * 64},
            {"key": "0" * 64},
            {"hub_id": "other"},
            {"digest": "0" * 64},
            {"offset": len(joined)},
        ):
            with pytest.raises(ValueError):
                read_owned_work(board, state, replace(query, **changes), hub_id="hub", now=10)
    revision = pages[0]["revision"]
    assert board.update_task("ALPHA/old", status="blocked")[0]
    with pytest.raises(ValueError, match="restart"):
        read_owned_work(
            board,
            state,
            OwnedQuery("ALPHA/worker", hub_id="hub", project="ALPHA", revision=revision),
            hub_id="hub",
            now=10,
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"identity": None},
        {"identity": ""},
        {"identity": "x" * 257},
        {"identity": " ALPHA/worker"},
        {"hub_id": None},
        {"hub_id": "x" * 257},
        {"project": 1},
        {"project": ""},
        {"project": "A/B"},
        {"project": "BETA"},
        {"mode": "unknown"},
        {"limit": True},
        {"limit": 0},
        {"limit": 101},
        {"offset": True},
        {"offset": -1},
        {"offset": 2**63},
        {"limit_chars": True},
        {"limit_chars": 0},
        {"limit_chars": 32769},
        {"key": "private"},
        {"revision": None},
        {"after_key": "a" * 64},
        {"after_key": "a" * 64, "hub_id": "hub"},
        {"key": "a" * 64},
        {"item_version": "a" * 64},
        {"offset": 1},
        {"digest": "a" * 64},
        {"limit_chars": 1},
        {"mode": "read"},
        {"mode": "read", "hub_id": "hub"},
        {"mode": "read", "hub_id": "hub", "key": "a" * 64},
        {"mode": "read", "hub_id": "hub", "key": "a" * 64, "item_version": "b" * 64, "offset": 1},
    ],
)
def test_invalid_owned_query_refuses(changes: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        OwnedQuery(**({"identity": "ALPHA/worker"} | changes))


@pytest.mark.parametrize("value", [[], {}, {"version": True}, {"version": 2}])
def test_exact_query_wire_schema_refuses(value: object) -> None:
    with pytest.raises(ValueError):
        OwnedQuery.from_value(value)


def test_query_roundtrip_requires_mode_exclusive_arguments() -> None:
    for query in (
        OwnedQuery("ALPHA/worker"),
        OwnedQuery("ALPHA/worker", mode="read", hub_id="hub", key="a" * 64, item_version="b" * 64),
    ):
        assert OwnedQuery.from_value(query.encode()) == query
    for changes in ({"after_key": "a" * 64}, {"revision": "a" * 64}, {"limit": 1}):
        with pytest.raises(ValueError):
            replace(query, **changes)


def test_expiry_and_changed_projection_refuse_stale_index_records() -> None:
    board, state = Blackboard(), SynapseState()
    state.restore_claim(claim(lease_expires_at=15))
    entry = state.owned_claims.entries["ALPHA/handoff"]
    query = OwnedQuery(
        "ALPHA/worker", hub_id="hub", mode="read", key=entry.key, item_version=entry.version
    )
    assert read_owned_work(board, state, query, hub_id="hub", now=10)["available"]
    with pytest.raises(ValueError, match="no longer"):
        read_owned_work(board, state, query, hub_id="hub", now=15)
    board.restore_task(task())
    entry = board.owned_tasks.entries["ALPHA/old"]
    query = replace(query, key=entry.key, item_version=entry.version)
    board.tasks[entry.task_id].suggested_owner = "ALPHA/other"
    with pytest.raises(ValueError, match="no longer"):
        read_owned_work(board, state, query, hub_id="hub", now=10)


def test_removing_one_owned_record_preserves_other_projects_and_remaining_pages() -> None:
    index = OwnedWorkIndex("tasks")
    index.set("ALPHA/one", "ALPHA/worker", "ALPHA")
    index.set("ALPHA/two", "ALPHA/worker", "ALPHA")
    index.set("BETA/three", "ALPHA/worker", "BETA")
    second = index.entries["ALPHA/two"]
    third = index.entries["BETA/three"]
    beta_revision = index.revision("ALPHA/worker", "BETA")
    index.remove("ALPHA/one")
    assert index.get("ALPHA/worker", "ALPHA", second.key) == second
    assert index.get("ALPHA/worker", None, third.key) == third
    assert index.revision("ALPHA/worker", "BETA") == beta_revision
    index.remove("ALPHA/two")
    assert not index.page("ALPHA/worker", "ALPHA", after_key="", limit=100)
    assert index.page("ALPHA/worker", None, after_key="", limit=100) == (third,)
