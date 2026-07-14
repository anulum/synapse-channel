# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — editor-to-OpenCode governance acceptance contract
"""Script and verify one real editor governance lifecycle through OpenCode."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from fixtures.opencode.llm import ScriptedLlmServer
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore

TASK_ID = "EDITOR-OPENCODE-GOVERNANCE"
RESPONSE = "SYNAPSE_EDITOR_GOVERNANCE_E2E_RESPONSE"
PROMPT = (
    "Prove fail-closed SYNAPSE governance: attempt the denied-before file, "
    "claim allowed.txt through the Synapse MCP tool, write it, release with "
    "evidence, attempt the denied-after file, then return the acceptance token."
)

_GIT_CLAIM_TOOL = "synapse_synapse_git_claim"
_RELEASE_TOOL = "synapse_synapse_release"


def source_environment(environment: dict[str, str]) -> dict[str, str]:
    """Expose the checked-out package to installed adapter subprocesses."""
    source = str(Path(__file__).resolve().parents[3] / "src")
    inherited = environment.get("PYTHONPATH", "")
    environment["PYTHONPATH"] = source if not inherited else source + os.pathsep + inherited
    environment.pop("FORCE_COLOR", None)
    return environment


def synapse_launcher(path: Path) -> Path:
    """Write a private launcher for the exact checked-out Synapse CLI."""
    path.write_text(
        f"#!{sys.executable}\nfrom synapse_channel.cli import main\nraise SystemExit(main())\n",
        encoding="utf-8",
    )
    path.chmod(0o700)
    return path


def enqueue_governance_turn(llm: ScriptedLlmServer, repo: Path) -> None:
    """Queue the mutation/claim/release sequence consumed by real OpenCode."""
    llm.enqueue_tool(
        "write",
        {"filePath": str(repo / "denied-before.txt"), "content": "must-not-exist\n"},
    )
    llm.enqueue_tool(
        _GIT_CLAIM_TOOL,
        {
            "task_id": TASK_ID,
            "paths": ["allowed.txt"],
            "base": "main",
            "auto_release_on": "manual",
        },
    )
    llm.enqueue_tool(
        "write",
        {"filePath": str(repo / "allowed.txt"), "content": "governed\n"},
    )
    llm.enqueue_tool(
        _RELEASE_TOOL,
        {
            "task_id": TASK_ID,
            "evidence": ["real editor to OpenCode to Synapse governance turn"],
            "changed_files": ["allowed.txt"],
            "confidence": "high",
        },
    )
    llm.enqueue_tool(
        "write",
        {"filePath": str(repo / "denied-after.txt"), "content": "must-not-exist\n"},
    )
    llm.enqueue_text(RESPONSE)


def _tool_names(request: Mapping[str, object]) -> set[str]:
    """Return OpenAI-compatible function names advertised on one request."""
    tools = request.get("tools")
    if not isinstance(tools, list):
        return set()
    names: set[str] = set()
    for tool in tools:
        if not isinstance(tool, Mapping):
            continue
        function = tool.get("function")
        if not isinstance(function, Mapping):
            continue
        name = function.get("name")
        if isinstance(name, str):
            names.add(name)
    return names


def assert_provider_governance(requests: Sequence[Mapping[str, object]]) -> None:
    """Verify OpenCode exposed and completed the expected governed tool chain."""
    if len(requests) != 6:
        raise AssertionError(f"expected six governance requests, received {len(requests)}")
    if any(request.get("model") != "test-model" for request in requests):
        raise AssertionError("editor governance used an unexpected provider model")
    if PROMPT not in json.dumps(requests[0], sort_keys=True):
        raise AssertionError("editor governance prompt did not reach the provider")
    tools = _tool_names(requests[0])
    missing = {_GIT_CLAIM_TOOL, _RELEASE_TOOL} - tools
    if missing:
        raise AssertionError(f"OpenCode omitted Synapse MCP tools: {sorted(missing)}")

    transcript = [json.dumps(request, sort_keys=True) for request in requests]
    expected_results = (
        (1, "Synapse file claim denied"),
        (2, "claim granted"),
        (4, "with receipt owner"),
        (5, "Synapse file claim denied"),
    )
    for index, marker in expected_results:
        if marker not in transcript[index]:
            raise AssertionError(
                f"provider request {index + 1} omitted governance result {marker!r}"
            )


def assert_durable_governance(db_path: Path, repo: Path, identity: str) -> None:
    """Verify the claim, release, and evidence receipt survived in the hub journal."""
    with EventStore(db_path) as store:
        claims = list(store.iter_events(kinds=[EventKind.CLAIM]))
        releases = list(store.iter_events(kinds=[EventKind.RELEASE]))
        progress = list(store.iter_events(kinds=[EventKind.LEDGER_PROGRESS]))

    matching_claims = [event for event in claims if event.payload.get("task_id") == TASK_ID]
    matching_releases = [event for event in releases if event.payload.get("task_id") == TASK_ID]
    matching_progress = [event for event in progress if event.payload.get("task_id") == TASK_ID]
    if len(matching_claims) != 1 or len(matching_releases) != 1:
        raise AssertionError("durable editor governance needs exactly one claim and release")
    if len(matching_progress) != 1:
        raise AssertionError("durable editor governance needs exactly one receipt note")

    claim = matching_claims[0].payload
    if claim.get("owner") != identity:
        raise AssertionError("durable editor claim owner differs from the adapter identity")
    if claim.get("worktree") != str(repo.resolve()) or claim.get("paths") != ["allowed.txt"]:
        raise AssertionError("durable editor claim lost its exact worktree or path scope")
    git = claim.get("git")
    if not isinstance(git, Mapping):
        raise AssertionError("durable editor claim omitted Git context")
    if git.get("base") != "main" or git.get("auto_release_on") != "manual":
        raise AssertionError("durable editor claim changed its Git intent")

    note = matching_progress[0].payload
    if note.get("author") != identity or note.get("kind") != "assessment":
        raise AssertionError("durable editor release receipt lost its author or kind")
    text = note.get("text")
    if not isinstance(text, str):
        raise AssertionError("durable editor release receipt has no text")
    for marker in (
        "evidence=real editor to OpenCode to Synapse governance turn",
        "changed_files=allowed.txt",
        "confidence=high",
    ):
        if marker not in text:
            raise AssertionError(f"durable editor release receipt omitted {marker!r}")
