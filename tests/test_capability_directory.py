# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for manifest-backed capability directory records

from __future__ import annotations

import json
from pathlib import Path

from synapse_channel.core.capability_directory import (
    DISCOVERY_TRUST_BOUNDARY,
    CapabilityDirectory,
    build_capability_directory,
    directory_to_json,
    filter_capability_directory,
)


def _directory() -> CapabilityDirectory:
    """Build a representative directory for tests."""
    return build_capability_directory(
        manifest=[
            {
                "agent": "FAST",
                "description": "quick worker",
                "skills": ["ollama", "fast-path"],
                "task_classes": ["chat", "rule"],
                "model": "gemma3:4b",
                "contracts": [{"task_class": "chat"}],
            },
            {
                "agent": "REASON",
                "description": "deep worker",
                "skills": ["reason"],
                "task_classes": ["reason"],
                "model": "",
                "contracts": [],
            },
        ],
        resources=[
            {
                "agent": "FAST",
                "kind": "llm",
                "name": "gemma3:4b",
                "capacity": 2,
                "meta": {"vram": "8G"},
            },
            {"agent": "TOOLS", "kind": "fs", "name": "workspace", "capacity": 1},
        ],
    )


def test_build_capability_directory_indexes_agents_and_resources() -> None:
    directory = _directory()

    assert directory.trust_boundary == DISCOVERY_TRUST_BOUNDARY
    assert [entry.id for entry in directory.entries] == [
        "agent:FAST",
        "agent:REASON",
        "resource:FAST:llm:gemma3:4b",
        "resource:TOOLS:fs:workspace",
    ]
    agent = directory.entries[0]
    assert agent.entry_type == "agent"
    assert agent.task_classes == ("chat", "rule")
    assert agent.skills == ("ollama", "fast-path")
    assert agent.contracts == 1
    resource = directory.entries[2]
    assert resource.entry_type == "resource"
    assert resource.resource_kind == "llm"
    assert resource.capacity == 2
    assert resource.meta == {"vram": "8G"}


def test_filter_capability_directory_applies_discovery_filters() -> None:
    directory = _directory()

    assert [
        entry.id for entry in filter_capability_directory(directory, task_class="chat").entries
    ] == ["agent:FAST"]
    assert [
        entry.id for entry in filter_capability_directory(directory, skill="reason").entries
    ] == ["agent:REASON"]
    assert [
        entry.id for entry in filter_capability_directory(directory, resource_kind="llm").entries
    ] == ["resource:FAST:llm:gemma3:4b"]
    assert [entry.id for entry in filter_capability_directory(directory, agent="FAST").entries] == [
        "agent:FAST",
        "resource:FAST:llm:gemma3:4b",
    ]


def test_directory_to_json_is_stable_and_carries_trust_boundary() -> None:
    payload = json.loads(directory_to_json(_directory()))

    assert payload["trust_boundary"] == DISCOVERY_TRUST_BOUNDARY
    assert payload["entries"][0]["id"] == "agent:FAST"
    assert payload["entries"][0]["trust"] == "discovery-only"
    assert payload["entries"][2]["resource_kind"] == "llm"


def test_build_capability_directory_ignores_malformed_entries() -> None:
    directory = build_capability_directory(
        manifest=[
            {"agent": "", "task_classes": ["chat"]},
            {"agent": "BARE", "task_classes": "chat", "skills": ["", "one"], "contracts": "bad"},
        ],
        resources=[
            {"agent": "FAST", "kind": "", "name": "m"},
            {"agent": "FAST", "kind": "llm", "name": ""},
            {"agent": "FAST", "kind": "llm", "name": "m", "meta": "bad"},
        ],
    )

    assert [entry.id for entry in directory.entries] == ["agent:BARE", "resource:FAST:llm:m"]
    assert directory.entries[0].task_classes == ()
    assert directory.entries[0].skills == ("one",)
    assert directory.entries[0].contracts == 0
    assert directory.entries[1].meta == {}


def test_directory_docs_are_wired() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")
    cli_docs = Path("docs/cli.md").read_text(encoding="utf-8")
    mcp_docs = Path("docs/mcp.md").read_text(encoding="utf-8")
    changelog = Path("CHANGELOG.md").read_text(encoding="utf-8")

    assert "synapse directory" in readme
    assert "capability directory" in cli_docs
    assert "`synapse_directory()`" in mcp_docs
    assert "`synapse://directory`" in mcp_docs
    assert "capability directory" in changelog
