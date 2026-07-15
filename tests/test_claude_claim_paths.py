# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Claude hook input and canonical path regressions

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from synapse_channel.claude_claim_guard import (
    ClaimGuardError,
    HookRequest,
    RepositoryTarget,
    claim_path_covers,
    parse_hook_request,
    resolve_repository_target,
)
from synapse_channel.git.gitclaim import GitError


def _event(root: Path, path: Path, *, tool: str = "Edit") -> str:
    return json.dumps(
        {
            "session_id": "session-1",
            "tool_use_id": "tool-1",
            "cwd": str(root),
            "hook_event_name": "PreToolUse",
            "tool_name": tool,
            "tool_input": {"file_path": str(path), "old_string": "a", "new_string": "b"},
        }
    )


def _runner(root: Path, branch: str = "main") -> Callable[[list[str]], str]:
    def run(args: list[str]) -> str:
        assert args[-4:] == ["rev-parse", "--show-toplevel", "--abbrev-ref", "HEAD"]
        return f"{root}\n{branch}"

    return run


def test_parse_hook_request_accepts_strict_edit_shape(tmp_path: Path) -> None:
    target = tmp_path / "src" / "module.py"
    request = parse_hook_request(_event(tmp_path, target))
    assert request == HookRequest(
        session_id="session-1",
        tool_use_id="tool-1",
        tool_name="Edit",
        cwd=tmp_path,
        file_path=target,
    )


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
                "tool_input": {"file_path": "/repo/a.py"},
            }
        ),
        json.dumps(
            {
                "session_id": "s",
                "tool_use_id": "t",
                "cwd": "/repo",
                "hook_event_name": "PreToolUse",
                "tool_name": "Write",
                "tool_input": {"file_path": "relative.py"},
            }
        ),
        json.dumps(
            {
                "session_id": "s",
                "tool_use_id": "t",
                "cwd": "/repo",
                "hook_event_name": "PreToolUse",
                "tool_name": "Write",
                "tool_input": None,
            }
        ),
        json.dumps(
            {
                "session_id": "s",
                "tool_use_id": "t",
                "cwd": "relative",
                "hook_event_name": "PreToolUse",
                "tool_name": "Write",
                "tool_input": {"file_path": "/repo/a.py"},
            }
        ),
        json.dumps(
            {
                "session_id": "",
                "tool_use_id": "t",
                "cwd": "/repo",
                "hook_event_name": "PreToolUse",
                "tool_name": "Write",
                "tool_input": {"file_path": "/repo/a.py"},
            }
        ),
    ],
)
def test_parse_hook_request_rejects_unsupported_or_malformed_input(raw: str) -> None:
    with pytest.raises(ClaimGuardError):
        parse_hook_request(raw)


def test_resolve_repository_target_canonicalises_repo_relative_path(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    request = parse_hook_request(_event(tmp_path, tmp_path / "src" / "new.py", tool="Write"))
    assert isinstance(request, HookRequest)
    target = resolve_repository_target(request, runner=_runner(tmp_path, "feature/x"))
    assert target == RepositoryTarget(tmp_path.resolve(), "feature/x", "src/new.py")


def test_resolve_repository_target_denies_symlink_escape(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    outside = tmp_path / "outside"
    repo.mkdir()
    outside.mkdir()
    (repo / "linked").symlink_to(outside, target_is_directory=True)
    request = parse_hook_request(_event(repo, repo / "linked" / "escape.py", tool="Write"))
    assert isinstance(request, HookRequest)
    with pytest.raises(ClaimGuardError, match="escapes"):
        resolve_repository_target(request, runner=_runner(repo))


def test_resolve_repository_target_walks_to_existing_parent(tmp_path: Path) -> None:
    target_path = tmp_path / "missing" / "nested" / "new.py"
    request = parse_hook_request(_event(tmp_path, target_path, tool="Write"))
    assert isinstance(request, HookRequest)

    def runner(args: list[str]) -> str:
        assert args[1] == str(tmp_path)
        return f"{tmp_path}\nmain"

    target = resolve_repository_target(request, runner=runner)
    assert target.relative_path == "missing/nested/new.py"


def test_resolve_repository_target_denies_directory_and_bad_git_context(tmp_path: Path) -> None:
    directory_request = parse_hook_request(_event(tmp_path, tmp_path, tool="Write"))
    assert isinstance(directory_request, HookRequest)
    with pytest.raises(ClaimGuardError, match="file path"):
        resolve_repository_target(directory_request, runner=_runner(tmp_path))

    file_request = parse_hook_request(_event(tmp_path, tmp_path / "new.py", tool="Write"))
    assert isinstance(file_request, HookRequest)

    def broken(_args: list[str]) -> str:
        raise GitError("not a repository")

    with pytest.raises(ClaimGuardError, match="readable Git"):
        resolve_repository_target(file_request, runner=broken)
    with pytest.raises(ClaimGuardError, match="invalid worktree"):
        resolve_repository_target(file_request, runner=lambda _args: "only-one-line")


def test_claim_path_coverage_is_one_way() -> None:
    assert claim_path_covers("src", "src/package/module.py")
    assert claim_path_covers("src/package/module.py", "src/package/module.py")
    assert claim_path_covers("", "any/file.py")
    assert not claim_path_covers("src/package/module.py", "src/package")
    assert not claim_path_covers("src/one", "src/other.py")
