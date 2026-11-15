# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — the dashboard's scope: one rule per record, never inferred
"""Narrow the fleet snapshot to one project, or to what has none, and count both sides."""

from __future__ import annotations

import json

import pytest

from dashboard_scope_fleet import scope_fleet
from synapse_channel.dashboard_scope import (
    SCOPE_FORMS,
    WHOLE_HUB,
    DashboardScope,
    parse_scope,
    scope_snapshot,
    scope_summary,
    scoped_document,
)
from synapse_channel.dashboard_scope_groups import parse_scope_groups


def test_a_request_without_a_scope_asks_for_the_whole_hub() -> None:
    assert parse_scope([]) is WHOLE_HUB
    assert parse_scope(["all"]) == WHOLE_HUB
    assert parse_scope(["none"]) == DashboardScope("none")
    assert parse_scope(["project:SYNAPSE-CHANNEL"]) == DashboardScope("project", "SYNAPSE-CHANNEL")
    assert [scope.text for scope in (WHOLE_HUB, DashboardScope("none"))] == ["all", "none"]
    assert DashboardScope("project", "ALPHA").text == "project:ALPHA"


@pytest.mark.parametrize(
    ("scope", "held"),
    [
        (WHOLE_HUB, {"ALPHA", "BETA", ""}),
        (DashboardScope("project", "ALPHA"), {"ALPHA"}),
        (DashboardScope("none"), {""}),
    ],
)
def test_each_scope_holds_exactly_its_namespaces(scope: DashboardScope, held: set[str]) -> None:
    assert {namespace for namespace in ("ALPHA", "BETA", "") if scope.holds(namespace)} == held


@pytest.mark.parametrize(
    "values",
    [
        ["all", "none"],
        ["group:"],
        ["group:a/b"],
        ["peer:"],
        ["peer:hub/2"],
        ["project:"],
        ["project:ALPHA/worker"],
        ["project:ALPHA\x00"],
        ["project:" + "x" * 257],
        ["PROJECT:ALPHA"],
        [""],
    ],
)
def test_an_unknown_or_malformed_scope_is_refused_with_the_allowed_forms(values: list[str]) -> None:
    with pytest.raises(
        ValueError, match="all, none, project:<namespace>, group:<name> or peer:<hub id>"
    ):
        parse_scope(values)
    assert SCOPE_FORMS == "all, none, project:<namespace>, group:<name> or peer:<hub id>"


def test_the_longest_namespace_is_accepted() -> None:
    assert parse_scope(["project:" + "x" * 256]).namespace == "x" * 256


def test_the_whole_hub_scope_returns_the_snapshot_itself() -> None:
    snapshot = scope_fleet()
    assert scope_snapshot(snapshot, WHOLE_HUB) is snapshot


def test_a_project_scope_keeps_its_tasks_agents_notes_and_claims() -> None:
    scoped = scope_snapshot(scope_fleet(), DashboardScope("project", "ALPHA"))

    assert [task["task_id"] for task in scoped.board["tasks"]] == ["A1", "A2"]
    assert scoped.board["ready"] == ["A1"]
    assert [note["task_id"] for note in scoped.board["progress"]] == ["A1"]
    assert scoped.online_agents == ["ALPHA/worker", "ALPHA/reviewer"]
    assert [seen["agent"] for seen in scoped.state["agents"]] == ["ALPHA/worker", "ALPHA/reviewer"]
    assert [claim["task_id"] for claim in scoped.state["active_claims"]] == ["A1"]
    assert scoped.agent_roles == {"ALPHA/worker": ["ALPHA/builder"]}
    assert scoped.agent_liveness == {"ALPHA/worker": {"live": True}}
    assert scoped.wake_capabilities == {"ALPHA/worker": "active"}
    assert scoped.state["generated_at"] == 1_783_000_000.0


def test_a_claim_on_a_task_in_scope_stays_whoever_holds_it() -> None:
    """A terminal outside the project holds BETA's task; BETA must not look unclaimed."""
    scoped = scope_snapshot(scope_fleet(), DashboardScope("project", "BETA"))

    assert [task["task_id"] for task in scoped.board["tasks"]] == ["B1"]
    assert scoped.online_agents == ["BETA/worker"]
    assert [(claim["task_id"], claim["owner"]) for claim in scoped.state["active_claims"]] == [
        ("B1", "user/terminal-1")
    ]


def test_the_none_scope_holds_unscoped_tasks_and_bare_names() -> None:
    scoped = scope_snapshot(scope_fleet(), DashboardScope("none"))

    assert [task["task_id"] for task in scoped.board["tasks"]] == ["N1", "N2"]
    assert scoped.online_agents == ["solo"]
    assert [claim["owner"] for claim in scoped.state["active_claims"]] == ["solo"]
    assert scoped.agent_liveness == {"solo": {"live": True}}


