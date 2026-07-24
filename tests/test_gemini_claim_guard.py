# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Gemini CLI live-claim guard regressions

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.file_claim_guard import MutationRequest
from synapse_channel.gemini_claim_guard import (
    GeminiClaimGuardError,
    evaluate_hook_event,
    gemini_denial_payload,
    parse_hook_request,
)


def _event(
    root: Path,
    path: Path,
    *,
    tool: str = "replace",
) -> str:
    tool_input: dict[str, Any] = {"file_path": str(path)}
    if tool == "replace":
        tool_input |= {"old_string": "a", "new_string": "b"}
    else:
        tool_input |= {"content": "b"}
    return json.dumps(
        {
            "session_id": "session-1",
            "transcript_path": str(root / "transcript.json"),
            "cwd": str(root),
            "hook_event_name": "BeforeTool",
            "timestamp": "2026-07-12T15:30:00.000Z",
            "tool_name": tool,
            "tool_input": tool_input,
        }
    )


def _runner(root: Path, branch: str = "main") -> Callable[[list[str]], str]:
    """Stub git: rev-parse, optional core.ignorecase probe, then ls-files."""

    def run(args: list[str]) -> str:
        if args[-4:] == ["rev-parse", "--show-toplevel", "--abbrev-ref", "HEAD"]:
            return f"{root}\n{branch}"
        if "core.ignorecase" in args:
            return "false"
        assert args[-3:] == ["ls-files", "-z", "--cached"]
        return ""

    return run


def _claim(
    root: Path,
    *,
    owner: str = "seat/one",
    paths: list[str] | None = None,
    branch: str = "main",
    status: str = "claimed",
) -> dict[str, Any]:
    return {
        "task_id": "TASK",
        "owner": owner,
        "status": status,
        "worktree": str(root),
        "paths": ["src"] if paths is None else paths,
        "git": {"branch": branch, "base": "main", "auto_release_on": "manual"},
    }


def test_parse_hook_request_accepts_replace(tmp_path: Path) -> None:
    request = parse_hook_request(_event(tmp_path, tmp_path / "src" / "a.py"))
    assert isinstance(request, MutationRequest)
    assert request.session_id == "session-1"
    assert request.tool_use_id == "2026-07-12T15:30:00.000Z"
    assert request.cwd == tmp_path
    assert request.file_paths == (tmp_path / "src" / "a.py",)
    assert request.allow_semantic_source is True


def test_parse_hook_request_accepts_write_file(tmp_path: Path) -> None:
    request = parse_hook_request(_event(tmp_path, tmp_path / "src" / "a.py", tool="write_file"))
    assert isinstance(request, MutationRequest)
    assert request.file_paths == (tmp_path / "src" / "a.py",)
    assert request.allow_semantic_source is False


def test_parse_hook_request_accepts_installed_bundle_shape() -> None:
    """Exercise the exact field set built by the installed 0.47.0 hook engine.

    ``createBaseInput`` contributes ``session_id`` / ``transcript_path`` / ``cwd`` /
    ``hook_event_name`` / ``timestamp`` and ``fireBeforeToolEvent`` adds ``tool_name``
    and ``tool_input`` — there is no per-call tool id in the Gemini contract.
    """
    raw = json.dumps(
        {
            "session_id": "session-1",
            "transcript_path": "/tmp/synapse-gemini-payload-test/transcript.json",
            "cwd": "/tmp/synapse-gemini-payload-test",
            "hook_event_name": "BeforeTool",
            "timestamp": "2026-07-12T15:30:00.000Z",
            "tool_name": "write_file",
            "tool_input": {
                "file_path": "/tmp/synapse-gemini-payload-test/hello.txt",
                "content": "hello",
            },
        }
    )
    request = parse_hook_request(raw)
    assert isinstance(request, MutationRequest)
    assert request.session_id == "session-1"
    assert request.tool_use_id == "2026-07-12T15:30:00.000Z"
    assert request.cwd == Path("/tmp/synapse-gemini-payload-test")
    assert request.file_paths == (Path("/tmp/synapse-gemini-payload-test/hello.txt"),)


def test_parse_hook_request_rejects_claude_event_name(tmp_path: Path) -> None:
    payload = json.loads(_event(tmp_path, tmp_path / "src" / "a.py"))
    payload["hook_event_name"] = "PreToolUse"
    with pytest.raises(GeminiClaimGuardError, match="only BeforeTool"):
        parse_hook_request(json.dumps(payload))


def test_parse_hook_request_rejects_unsupported_tool(tmp_path: Path) -> None:
    with pytest.raises(GeminiClaimGuardError, match="only replace, write_file"):
        parse_hook_request(_event(tmp_path, tmp_path / "src" / "a.py", tool="execute"))


