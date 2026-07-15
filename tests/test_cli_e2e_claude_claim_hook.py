# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real CLI/hub journey for the Claude Edit/Write claim guard

from __future__ import annotations

import json
import sys
from pathlib import Path

from cli_e2e_helpers import free_port, git_repo, isolated_hub, run_cli


def _event(repo: Path, target: Path) -> str:
    return json.dumps(
        {
            "session_id": "e2e-session",
            "tool_use_id": f"tool-{target.name}",
            "cwd": str(repo),
            "hook_event_name": "PreToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": str(target), "content": "content\n"},
        }
    )


def test_live_claim_allows_owned_file_and_denies_unclaimed_file(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")
    (repo / "src").mkdir()
    with isolated_hub(tmp_path) as hub:
        claimed = run_cli(
            "git-claim",
            "CLAUDE-E2E",
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
            "claude-claim-hook",
            "--identity",
            "seat/one",
            stdin=_event(repo, repo / "src" / "owned.py"),
            uri=hub.uri,
            cwd=repo,
        )
        assert allowed.ok(), allowed.output
        assert allowed.stdout == ""

        denied = run_cli(
            "adapters",
            "claude-claim-hook",
            "--identity",
            "seat/one",
            stdin=_event(repo, repo / "src" / "other.py"),
            uri=hub.uri,
            cwd=repo,
        )
        assert denied.ok(), denied.output
        output = json.loads(denied.stdout)
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"

        wrong_owner = run_cli(
            "adapters",
            "claude-claim-hook",
            "--identity",
            "seat/two",
            stdin=_event(repo, repo / "src" / "owned.py"),
            uri=hub.uri,
            cwd=repo,
        )
        assert wrong_owner.ok(), wrong_owner.output
        wrong_owner_output = json.loads(wrong_owner.stdout)
        assert wrong_owner_output["hookSpecificOutput"]["permissionDecision"] == "deny"

        malformed = run_cli(
            "adapters",
            "claude-claim-hook",
            "--identity",
            "seat/one",
            stdin="{not-json",
            uri=hub.uri,
            cwd=repo,
        )
        assert malformed.ok(), malformed.output
        malformed_output = json.loads(malformed.stdout)
        assert malformed_output["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_printed_token_file_recipe_authenticates_secured_hub(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")
    token_file = tmp_path / "hub.token"
    token_file.write_text("secured-token\n", encoding="utf-8")
    token_file.chmod(0o600)
    with isolated_hub(tmp_path, extra_args=("--token-file", str(token_file))) as hub:
        claimed = run_cli(
            "git-claim",
            "CLAUDE-TOKEN-E2E",
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
            "claude-claim-hook",
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
        hook = json.loads(rendered.stdout)["hooks"]["PreToolUse"][0]["hooks"][0]
        command_args = hook["args"]
        assert command_args[command_args.index("--token-file") + 1] == str(token_file.resolve())
        assert "secured-token" not in json.dumps(hook)

        allowed = run_cli(
            *command_args,
            stdin=_event(repo, repo / "README.md"),
            cwd=repo,
        )
        assert allowed.ok(), allowed.output
        assert allowed.stdout == ""


def test_unreachable_hub_denies_on_exit_zero(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")
    unused_port = free_port()
    result = run_cli(
        "adapters",
        "claude-claim-hook",
        "--identity",
        "seat/one",
        "--ready-timeout",
        "0.05",
        stdin=_event(repo, repo / "README.md"),
        uri=f"ws://127.0.0.1:{unused_port}",
        cwd=repo,
    )
    assert result.ok(), result.output
    output = json.loads(result.stdout)
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "unavailable" in output["hookSpecificOutput"]["permissionDecisionReason"]


def test_cli_prints_mergeable_config_without_writing_settings(tmp_path: Path) -> None:
    result = run_cli(
        "adapters",
        "claude-claim-hook",
        "--identity",
        "seat/one",
        "--print-config",
        "--synapse-bin",
        sys.executable,
        cwd=tmp_path,
    )
    assert result.ok(), result.output
    config = json.loads(result.stdout)
    assert config["hooks"]["PreToolUse"][0]["matcher"] == "Edit|Write|Bash"
    assert not (tmp_path / ".claude").exists()
