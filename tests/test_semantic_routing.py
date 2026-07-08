# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for advisory semantic task routing

from __future__ import annotations

import json
from typing import cast

from synapse_channel.core.capability_directory import build_capability_directory
from synapse_channel.core.capability_observations import (
    ObservedCapabilityEvidence,
    ObservedCapabilityIndex,
)
from synapse_channel.core.semantic_routing import (
    ROUTING_TRUST_BOUNDARY,
    find_task,
    recommend_agents_for_task,
    recommendation_to_json,
)


def test_recommend_agents_scores_structured_and_text_signals() -> None:
    directory = build_capability_directory(
        manifest=[
            {
                "agent": "FAST",
                "description": "Repairs local websocket routing and hub adapters.",
                "skills": ["websocket", "routing"],
                "task_classes": ["transport"],
                "model": "local-fast",
                "contracts": [{"task_class": "transport"}],
            },
            {
                "agent": "DOCS",
                "description": "Writes release notes.",
                "skills": ["markdown"],
                "task_classes": ["docs"],
            },
        ],
    )
    task = {
        "task_id": "ROUTE-1",
        "title": "Websocket transport routing repair",
        "description": "Fix local hub websocket route fallback.",
    }

    recommendation = recommend_agents_for_task(task, directory, limit=3)

    assert recommendation.task_id == "ROUTE-1"
    assert (
        recommendation.query
        == "Websocket transport routing repair Fix local hub websocket route fallback."
    )
    assert recommendation.trust_boundary == ROUTING_TRUST_BOUNDARY
    assert [candidate.agent for candidate in recommendation.candidates] == ["FAST"]
    assert recommendation.candidates[0].score == 40
    assert recommendation.candidates[0].reasons == (
        "task_class:transport",
        "skill:routing",
        "skill:websocket",
        "description:hub",
        "description:local",
        "description:routing",
        "description:websocket",
        "contract:evidence",
    )
    assert recommendation.candidates[0].observed_evidence == ()


def test_recommend_agents_adds_observed_capability_evidence() -> None:
    directory = build_capability_directory(
        manifest=[
            {
                "agent": "FAST",
                "description": "General local worker.",
                "skills": ["maintenance"],
                "task_classes": ["ops"],
            },
            {
                "agent": "SLOW",
                "description": "Python cleanup worker.",
                "skills": ["python"],
                "task_classes": ["code"],
            },
        ],
    )
    observations = ObservedCapabilityIndex(
        evidence=(
            ObservedCapabilityEvidence(
                agent="FAST",
                task_id="DONE",
                seq=42,
                ts=10.0,
                tokens=("cleanup", "python", "routing"),
                detail="release receipt: evidence=pytest -q",
            ),
            ObservedCapabilityEvidence(
                agent="FAST",
                task_id="OTHER",
                seq=43,
                ts=11.0,
                tokens=("unrelated",),
                detail="release receipt: evidence=pytest -q",
            ),
        )
    )
    task = {"task_id": "NEXT", "title": "Python routing cleanup", "description": ""}

    recommendation = recommend_agents_for_task(task, directory, observations=observations)

    assert [(candidate.agent, candidate.score) for candidate in recommendation.candidates] == [
        ("FAST", 18),
        ("SLOW", 11),
    ]
    assert recommendation.candidates[0].reasons == (
        "observed:cleanup",
        "observed:python",
        "observed:routing",
        "observed_task:DONE@42",
    )
    assert recommendation.candidates[0].observed_evidence == (
        {"task_id": "DONE", "seq": 42, "tokens": ["cleanup", "python", "routing"]},
    )


def test_recommend_agents_orders_ties_deterministically_and_can_include_zero() -> None:
    directory = build_capability_directory(
        manifest=[
            {"agent": "BETA", "skills": ["python"], "task_classes": ["code"]},
            {"agent": "ALPHA", "skills": ["python"], "task_classes": ["code"]},
            {"agent": "ZERO", "skills": ["docs"], "task_classes": ["writing"]},
        ],
    )
    task = {"task_id": "T", "title": "Python code cleanup", "description": ""}

    recommendation = recommend_agents_for_task(task, directory, limit=3, include_zero=True)

    assert [(candidate.agent, candidate.score) for candidate in recommendation.candidates] == [
        ("ALPHA", 17),
        ("BETA", 17),
        ("ZERO", 0),
    ]
    assert recommendation.fallback_reason == ""
    assert recommendation.candidates[2].reasons == ("no local signal match",)


def test_recommend_agents_malformed_limit_falls_back_to_default_bound() -> None:
    directory = build_capability_directory(
        manifest=[
            {"agent": "A", "skills": ["python"], "task_classes": ["code"]},
            {"agent": "B", "skills": ["python"], "task_classes": ["code"]},
        ],
    )

    recommendation = recommend_agents_for_task(
        {"task_id": "T", "title": "Python code"},
        directory,
        limit=cast(int, float("inf")),
    )

    assert [candidate.agent for candidate in recommendation.candidates] == ["A", "B"]


def test_recommend_agents_reports_empty_directory_and_no_matches() -> None:
    empty = build_capability_directory(manifest=[])
    missing = recommend_agents_for_task({"task_id": "T", "title": "anything"}, empty)
    assert missing.candidates == ()
    assert missing.fallback_reason == "no agent capability cards are available"

    directory = build_capability_directory(
        manifest=[{"agent": "DOCS", "skills": ["markdown"], "task_classes": ["docs"]}]
    )
    no_match = recommend_agents_for_task({"task_id": "T", "title": "kernel cuda"}, directory)
    assert no_match.candidates == ()
    assert no_match.fallback_reason == "no capability card matched the task text"


def test_find_task_ignores_malformed_entries() -> None:
    board = {
        "tasks": [
            "bad",
            {"task_id": "OTHER", "title": "Wrong"},
            {"task_id": "TARGET", "title": "Right"},
        ]
    }

    assert find_task(board, "TARGET") == {"task_id": "TARGET", "title": "Right"}
    assert find_task(board, "missing") is None
    assert find_task({"tasks": "bad"}, "TARGET") is None


def test_recommendation_json_is_stable() -> None:
    directory = build_capability_directory(
        manifest=[{"agent": "FAST", "skills": ["python"], "task_classes": ["code"]}]
    )
    recommendation = recommend_agents_for_task({"task_id": "T", "title": "Python code"}, directory)

    payload = json.loads(recommendation_to_json(recommendation))

    assert payload["task_id"] == "T"
    assert payload["candidates"][0]["agent"] == "FAST"
    assert payload["trust_boundary"] == ROUTING_TRUST_BOUNDARY