def test_the_summary_counts_the_scope_beside_the_hub_and_lists_every_namespace() -> None:
    whole = scope_fleet()
    scope = DashboardScope("project", "ALPHA")
    summary = scope_summary(whole, scope_snapshot(whole, scope), scope)

    assert summary == {
        "scope": "project:ALPHA",
        "applied": True,
        "advisory": False,
        "tasks_in_scope": 2,
        "tasks_total": 5,
        "tasks_without_project": 2,
        "agents_in_scope": 2,
        "agents_total": 5,
        "agents_without_namespace": 1,
        "claims_in_scope": 1,
        "claims_total": 3,
        "namespaces": [
            {"namespace": "ALPHA", "tasks": 2, "agents": 2},
            {"namespace": "BETA", "tasks": 1, "agents": 1},
            {"namespace": "user", "tasks": 0, "agents": 1},
        ],
        "groups": [],
        "peers": [],
    }


def test_the_scoped_document_is_bounded_carries_the_block_and_never_grows() -> None:
    whole = scoped_document(scope_fleet(), WHOLE_HUB)
    alpha = scoped_document(scope_fleet(), DashboardScope("project", "ALPHA"))

    assert whole["scope"]["applied"] is False and whole["scope"]["tasks_in_scope"] == 5
    assert [task["task_id"] for task in alpha["board"]["tasks"]] == ["A1"]
    assert alpha["bounds"]["tasks_total"] == 2 and alpha["bounds"]["closed_omitted"] == 1
    assert alpha["scope"]["scope"] == "project:ALPHA"
    assert [seat["identity"] for seat in alpha["fleet"]["seats"]] == [
        "ALPHA/reviewer",
        "ALPHA/worker",
    ]
    # The headline is computed in the scope: its counters count the scope's records only.
    headline = alpha["headline"]
    assert (headline["tasks_open"], headline["tasks_closed"], headline["claims_active"]) == (
        1,
        1,
        1,
    )
    assert (headline["agents_live"], whole["headline"]["agents_live"]) == (2, 5)
    for scope in (
        DashboardScope("project", "ALPHA"),
        DashboardScope("project", "BETA"),
        DashboardScope("none"),
    ):
        narrowed = scoped_document(scope_fleet(), scope)
        assert len(json.dumps(narrowed, sort_keys=True)) <= len(json.dumps(whole, sort_keys=True))


GROUPS = parse_scope_groups(
    json.dumps({"version": 1, "groups": {"pair": ["ALPHA", "BETA"], "beta": ["BETA"]}})
)


def test_a_group_scope_holds_the_union_of_its_namespaces_and_says_its_name() -> None:
    pair = parse_scope(["group:pair"], GROUPS)

    assert pair == DashboardScope("group", group="pair", members=frozenset({"ALPHA", "BETA"}))
    assert pair.text == "group:pair"
    assert {namespace for namespace in ("ALPHA", "BETA", "user", "") if pair.holds(namespace)} == {
        "ALPHA",
        "BETA",
    }
    # A namespace may be in two groups: each group is its own view.
    assert parse_scope(["group:beta"], GROUPS).holds("BETA")


def test_the_declared_groups_do_not_change_what_a_scope_is() -> None:
    assert parse_scope([], GROUPS) == WHOLE_HUB
    assert parse_scope([], GROUPS).declared is GROUPS
    assert parse_scope(["project:ALPHA"], GROUPS) == DashboardScope("project", "ALPHA")
    assert parse_scope(["none"], GROUPS).declared is GROUPS


def test_a_group_scope_without_a_groups_file_is_refused_naming_the_flag() -> None:
    with pytest.raises(ValueError, match="start the dashboard with --scope-groups"):
        parse_scope(["group:pair"])
    with pytest.raises(ValueError, match="declares no group of that name"):
        parse_scope(["group:infra"], GROUPS)


def test_a_group_scope_keeps_every_record_of_its_namespaces() -> None:
    whole = scope_fleet()
    pair = parse_scope(["group:pair"], GROUPS)
    scoped = scope_snapshot(whole, pair)

    assert [task["task_id"] for task in scoped.board["tasks"]] == ["A1", "A2", "B1"]
    assert scoped.online_agents == ["ALPHA/worker", "ALPHA/reviewer", "BETA/worker"]
    assert [claim["owner"] for claim in scoped.state["active_claims"]] == [
        "ALPHA/worker",
        "user/terminal-1",
    ]


def test_the_summary_lists_every_declared_group_with_its_counts() -> None:
    whole = scope_fleet()
    pair = parse_scope(["group:pair"], GROUPS)
    summary = scope_summary(whole, scope_snapshot(whole, pair), pair)

    assert (summary["scope"], summary["applied"], summary["tasks_in_scope"]) == (
        "group:pair",
        True,
        3,
    )
    assert summary["groups"] == [
        {"group": "beta", "namespaces": ["BETA"], "tasks": 1, "agents": 1},
        {"group": "pair", "namespaces": ["ALPHA", "BETA"], "tasks": 3, "agents": 3},
    ]
    document = scoped_document(whole, pair)
    assert document["scope"]["groups"] == summary["groups"]
