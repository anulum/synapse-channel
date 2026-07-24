# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Kimi Code live-claim guard regressions

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.file_claim_guard import MutationRequest
from synapse_channel.kimi_claim_guard import (
    KimiClaimGuardError,
    evaluate_hook_event,
    parse_hook_request,
)


def _event(
    root: Path,
    path: Path,
    *,
    tool: str = "Edit",
) -> str:
    return json.dumps(
        {
            "session_id": "session-1",
            "tool_call_id": "tool-1",
            "cwd": str(root),
            "hook_event_name": "PreToolUse",
            "tool_name": tool,
            "tool_input": {"path": str(path), "old_string": "a", "new_string": "b"},
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


def test_parse_hook_request_accepts_tool_call_id(tmp_path: Path) -> None:
    request = parse_hook_request(_event(tmp_path, tmp_path / "src" / "a.py"))
    assert isinstance(request, MutationRequest)
    assert request.session_id == "session-1"
    assert request.tool_use_id == "tool-1"
    assert request.cwd == tmp_path
    assert request.file_paths == (tmp_path / "src" / "a.py",)
    assert request.allow_semantic_source is True


def test_parse_hook_request_rejects_unverified_tool_use_id_alias(tmp_path: Path) -> None:
    payload = json.loads(_event(tmp_path, tmp_path / "src" / "a.py"))
    payload["tool_use_id"] = payload.pop("tool_call_id")
    with pytest.raises(KimiClaimGuardError, match="input.tool_call_id"):
        parse_hook_request(json.dumps(payload))


def test_parse_hook_request_rejects_unverified_file_path_alias(tmp_path: Path) -> None:
    payload = json.loads(_event(tmp_path, tmp_path / "src" / "a.py"))
    payload["tool_input"]["file_path"] = payload["tool_input"].pop("path")
    with pytest.raises(KimiClaimGuardError, match="tool_input.path"):
        parse_hook_request(json.dumps(payload))


def test_parse_hook_request_accepts_installed_kimi_0233_shape() -> None:
    """Exercise the snake-case shape emitted by the installed Kimi 0.23.3 hook engine."""
    raw = json.dumps(
        {
            "hook_event_name": "PreToolUse",
            "session_id": "session-1",
            "cwd": "/tmp/synapse-kimi-payload-test",
            "tool_name": "Write",
            "tool_input": {
                "path": "/tmp/synapse-kimi-payload-test/hello.txt",
                "content": "hello",
            },
            "tool_call_id": "tool-1",
        }
    )
    request = parse_hook_request(raw)
    assert isinstance(request, MutationRequest)
    assert request.session_id == "session-1"
    assert request.tool_use_id == "tool-1"
    assert request.cwd == Path("/tmp/synapse-kimi-payload-test")
    assert request.file_paths == (Path("/tmp/synapse-kimi-payload-test/hello.txt"),)
    assert request.allow_semantic_source is False


def test_parse_hook_request_rejects_wrong_event(tmp_path: Path) -> None:
    payload = json.loads(_event(tmp_path, tmp_path / "src" / "a.py"))
    payload["hook_event_name"] = "PostToolUse"
    with pytest.raises(KimiClaimGuardError, match="only PreToolUse"):
        parse_hook_request(json.dumps(payload))


def test_parse_hook_request_rejects_unsupported_tool(tmp_path: Path) -> None:
    with pytest.raises(KimiClaimGuardError, match="only Edit, Write, or Bash"):
        parse_hook_request(_event(tmp_path, tmp_path / "src" / "a.py", tool="WebSearch"))


def test_parse_hook_request_rejects_missing_path(tmp_path: Path) -> None:
    payload = json.loads(_event(tmp_path, tmp_path / "src" / "a.py"))
    payload["tool_input"] = {}
    with pytest.raises(KimiClaimGuardError, match="tool_input\\.path"):
        parse_hook_request(json.dumps(payload))


def test_parse_hook_request_rejects_relative_cwd(tmp_path: Path) -> None:
    payload = json.loads(_event(tmp_path, tmp_path / "src" / "a.py"))
    payload["cwd"] = "relative/path"
    with pytest.raises(KimiClaimGuardError, match="cwd must be absolute"):
        parse_hook_request(json.dumps(payload))


def test_parse_hook_request_rejects_invalid_json() -> None:
    with pytest.raises(KimiClaimGuardError, match="not valid JSON"):
        parse_hook_request("{not-json")


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"hook_event_name": "PreToolUse"},
        {
            "session_id": "session-1",
            "tool_call_id": "",
            "cwd": "/repo",
            "hook_event_name": "PreToolUse",
            "tool_name": "Write",
            "tool_input": {"path": "a.py"},
        },
        {
            "session_id": "session-1",
            "tool_call_id": "tool-1",
            "cwd": "/repo",
            "hook_event_name": "PreToolUse",
            "tool_name": "Write",
            "tool_input": None,
        },
    ],
)
def test_parse_hook_request_rejects_other_malformed_shapes(payload: object) -> None:
    with pytest.raises(KimiClaimGuardError):
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
