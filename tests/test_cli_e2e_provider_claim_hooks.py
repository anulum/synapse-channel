# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real CLI/hub journeys for Codex, Gemini, and Kimi claim guards

from __future__ import annotations

import json
import shlex
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from cli_e2e_helpers import git_repo, isolated_hub, run_cli


def _codex_event(repo: Path, path: str) -> str:
    command = f"*** Begin Patch\n*** Update File: {path}\n*** End Patch"
    return json.dumps(
        {
            "session_id": "codex-session",
            "tool_use_id": f"tool-{Path(path).name}",
            "cwd": str(repo),
            "hook_event_name": "PreToolUse",
            "tool_name": "apply_patch",
            "tool_input": {"command": command},
        }
    )


def _kimi_event(repo: Path, path: str) -> str:
    return json.dumps(
        {
            "session_id": "kimi-session",
            "tool_call_id": f"tool-{Path(path).name}",
            "cwd": str(repo),
            "hook_event_name": "PreToolUse",
            "tool_name": "Edit",
            "tool_input": {"path": path, "old_string": "a", "new_string": "b"},
        }
    )


def _gemini_event(repo: Path, path: str) -> str:
    return json.dumps(
        {
            "session_id": "gemini-session",
            "transcript_path": str(repo / "transcript.json"),
            "cwd": str(repo),
            "hook_event_name": "BeforeTool",
            "timestamp": "2026-07-12T15:30:00.000Z",
            "tool_name": "write_file",
            "tool_input": {"file_path": path, "content": "content\n"},
        }
    )


def _rendered_command(provider: str, output: str) -> str:
    if provider == "codex-claim-hook":
        return str(json.loads(output)["hooks"]["PreToolUse"][0]["hooks"][0]["command"])
    if provider == "gemini-claim-hook":
        return str(json.loads(output)["hooks"]["BeforeTool"][0]["hooks"][0]["command"])
    if sys.version_info >= (3, 11):
        import tomllib
    else:  # pragma: no cover - exercised only on Python 3.10
        import tomli as tomllib

    return str(tomllib.loads(output)["hooks"][0]["command"])


def _deny_reason(provider: str, stdout: str) -> str:
    """Return the denial reason from the provider's native structured deny."""
    output = json.loads(stdout)
    if provider == "gemini-claim-hook":
        assert output["decision"] == "deny"
        return str(output["reason"])
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
    return str(output["hookSpecificOutput"]["permissionDecisionReason"])


_PROVIDER_CASES = [
    ("codex-claim-hook", _codex_event),
    ("gemini-claim-hook", _gemini_event),
    ("kimi-claim-hook", _kimi_event),
]


@pytest.mark.parametrize(("command", "event"), _PROVIDER_CASES)
def test_live_claim_allows_owned_and_denies_unclaimed_file(
    tmp_path: Path, command: str, event: Callable[[Path, str], str]
) -> None:
    repo = git_repo(tmp_path / command)
    (repo / "src").mkdir()
    with isolated_hub(tmp_path) as hub:
        claimed = run_cli(
            "git-claim",
            f"{command}-E2E",
            "--paths",
            "src/owned.py",
            "--auto-release-on",
            "manual",
            "--name",
            "seat/one",
            uri=hub.uri,
            cwd=repo,
        )
        assert claimed.ok(), claimed.output

        allowed = run_cli(
            "adapters",
            command,
            "--identity",
            "seat/one",
            stdin=event(repo, "src/owned.py"),
            uri=hub.uri,
            cwd=repo,
        )
        assert allowed.ok(), allowed.output
        assert allowed.stdout == ""

        denied = run_cli(
            "adapters",
            command,
            "--identity",
            "seat/one",
            stdin=event(repo, "src/other.py"),
            uri=hub.uri,
            cwd=repo,
        )
        assert denied.ok(), denied.output
        assert "claim" in _deny_reason(command, denied.stdout).lower()


@pytest.mark.parametrize(("command", "event"), _PROVIDER_CASES)
def test_printed_recipe_authenticates_token_secured_hub(
    tmp_path: Path, command: str, event: Callable[[Path, str], str]
) -> None:
    repo = git_repo(tmp_path / command)
    token_file = tmp_path / "hub.token"
    token_file.write_text("secured-token\n", encoding="utf-8")
    with isolated_hub(tmp_path, extra_args=("--token-file", str(token_file))) as hub:
        claimed = run_cli(
            "git-claim",
            f"{command}-TOKEN-E2E",
            "--paths",
            "README.md",
            "--auto-release-on",
            "manual",
            "--name",
            "seat/one",
            "--token-file",
            str(token_file),
            uri=hub.uri,
            cwd=repo,
        )
        assert claimed.ok(), claimed.output

        rendered = run_cli(
            "adapters",
            command,
            "--identity",
            "seat/one",
            "--token-file",
            str(token_file),
            "--print-config",
            "--synapse-bin",
            sys.executable,
            uri=hub.uri,
            cwd=repo,
        )
        assert rendered.ok(), rendered.output
        assert "secured-token" not in rendered.stdout
        argv = shlex.split(_rendered_command(command, rendered.stdout))
        assert argv[0] == str(Path(sys.executable).resolve())

        allowed = run_cli(
            *argv[1:],
            stdin=event(repo, "README.md"),
            cwd=repo,
        )
        assert allowed.ok(), allowed.output
        assert allowed.stdout == ""
