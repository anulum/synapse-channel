# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Claude Code plugin CLI and real validator tests
"""Exercise public onboarding commands against an isolated host profile."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from cli_e2e_helpers import git_repo, git_run, isolated_hub, run_cli
from synapse_channel import cli


def _invoke(root: Path, action: str, *extra: str) -> int:
    return cli.main(
        [
            "adapters",
            "claude-plugin",
            action,
            "--config-root",
            str(root),
            "--identity",
            "TEST/claude",
            "--uri",
            "ws://127.0.0.1:8876",
            *extra,
        ]
    )


def test_dry_run_and_inspect_do_not_touch_a_profile(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "profile"
    assert _invoke(root, "inspect") == 0
    assert json.loads(capsys.readouterr().out)["state"] == "absent"
    assert _invoke(root, "dry-run") == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["operation"] == "install"
    assert plan["would_change"] is True
    assert not root.exists()
    assert _invoke(root, "dry-run", "--operation", "uninstall") == 0
    assert json.loads(capsys.readouterr().out)["would_change"] is False
    assert _invoke(root, "uninstall") == 0
    assert json.loads(capsys.readouterr().out)["state"] == "absent"


def test_diagnose_and_install_fail_cleanly_for_missing_host_or_bad_identity(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "profile"
    assert _invoke(root, "install", "--claude-bin", str(tmp_path / "missing")) == 2
    assert "unavailable" in capsys.readouterr().err
    assert not root.exists()
    if shutil.which("claude") is None:
        pytest.skip("Claude Code host unavailable")
    assert _invoke(root, "diagnose") == 1
    assert json.loads(capsys.readouterr().out)["host_valid"] is False
    assert _invoke(root, "install", "--identity", "invalid") == 2
    assert "exact project/seat" in capsys.readouterr().err
    assert not (root / "skills/synapse-channel").exists()


@pytest.mark.skipif(shutil.which("claude") is None, reason="Claude Code host unavailable")
def test_installer_rejects_a_non_synapse_command_before_any_write(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "profile"
    assert _invoke(root, "install", "--synapse-bin", "/bin/true") == 2
    assert "selected Synapse command" in capsys.readouterr().err
    assert not root.exists()


@pytest.mark.skipif(shutil.which("claude") is None, reason="Claude Code host unavailable")
def test_installer_requires_token_file_capable_synapse_and_never_embeds_secret(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source_bin = Path(__file__).resolve().parents[1] / ".venv/bin/synapse"
    if not source_bin.is_file():
        pytest.skip("source environment has no token-file-capable Synapse command")
    root = tmp_path / "profile"
    token = tmp_path / "secret"
    token.write_text("private-test-token\n", encoding="utf-8")
    token.chmod(0o600)
    assert (
        _invoke(root, "install", "--synapse-bin", str(source_bin), "--token-file", str(token)) == 0
    )
    assert "private-test-token" not in capsys.readouterr().out
    assert "private-test-token" not in (root / "skills/synapse-channel/.mcp.json").read_text(
        encoding="utf-8"
    )
    assert _invoke(root, "uninstall") == 0
    capsys.readouterr()


@pytest.mark.skipif(shutil.which("claude") is None, reason="Claude Code host unavailable")
def test_installer_refuses_an_unverified_claude_version(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "profile"
    assert _invoke(root, "install", "--claude-bin", "/bin/echo") == 2
    assert "unverified" in capsys.readouterr().err
    assert not root.exists()


@pytest.mark.skipif(shutil.which("claude") is None, reason="Claude Code host unavailable")
def test_real_host_validates_install_upgrade_and_removal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "profile"
    root.mkdir()
    settings = root / "settings.json"
    settings.write_text('{"theme":"dark"}\n', encoding="utf-8")
    original = settings.read_bytes()
    assert _invoke(root, "install") == 0
    assert json.loads(capsys.readouterr().out)["state"] == "owned"
    host_environment = dict(os.environ, CLAUDE_CONFIG_DIR=str(root))
    loaded = subprocess.run(
        ["claude", "plugin", "list", "--json"],
        env=host_environment,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert loaded.returncode == 0, loaded.stderr
    assert any(
        entry["id"] == "synapse-channel@skills-dir" and entry["enabled"]
        for entry in json.loads(loaded.stdout)
    )
    details = subprocess.run(
        ["claude", "plugin", "details", "synapse-channel@skills-dir"],
        env=host_environment,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert details.returncode == 0, details.stderr
    assert "Hooks (1)" in details.stdout and "MCP servers (1)" in details.stdout
    assert _invoke(root, "diagnose") == 0
    diagnosed = json.loads(capsys.readouterr().out)
    assert diagnosed["host_valid"] is True
    assert "2.1.278" in diagnosed["host_version"]
    assert _invoke(root, "upgrade") == 0
    assert json.loads(capsys.readouterr().out)["state"] == "owned"
    assert _invoke(root, "uninstall") == 0
    assert json.loads(capsys.readouterr().out)["state"] == "absent"
    assert settings.read_bytes() == original


async def test_installed_plugin_reads_real_hub_and_enforces_live_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mcp = pytest.importorskip("mcp")
    mcp_stdio = pytest.importorskip("mcp.client.stdio")
    root = tmp_path / "profile"
    repo = git_repo(tmp_path / "workspace")
    git_run(repo, "branch", "-M", "main")
    source_bin = Path(__file__).resolve().parents[1] / ".venv/bin/synapse"
    if not source_bin.is_file():
        source_bin = Path(sys.executable).parent / "synapse"
    if not source_bin.is_file():
        pytest.skip("source environment has no synapse entrypoint")
    with isolated_hub(tmp_path) as hub:
        install = run_cli(
            "adapters",
            "claude-plugin",
            "install",
            "--config-root",
            str(root),
            "--identity",
            "TEST/claude",
            "--uri",
            hub.uri,
            "--synapse-bin",
            str(source_bin),
        )
        assert install.returncode == 0, install.stderr
        plugin = root / "skills/synapse-channel"
        config = json.loads((plugin / ".mcp.json").read_text(encoding="utf-8"))
        server = config["mcpServers"]["synapse"]
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
        monkeypatch.chdir(repo)
        params = mcp.StdioServerParameters(
            command=server["command"],
            args=server["args"],
            env=environment,
        )
        async with mcp_stdio.stdio_client(params) as (read, write):
            async with mcp.ClientSession(read, write) as session:
                await session.initialize()
                names = {tool.name for tool in (await session.list_tools()).tools}
                assert {"synapse_board", "synapse_git_claim"} <= names
                board = await session.call_tool("synapse_board", {})
                assert not board.isError, board
                claim = await session.call_tool(
                    "synapse_git_claim", {"task_id": "plugin-claim", "paths": ["owned.txt"]}
                )
                assert not claim.isError, claim

        hook = json.loads((plugin / "hooks/hooks.json").read_text(encoding="utf-8"))
        command = hook["hooks"]["PreToolUse"][0]["hooks"][0]
        for filename, allowed in (("owned.txt", True), ("unclaimed.txt", False)):
            target = repo / filename
            event = {
                "session_id": "plugin-host-test",
                "tool_use_id": filename,
                "cwd": str(repo),
                "hook_event_name": "PreToolUse",
                "tool_name": "Write",
                "tool_input": {"file_path": str(target), "content": "hello"},
            }
            result = subprocess.run(
                [command["command"], *command["args"]],
                input=json.dumps(event),
                text=True,
                capture_output=True,
                cwd=repo,
                env=environment,
                timeout=15,
                check=False,
            )
            assert result.returncode == 0, result.stderr
            if allowed:
                assert result.stdout == ""
                target.write_text("hello", encoding="utf-8")
            else:
                denial = json.loads(result.stdout)["hookSpecificOutput"]
                assert denial["permissionDecision"] == "deny"
                assert not target.exists()
        assert (repo / "owned.txt").read_text(encoding="utf-8") == "hello"
        released = run_cli("release", "plugin-claim", "--name", "TEST/claude", uri=hub.uri)
        assert released.returncode == 0, released.stderr
