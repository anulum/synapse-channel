# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — unit tests for the Grok claim guard
"""Tests for :mod:`synapse_channel.grok_claim_guard`."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from synapse_channel.file_claim_guard import MutationRequest
from synapse_channel.grok_claim_guard import (
    GrokClaimGuardError,
    denial_payload,
    parse_hook_request,
)


def _runner(root: Path, branch: str = "main") -> Callable[[list[str]], str]:
    """Return a deterministic runner for Git context and index queries."""

    def run(args: list[str]) -> str:
        if args[-4:] == ["rev-parse", "--show-toplevel", "--abbrev-ref", "HEAD"]:
            return f"{root}\n{branch}"
        assert args[-3:] == ["ls-files", "-z", "--cached"]
        return ""

    return run


def _event(
    *,
    tool: str = "search_replace",
    path: str = "/tmp/repo/src/a.py",
    cwd: str = "/tmp/repo",
    camel: bool = True,
) -> str:
    if camel:
        payload = {
            "hookEventName": "pre_tool_use",
            "sessionId": "sess-1",
            "cwd": cwd,
            "toolName": tool,
            "toolInput": {"path": path, "old_string": "a", "new_string": "b"},
            "toolUseId": "call-1",
        }
    else:
        payload = {
            "hook_event_name": "PreToolUse",
            "session_id": "sess-1",
            "cwd": cwd,
            "tool_name": tool,
            "tool_input": {"file_path": path},
            "tool_call_id": "call-1",
        }
    return json.dumps(payload)


def test_parse_camel_case_search_replace() -> None:
    request = parse_hook_request(_event())
    assert isinstance(request, MutationRequest)
    assert request.file_paths == (Path("/tmp/repo/src/a.py"),)
    assert request.cwd == Path("/tmp/repo")
    assert request.session_id == "sess-1"
    assert request.tool_use_id == "call-1"
    assert request.allow_semantic_source is True


def test_parse_snake_case_and_write_tool() -> None:
    request = parse_hook_request(_event(tool="write", camel=False, path="/tmp/repo/x.md"))
    assert isinstance(request, MutationRequest)
    assert request.file_paths == (Path("/tmp/repo/x.md"),)
    assert request.allow_semantic_source is False


def test_relative_path_is_preserved_for_shared_canonicalisation() -> None:
    raw = _event(path="src/a.py", cwd="/tmp/repo")
    request = parse_hook_request(raw)
    assert isinstance(request, MutationRequest)
    assert request.file_paths == (Path("src/a.py"),)


def test_denial_payload_is_grok_native() -> None:
    payload = denial_payload("no claim")
    assert payload == {"decision": "deny", "reason": "no claim"}
    assert "permissionDecision" not in json.dumps(payload)


def test_unsupported_tool_is_rejected() -> None:
    with pytest.raises(GrokClaimGuardError, match="file editors or run_terminal_command"):
        parse_hook_request(_event(tool="bash"))


def test_non_json_is_rejected() -> None:
    with pytest.raises(GrokClaimGuardError, match="not valid JSON"):
        parse_hook_request("not-json")


def test_render_hook_config_is_mergeable_grok_json(tmp_path: Path) -> None:
    from synapse_channel.cli_grok_claim_hook import render_hook_config

    config = render_hook_config(
        identity="user/terminal-1",
        uri="ws://127.0.0.1:8876",
        ready_timeout=2.0,
        token_file=None,
        synapse_bin="synapse",
    )
    assert "PreToolUse" in config["hooks"]
    matcher = config["hooks"]["PreToolUse"][0]["matcher"]
    assert "search_replace" in matcher and "write" in matcher
    assert "run_terminal_command" in matcher
    command = config["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert "grok-claim-hook" in command
    assert "user/terminal-1" in command


def test_missing_event_name_is_rejected() -> None:
    payload = {
        "sessionId": "sess-1",
        "cwd": "/tmp/repo",
        "toolName": "search_replace",
        "toolInput": {"path": "/tmp/repo/a.py"},
        "toolUseId": "call-1",
    }
    with pytest.raises(GrokClaimGuardError, match="hookEventName|input"):
        parse_hook_request(json.dumps(payload))


def test_missing_tool_input_is_rejected() -> None:
    payload = {
        "hookEventName": "PreToolUse",
        "sessionId": "sess-1",
        "cwd": "/tmp/repo",
        "toolName": "search_replace",
        "toolUseId": "call-1",
    }
    with pytest.raises(GrokClaimGuardError, match="toolInput"):
        parse_hook_request(json.dumps(payload))


def test_non_object_json_is_rejected() -> None:
    with pytest.raises(GrokClaimGuardError, match="JSON object"):
        parse_hook_request("[]")


def test_relative_cwd_is_rejected() -> None:
    with pytest.raises(GrokClaimGuardError, match="absolute"):
        parse_hook_request(_event(cwd="relative/repo"))


def test_non_pretool_event_is_rejected() -> None:
    raw = json.dumps(
        {
            "hookEventName": "PostToolUse",
            "sessionId": "sess-1",
            "cwd": "/tmp/repo",
            "toolName": "search_replace",
            "toolInput": {"path": "/tmp/repo/a.py"},
            "toolUseId": "call-1",
        }
    )
    with pytest.raises(GrokClaimGuardError, match="PreToolUse"):
        parse_hook_request(raw)


def test_edit_and_multiedit_aliases_parse() -> None:
    for tool in ("Edit", "MultiEdit"):
        request = parse_hook_request(_event(tool=tool))
        assert isinstance(request, MutationRequest)
        assert request.file_paths == (Path("/tmp/repo/src/a.py"),)
        assert request.allow_semantic_source is True
    write_request = parse_hook_request(_event(tool="Write"))
    assert isinstance(write_request, MutationRequest)
    assert write_request.allow_semantic_source is False


def test_target_file_key_is_accepted() -> None:
    payload = {
        "hookEventName": "PreToolUse",
        "sessionId": "sess-1",
        "cwd": "/tmp/repo",
        "toolName": "search_replace",
        "toolInput": {"target_file": "src/a.py"},
        "toolUseId": "call-1",
    }
    request = parse_hook_request(json.dumps(payload))
    assert isinstance(request, MutationRequest)
    assert request.file_paths == (Path("src/a.py"),)


@pytest.mark.asyncio
async def test_evaluate_hook_event_denies_controlled_query_failure(tmp_path: Path) -> None:
    from typing import Any

    from synapse_channel.claim_state import ClaimStateError
    from synapse_channel.grok_claim_guard import evaluate_hook_event

    (tmp_path / "src").mkdir()

    async def unavailable(**_kwargs: object) -> dict[str, Any]:
        raise ClaimStateError("hub unavailable")

    verdict = await evaluate_hook_event(
        _event(path=str(tmp_path / "src" / "module.py"), cwd=str(tmp_path)),
        identity="seat/one",
        uri="ws://hub",
        token=None,
        timeout=0.1,
        state_fetcher=unavailable,
        git_runner=_runner(tmp_path),
    )
    assert not verdict.allowed
    assert verdict.reason == "hub unavailable"


@pytest.mark.asyncio
async def test_evaluate_hook_event_allows_live_covering_claim(tmp_path: Path) -> None:
    from typing import Any

    from synapse_channel.grok_claim_guard import evaluate_hook_event

    (tmp_path / "src").mkdir()

    async def snapshot(**_kwargs: object) -> dict[str, Any]:
        return {
            "active_claims": [
                {
                    "task_id": "TASK",
                    "owner": "seat/one",
                    "status": "claimed",
                    "worktree": str(tmp_path),
                    "paths": ["src"],
                    "git": {"branch": "main", "base": "main", "auto_release_on": "manual"},
                }
            ]
        }

    verdict = await evaluate_hook_event(
        _event(path=str(tmp_path / "src" / "module.py"), cwd=str(tmp_path)),
        identity="seat/one",
        uri="ws://hub",
        token=None,
        timeout=0.1,
        state_fetcher=snapshot,
        git_runner=_runner(tmp_path),
    )
    assert verdict.allowed


@pytest.mark.asyncio
async def test_evaluate_hook_event_denies_unclaimed_path(tmp_path: Path) -> None:
    from typing import Any

    from synapse_channel.grok_claim_guard import evaluate_hook_event

    (tmp_path / "src").mkdir()

    async def snapshot(**_kwargs: object) -> dict[str, Any]:
        return {"active_claims": []}

    verdict = await evaluate_hook_event(
        _event(path=str(tmp_path / "src" / "module.py"), cwd=str(tmp_path)),
        identity="seat/one",
        uri="ws://hub",
        token=None,
        timeout=0.1,
        state_fetcher=snapshot,
        git_runner=_runner(tmp_path),
    )
    assert not verdict.allowed


@pytest.mark.asyncio
async def test_evaluate_hook_event_denies_malformed_payload(tmp_path: Path) -> None:
    from synapse_channel.grok_claim_guard import evaluate_hook_event

    async def must_not_fetch(**_kwargs: object) -> dict[str, object]:
        raise AssertionError("malformed input must not query the hub")

    def runner(_args: list[str]) -> str:
        raise AssertionError("malformed input must not resolve git")

    verdict = await evaluate_hook_event(
        "{not-json",
        identity="seat/one",
        uri="ws://hub",
        token=None,
        timeout=0.1,
        state_fetcher=must_not_fetch,
        git_runner=runner,
    )
    assert not verdict.allowed
