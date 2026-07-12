# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real CLI/hub journey for the Gemini BeforeTool claim guard

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

from cli_e2e_helpers import free_port, git_repo, isolated_hub, run_cli


def _event(repo: Path, target: Path, *, tool: str = "write_file") -> str:
    return json.dumps(
        {
            "session_id": "e2e-session",
            "transcript_path": str(repo / "transcript.json"),
            "cwd": str(repo),
            "hook_event_name": "BeforeTool",
            "timestamp": f"2026-07-12T15:30:00.{abs(hash(target.name)) % 1000:03d}Z",
            "tool_name": tool,
            "tool_input": {"file_path": str(target), "content": "content\n"},
        }
    )


def test_live_claim_allows_owned_file_and_denies_unclaimed_file(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")
    (repo / "src").mkdir()
    with isolated_hub(tmp_path) as hub:
        claimed = run_cli(
            "git-claim",
            "GEMINI-E2E",
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
            "gemini-claim-hook",
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
            "gemini-claim-hook",
            "--identity",
            "seat/one",
            stdin=_event(repo, repo / "src" / "other.py"),
            uri=hub.uri,
            cwd=repo,
        )
        assert denied.ok(), denied.output
        output = json.loads(denied.stdout)
        assert output["decision"] == "deny"
        assert "claim" in output["reason"].lower()

        wrong_owner = run_cli(
            "adapters",
            "gemini-claim-hook",
            "--identity",
            "seat/two",
            stdin=_event(repo, repo / "src" / "owned.py"),
            uri=hub.uri,
            cwd=repo,
        )
        assert wrong_owner.ok(), wrong_owner.output
        assert json.loads(wrong_owner.stdout)["decision"] == "deny"

        malformed = run_cli(
            "adapters",
            "gemini-claim-hook",
            "--identity",
            "seat/one",
            stdin="{not-json",
            uri=hub.uri,
            cwd=repo,
        )
        assert malformed.ok(), malformed.output
        assert json.loads(malformed.stdout)["decision"] == "deny"


def test_printed_token_file_recipe_authenticates_secured_hub(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")
    token_file = tmp_path / "hub.token"
    token_file.write_text("secured-token\n", encoding="utf-8")
    with isolated_hub(tmp_path, extra_args=("--token-file", str(token_file))) as hub:
        claimed = run_cli(
            "git-claim",
            "GEMINI-TOKEN-E2E",
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
            "gemini-claim-hook",
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
        config = json.loads(rendered.stdout)
        assert "secured-token" not in rendered.stdout
        # Extract the command from the printed settings.json fragment. Drop the
        # resolved binary (sys.executable, because the test passed --synapse-bin)
        # and run the remaining args through the test harness entrypoint.
        command_line = str(config["hooks"]["BeforeTool"][0]["hooks"][0]["command"])
        parsed = shlex.split(command_line)
        command_args = parsed[1:]
        assert parsed[0]
        assert command_args[command_args.index("--token-file") + 1] == str(token_file.resolve())

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
        "gemini-claim-hook",
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
    assert output["decision"] == "deny"
    assert "unavailable" in output["reason"]


def test_cli_prints_mergeable_config_without_writing_settings(tmp_path: Path) -> None:
    result = run_cli(
        "adapters",
        "gemini-claim-hook",
        "--identity",
        "seat/one",
        "--print-config",
        "--synapse-bin",
        sys.executable,
        cwd=tmp_path,
    )
    assert result.ok(), result.output
    config = json.loads(result.stdout)
    assert config["hooks"]["BeforeTool"][0]["matcher"] == "^(replace|write_file)$"
    assert not (tmp_path / ".gemini").exists()
