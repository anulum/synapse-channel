# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for advisory resource bidding

from __future__ import annotations

import json
from pathlib import Path

from synapse_channel.core.capability_directory import (
    CapabilityDirectory,
    CapabilityDirectoryEntry,
    build_capability_directory,
)
from synapse_channel.core.resource_bidding import (
    RESOURCE_BID_TRUST_BOUNDARY,
    recommend_resource_bids,
    resource_bid_report_to_json,
)


def _directory() -> CapabilityDirectory:
    """Build a representative capability/resource directory."""
    return build_capability_directory(
        manifest=[
            {
                "agent": "FAST",
                "description": "GPU python training worker",
                "skills": ["python", "cuda"],
                "task_classes": ["training"],
                "model": "local",
            },
            {
                "agent": "DOCS",
                "description": "documentation writer",
                "skills": ["markdown"],
                "task_classes": ["docs"],
            },
        ],
        resources=[
            {
                "agent": "FAST",
                "kind": "gpu",
                "name": "a100",
                "capacity": 4,
                "meta": {"memory": "80GB", "queue": "short"},
            },
            {"agent": "DOCS", "kind": "cpu", "name": "docs-runner", "capacity": 1},
        ],
    )


def test_recommend_resource_bids_ranks_resource_offers_with_provenance() -> None:
    task = {
        "task_id": "TRAIN",
        "title": "GPU python training",
        "description": "Run cuda training on local a100 hardware with an 80GB short queue.",
    }

    report = recommend_resource_bids(task, _directory(), resource_kind="gpu", limit=3)

    assert report.task_id == "TRAIN"
    assert report.trust_boundary == RESOURCE_BID_TRUST_BOUNDARY
    assert len(report.candidates) == 1
    candidate = report.candidates[0]
    assert candidate.agent == "FAST"
    assert candidate.resource_id == "resource:FAST:gpu:a100"
    assert candidate.capacity == 4
    assert candidate.score == 51
    assert candidate.reasons == (
        "resource_kind:gpu",
        "capacity:4",
        "task_class:training",
        "skill:cuda",
        "skill:python",
        "description:gpu",
        "description:python",
        "description:training",
        "resource:a100",
        "resource:gpu",
        "meta:80gb",
        "meta:short",
    )


def test_resource_bids_can_include_zero_score_diagnostics() -> None:
    report = recommend_resource_bids(
        {"task_id": "UNKNOWN", "title": "unmatched"},
        _directory(),
        include_zero=True,
        limit=5,
    )

    assert [candidate.resource_kind for candidate in report.candidates] == ["gpu", "cpu"]
    assert report.candidates[1].score == 1
    assert report.candidates[1].reasons == ("capacity:1",)


def test_resource_bids_report_fallbacks_for_missing_inputs() -> None:
    no_resources = recommend_resource_bids(
        {"task_id": "T", "title": "gpu"},
        build_capability_directory(manifest=[], resources=[]),
    )
    filtered_out = recommend_resource_bids(
        {"task_id": "T", "title": "gpu"},
        _directory(),
        resource_kind="fpga",
    )

    assert no_resources.fallback_reason == "no resource offers are available"
    assert filtered_out.fallback_reason == "no resource offer matched the task text"


def test_resource_bids_cover_providerless_zero_and_nested_meta_edges() -> None:
    zero_directory = CapabilityDirectory(
        entries=(
            CapabilityDirectoryEntry(
                id="resource:TOOLS:fpga:lab",
                entry_type="resource",
                agent="TOOLS",
                label="fpga/lab",
                resource_kind="fpga",
                resource_name="lab",
                capacity=0,
            ),
        )
    )
    meta_directory = CapabilityDirectory(
        entries=(
            CapabilityDirectoryEntry(
                id="resource:TOOLS:fpga:lab",
                entry_type="resource",
                agent="TOOLS",
                label="fpga/lab",
                resource_kind="fpga",
                resource_name="lab",
                capacity=0,
                meta={
                    "count": 22,
                    "tags": ["versa", 3, False, {"ignored": True}],
                    "ignored": [{"nested": True}],
                    "ignored_map": {"nested": True},
                },
            ),
        )
    )

    without_zero = recommend_resource_bids({"task_id": "LAB", "title": "nomatch"}, zero_directory)
    with_zero = recommend_resource_bids(
        {"task_id": "LAB", "title": "nomatch"},
        zero_directory,
        include_zero=True,
        limit=0,
    )
    meta_report = recommend_resource_bids({"task_id": "LAB", "title": "versa 22"}, meta_directory)

    assert without_zero.candidates == ()
    assert with_zero.candidates[0].task_classes == ()
    assert with_zero.candidates[0].skills == ()
    assert with_zero.candidates[0].reasons == ("no local signal match",)
    assert meta_report.candidates[0].reasons == ("meta:22", "meta:versa")


def test_resource_bid_json_and_docs_are_wired() -> None:
    report = recommend_resource_bids({"task_id": "TRAIN", "title": "gpu"}, _directory())
    payload = json.loads(resource_bid_report_to_json(report))

    assert payload["task_id"] == "TRAIN"
    assert payload["candidates"][0]["resource_name"] == "a100"
    assert payload["trust_boundary"] == RESOURCE_BID_TRUST_BOUNDARY

    readme = Path("README.md").read_text(encoding="utf-8")
    cli_docs = Path("docs/cli.md").read_text(encoding="utf-8")
    mcp_docs = Path("docs/mcp.md").read_text(encoding="utf-8")
    assert "resource-bids" in readme
    assert "resource-bids" in cli_docs
    assert "`synapse_resource_bids" in mcp_docs
