# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real CLI/hub journey for the Kimi Edit/Write claim guard

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

from cli_e2e_helpers import free_port, git_repo, isolated_hub, run_cli


def _event(repo: Path, target: Path, *, tool: str = "Write") -> str:
    return json.dumps(
        {
            "session_id": "e2e-session",
            "tool_call_id": f"tool-{target.name}",
            "cwd": str(repo),
            "hook_event_name": "PreToolUse",
            "tool_name": tool,
            "tool_input": {"path": str(target), "content": "content\n"},
        }
    )


def test_live_claim_allows_owned_file_and_denies_unclaimed_file(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")
    (repo / "src").mkdir()
    with isolated_hub(tmp_path) as hub:
        claimed = run_cli(
            "git-claim",
            "KIMI-E2E",
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
            "kimi-claim-hook",
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
            "kimi-claim-hook",
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
            "kimi-claim-hook",
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
            "kimi-claim-hook",
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
    with isolated_hub(tmp_path, extra_args=("--token-file", str(token_file))) as hub:
        claimed = run_cli(
            "git-claim",
            "KIMI-TOKEN-E2E",
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
            "kimi-claim-hook",
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
        rendered_text = rendered.stdout
        assert "[[hooks]]" in rendered_text
        assert 'event = "PreToolUse"' in rendered_text
        assert "secured-token" not in rendered_text
        # Extract the command string from the printed TOML. Drop the resolved
        # binary (here sys.executable, because the test passed --synapse-bin)
        # and run the remaining args through the test harness entrypoint.
        if sys.version_info >= (3, 11):
            import tomllib
        else:  # pragma: no cover - exercised only on Python 3.10
            import tomli as tomllib

        command_line = str(tomllib.loads(rendered_text)["hooks"][0]["command"])
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
        "kimi-claim-hook",
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
        "kimi-claim-hook",
        "--identity",
        "seat/one",
        "--print-config",
        "--synapse-bin",
        sys.executable,
        cwd=tmp_path,
    )
    assert result.ok(), result.output
    rendered = result.stdout
    assert "[[hooks]]" in rendered
    assert 'matcher = "^(Write|Edit)$"' in rendered
    assert not (tmp_path / ".kimi-code").exists()


def test_cli_installs_and_uninstalls_config_under_kimi_code_home(tmp_path: Path) -> None:
    kimi_home = tmp_path / "kimi-home"
    environment = {"KIMI_CODE_HOME": str(kimi_home)}
    installed = run_cli(
        "adapters",
        "kimi-claim-hook",
        "--identity",
        "seat/one",
        "--install-config",
        "--synapse-bin",
        sys.executable,
        cwd=tmp_path,
        env=environment,
    )
    assert installed.ok(), installed.output
    config_path = kimi_home / "config.toml"
    content = config_path.read_text(encoding="utf-8")
    assert "synapse-channel:kimi-hook:begin" in content
    assert "seat/one" in content

    removed = run_cli(
        "adapters",
        "kimi-claim-hook",
        "--uninstall-config",
        cwd=tmp_path,
        env=environment,
    )
    assert removed.ok(), removed.output
    assert not config_path.exists()
