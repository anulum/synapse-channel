# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Codex apply_patch claim guard regressions

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.codex_claim_guard import (
    CodexClaimGuardError,
    evaluate_hook_event,
    parse_apply_patch_paths,
    parse_hook_request,
)
from synapse_channel.file_claim_guard import MutationRequest


def _patch(*headers: str) -> str:
    return "\n".join(("*** Begin Patch", *headers, "*** End Patch"))


def _event(root: Path, command: str, *, tool: str = "apply_patch") -> str:
    return json.dumps(
        {
            "session_id": "session",
            "turn_id": "turn",
            "tool_use_id": "tool",
            "cwd": str(root),
            "hook_event_name": "PreToolUse",
            "tool_name": tool,
            "tool_input": {"command": command},
        }
    )


def _runner(root: Path) -> Callable[[list[str]], str]:
    return lambda _args: f"{root}\nmain"


def _claim(root: Path, paths: list[str]) -> dict[str, Any]:
    return {
        "task_id": "CODEX",
        "owner": "seat/one",
        "status": "claimed",
        "worktree": str(root),
        "paths": paths,
        "git": {"branch": "main", "base": "main", "auto_release_on": "manual"},
    }


def test_patch_parser_collects_add_update_delete_and_move_paths() -> None:
    command = _patch(
        "*** Add File: src/add.py",
        "*** Update File: src/old.py",
        "*** Move to: src/new.py",
        "*** Delete File: src/delete.py",
        "*** End of File",
    )
    assert parse_apply_patch_paths(command) == (
        Path("src/add.py"),
        Path("src/old.py"),
        Path("src/new.py"),
        Path("src/delete.py"),
    )


def test_hook_parser_maps_codex_wire_shape_to_generic_request(tmp_path: Path) -> None:
    request = parse_hook_request(_event(tmp_path, _patch("*** Update File: src/a.py")))
    assert request == MutationRequest("session", "tool", tmp_path, (Path("src/a.py"),))
    assert request.allow_semantic_source is False


@pytest.mark.parametrize(
    "command",
    [
        "",
        "*** Begin Patch\n*** End Patch",
        "*** Begin Patch\n*** Update File: src/a.py",
        _patch("*** Update File: "),
        _patch("*** Update File:  src/a.py"),
        _patch("*** Unknown: src/a.py"),
    ],
)
def test_patch_parser_rejects_ambiguous_or_empty_commands(command: str) -> None:
    with pytest.raises(CodexClaimGuardError):
        parse_apply_patch_paths(command)


@pytest.mark.parametrize(
    "raw",
    [
        "not-json",
        "[]",
        json.dumps({"hook_event_name": "PostToolUse"}),
        json.dumps(
            {
                "session_id": "s",
                "tool_use_id": "t",
                "cwd": "/repo",
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": _patch("*** Update File: a.py")},
            }
        ),
        json.dumps(
            {
                "session_id": "s",
                "tool_use_id": "t",
                "cwd": "relative",
                "hook_event_name": "PreToolUse",
                "tool_name": "apply_patch",
                "tool_input": {"command": _patch("*** Update File: a.py")},
            }
        ),
        json.dumps(
            {
                "session_id": "s",
                "tool_use_id": "t",
                "cwd": "/repo",
                "hook_event_name": "PreToolUse",
                "tool_name": "apply_patch",
                "tool_input": None,
            }
        ),
    ],
)
def test_hook_parser_rejects_malformed_or_unsupported_events(raw: str) -> None:
    with pytest.raises(CodexClaimGuardError):
        parse_hook_request(raw)


@pytest.mark.parametrize("session_id", ["", 7])
def test_hook_parser_rejects_missing_or_non_string_session_id(
    tmp_path: Path, session_id: object
) -> None:
    event = json.loads(_event(tmp_path, _patch("*** Update File: a.py")))
    event["session_id"] = session_id
    with pytest.raises(CodexClaimGuardError, match="session_id"):
        parse_hook_request(json.dumps(event))


@pytest.mark.asyncio
async def test_codex_evaluation_requires_claims_for_every_patch_path(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    command = _patch("*** Update File: src/a.py", "*** Add File: src/b.py")

    async def partial(**_kwargs: object) -> dict[str, Any]:
        return {"active_claims": [_claim(tmp_path, ["src/a.py"])]}

    denied = await evaluate_hook_event(
        _event(tmp_path, command),
        identity="seat/one",
        uri="ws://hub",
        token=None,
        timeout=0.1,
        state_fetcher=partial,
        git_runner=_runner(tmp_path),
    )
    assert not denied.allowed
    assert "src/b.py" in denied.reason

    async def complete(**_kwargs: object) -> dict[str, Any]:
        return {"active_claims": [_claim(tmp_path, ["src/a.py", "src/b.py"])]}

    allowed = await evaluate_hook_event(
        _event(tmp_path, command),
        identity="seat/one",
        uri="ws://hub",
        token=None,
        timeout=0.1,
        state_fetcher=complete,
        git_runner=_runner(tmp_path),
    )
    assert allowed.allowed


@pytest.mark.asyncio
async def test_codex_malformed_event_denies_without_querying_hub(tmp_path: Path) -> None:
    async def must_not_run(**_kwargs: object) -> dict[str, Any]:
        raise AssertionError("malformed input must not query the hub")

    verdict = await evaluate_hook_event(
        _event(tmp_path, _patch("*** Update File: a.py"), tool="Bash"),
        identity="seat/one",
        uri="ws://hub",
        token=None,
        timeout=0.1,
        state_fetcher=must_not_run,
    )
    assert not verdict.allowed
    assert "only apply_patch" in verdict.reason