def test_parse_hook_request_rejects_claude_tool_aliases(tmp_path: Path) -> None:
    """Claude-style ``Edit``/``Write`` names never appear in Gemini hook input."""
    for alias in ("Edit", "Write"):
        with pytest.raises(GeminiClaimGuardError, match="only replace, write_file"):
            parse_hook_request(_event(tmp_path, tmp_path / "src" / "a.py", tool=alias))


def test_parse_hook_request_rejects_missing_file_path(tmp_path: Path) -> None:
    payload = json.loads(_event(tmp_path, tmp_path / "src" / "a.py"))
    payload["tool_input"] = {"old_string": "a", "new_string": "b"}
    with pytest.raises(GeminiClaimGuardError, match="tool_input\\.file_path"):
        parse_hook_request(json.dumps(payload))


def test_parse_hook_request_rejects_missing_timestamp(tmp_path: Path) -> None:
    payload = json.loads(_event(tmp_path, tmp_path / "src" / "a.py"))
    del payload["timestamp"]
    with pytest.raises(GeminiClaimGuardError, match="input\\.timestamp"):
        parse_hook_request(json.dumps(payload))


def test_parse_hook_request_rejects_relative_cwd(tmp_path: Path) -> None:
    payload = json.loads(_event(tmp_path, tmp_path / "src" / "a.py"))
    payload["cwd"] = "relative/path"
    with pytest.raises(GeminiClaimGuardError, match="cwd must be absolute"):
        parse_hook_request(json.dumps(payload))


def test_parse_hook_request_rejects_invalid_json() -> None:
    with pytest.raises(GeminiClaimGuardError, match="not valid JSON"):
        parse_hook_request("{not-json")


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"hook_event_name": "BeforeTool"},
        {
            "session_id": "",
            "cwd": "/repo",
            "hook_event_name": "BeforeTool",
            "timestamp": "2026-07-12T15:30:00.000Z",
            "tool_name": "replace",
            "tool_input": {"file_path": "a.py"},
        },
        {
            "session_id": "session-1",
            "cwd": "/repo",
            "hook_event_name": "BeforeTool",
            "timestamp": "2026-07-12T15:30:00.000Z",
            "tool_name": "replace",
            "tool_input": None,
        },
    ],
)
def test_parse_hook_request_rejects_other_malformed_shapes(payload: object) -> None:
    with pytest.raises(GeminiClaimGuardError):
        parse_hook_request(json.dumps(payload))


@pytest.mark.asyncio
async def test_evaluate_hook_event_allows_live_covering_claim(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()

    async def snapshot(**_kwargs: object) -> dict[str, Any]:
        return {"active_claims": [_claim(tmp_path)]}

    verdict = await evaluate_hook_event(
        _event(tmp_path, tmp_path / "src" / "module.py"),
        identity="seat/one",
        uri="ws://hub",
        token=None,
        timeout=0.1,
        state_fetcher=snapshot,
        git_runner=_runner(tmp_path),
    )
    assert verdict.allowed


@pytest.mark.asyncio
async def test_evaluate_hook_event_denies_unclaimed_file(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()

    async def snapshot(**_kwargs: object) -> dict[str, Any]:
        return {"active_claims": []}

    verdict = await evaluate_hook_event(
        _event(tmp_path, tmp_path / "src" / "module.py"),
        identity="seat/one",
        uri="ws://hub",
        token=None,
        timeout=0.1,
        state_fetcher=snapshot,
        git_runner=_runner(tmp_path),
    )
    assert not verdict.allowed
    assert "claim" in verdict.reason.lower()


@pytest.mark.asyncio
async def test_evaluate_hook_event_denies_wrong_owner(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()

    async def snapshot(**_kwargs: object) -> dict[str, Any]:
        return {"active_claims": [_claim(tmp_path, owner="seat/two")]}

    verdict = await evaluate_hook_event(
        _event(tmp_path, tmp_path / "src" / "module.py"),
        identity="seat/one",
        uri="ws://hub",
        token=None,
        timeout=0.1,
        state_fetcher=snapshot,
        git_runner=_runner(tmp_path),
    )
    assert not verdict.allowed
    assert "ambiguous" in verdict.reason


@pytest.mark.asyncio
async def test_evaluate_hook_event_denies_malformed_input_before_query() -> None:
    async def must_not_run(**_kwargs: object) -> dict[str, Any]:
        raise AssertionError("malformed input must not query the hub")

    verdict = await evaluate_hook_event(
        "not-json",
        identity="seat/one",
        uri="ws://hub",
        token=None,
        timeout=0.1,
        state_fetcher=must_not_run,
    )
    assert not verdict.allowed
    assert "not valid JSON" in verdict.reason


def test_gemini_denial_payload_uses_native_decision_shape() -> None:
    """The installed hook runner blocks on top-level ``decision``/``reason`` only."""
    assert gemini_denial_payload("claim required") == {
        "decision": "deny",
        "reason": "claim required",
    }
