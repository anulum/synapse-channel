# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — doctor finds Synapse-named MCP servers and unguarded hosts
"""K4-MCP-INBOUND (a)+(b): ``doctor`` names impostor-shaped MCP servers and missing guards.

Every case writes real host files under a temporary home and project — Claude's
``.claude.json`` and ``.mcp.json``, a real Synapse plugin install, the Codex
``config.toml`` and ``hooks.json`` produced by the real hook renderers — and runs
the real checks. Commands resolve against a controlled ``PATH`` of real files.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.claude_plugin_install import apply_plugin, plugin_path
from synapse_channel.cli_claim_hook_common import resolve_synapse_binary
from synapse_channel.cli_claude_claim_hook import render_hook_config as claude_hook
from synapse_channel.cli_codex_claim_hook import render_hook_config as codex_hook
from synapse_channel.cli_doctor import _diagnose
from synapse_channel.cli_doctor_mcp_hosts import (
    McpServerDefinition,
    classify_definition,
    default_project,
    diagnose_mcp_hosts,
)
from synapse_channel.client.diagnostics import Diagnosis

THIS_SYNAPSE = resolve_synapse_binary(None)


def _executable(directory: Path, name: str, body: str = "#!/bin/sh\nexit 0\n") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _checks(home: Path, project: Path, **kwargs: Any) -> dict[str, Diagnosis]:
    env = kwargs.pop("env", {})
    return {
        diagnosis.check: diagnosis
        for diagnosis in diagnose_mcp_hosts(home=home, project=project, env=env, **kwargs)
    }


def _claude_guard(settings: Path) -> None:
    _write_json(
        settings,
        claude_hook(
            identity="P/claude",
            uri="ws://localhost:8876",
            ready_timeout=3.0,
            token_file=None,
            synapse_bin=THIS_SYNAPSE,
        ),
    )


@pytest.fixture
def layout(tmp_path: Path) -> tuple[Path, Path]:
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    return home, project


def test_an_empty_home_has_nothing_to_report(layout: tuple[Path, Path]) -> None:
    home, project = layout
    checks = _checks(home, project)
    assert checks["mcp-host-sources"].status == "pass"
    assert "no Synapse-named MCP server in 4 inspected source(s)" in (
        checks["mcp-host-sources"].detail
    )
    assert checks["mcp-claim-guard"].detail == "no host has a Synapse MCP server configured"


def test_a_genuine_server_without_a_guard_is_flagged_until_the_guard_exists(
    layout: tuple[Path, Path],
) -> None:
    home, project = layout
    _write_json(
        home / ".claude.json",
        {
            "mcpServers": {
                "synapse": {"type": "stdio", "command": THIS_SYNAPSE, "args": ["mcp"]},
                "playwright": {"command": "npx", "args": ["x"]},
            }
        },
    )
    unguarded = _checks(home, project)
    assert unguarded["mcp-host-sources"].status == "pass"
    assert "1 Synapse MCP definition(s) launch this installation" in (
        unguarded["mcp-host-sources"].detail
    )
    guard = unguarded["mcp-claim-guard"]
    assert guard.status == "warn"
    assert "Claude Code (not-configured" in guard.detail
    assert "synapse claude-plugin install" in guard.remedy

    _claude_guard(project / ".claude" / "settings.local.json")
    guarded = _checks(home, project)
    assert guarded["mcp-claim-guard"].status == "pass"
    assert guarded["mcp-claim-guard"].detail == "claim guard configured for Claude Code"


def test_impostor_shaped_definitions_are_named_with_their_source(
    tmp_path: Path, layout: tuple[Path, Path]
) -> None:
    home, project = layout
    bin_dir = tmp_path / "bin"
    impostor = _executable(bin_dir, "evil")
    other_synapse = _executable(bin_dir / "other", "synapse")
    _write_json(
        home / ".claude.json",
        {
            "projects": {
                str(project): {
                    "mcpServers": {
                        "synapse": {"command": str(impostor), "args": ["mcp"]},
                        "synapse-helper": {"command": "evil", "args": ["serve"]},
                        "synapse-missing": {"command": "no-such-synapse-command"},
                        "synapse-empty": {},
                    }
                }
            }
        },
    )
    _write_json(
        project / ".mcp.json",
        {"mcpServers": {"synapse": {"command": str(other_synapse), "args": ["mcp"]}}},
    )
    checks = _checks(home, project, which=lambda name: shutil.which(name, path=str(bin_dir)))
    sources = checks["mcp-host-sources"]
    assert sources.status == "warn"
    detail = sources.detail
    assert f"Claude Code local 'synapse' ({home / '.claude.json'}): launches" in detail
    assert f"{impostor.resolve()}, not synapse" in detail
    assert "'synapse-helper'" in detail and "not `synapse mcp`" in detail
    assert "command 'no-such-synapse-command' does not resolve on PATH" in detail
    assert "'synapse-empty'" in detail and "defines no command" in detail
    assert f"Claude Code project 'synapse' ({project / '.mcp.json'})" in detail
    assert f"a different synapse executable at {other_synapse.resolve()}" in detail
    assert "claim granted" in sources.remedy
    assert checks["mcp-claim-guard"].status == "warn"


def test_a_real_plugin_install_is_genuine_and_guarded_and_a_tampered_one_is_not(
    layout: tuple[Path, Path],
) -> None:
    home, project = layout
    config_root = home / ".claude"
    installed = apply_plugin(
        "install",
        config_root=config_root,
        identity="P/claude",
        uri="ws://localhost:8876",
        synapse_bin=THIS_SYNAPSE,
    )
    assert installed.state == "owned"
    owned = _checks(home, project)
    assert owned["mcp-host-sources"].status == "pass"
    assert owned["mcp-claim-guard"].status == "pass"

    (plugin_path(config_root) / "README.md").write_text("changed\n", encoding="utf-8")
    tampered = _checks(home, project)
    assert tampered["mcp-host-sources"].status == "warn"
    assert "the Synapse plugin directory is modified" in tampered["mcp-host-sources"].detail


def test_codex_profile_and_relocated_homes_are_read(
    tmp_path: Path, layout: tuple[Path, Path]
) -> None:
    home, project = layout
    codex_home = tmp_path / "codex-home"
    claude_dir = tmp_path / "claude-dir"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text(
        f'[mcp_servers.synapse]\ncommand = "{sys.executable}"\n'
        'args = ["-m", "synapse_channel", "mcp", "--name", "P/codex"]\n'
        '[mcp_servers.synapse-remote]\nurl = "https://hub.example/mcp"\n',
        encoding="utf-8",
    )
    _write_json(
        claude_dir / ".claude.json",
        {"mcpServers": {"synapse": {"command": THIS_SYNAPSE, "args": ["mcp"]}}},
    )
    env = {"CODEX_HOME": str(codex_home), "CLAUDE_CONFIG_DIR": str(claude_dir)}
    checks = _checks(home, project, env=env)
    assert checks["mcp-host-sources"].status == "pass"
    assert checks["mcp-host-sources"].detail == (
        "2 Synapse MCP definition(s) launch this installation; "
        "1 remote endpoint(s) not checked here"
    )
    guard = checks["mcp-claim-guard"].detail
    assert "Claude Code (not-configured" in guard and "Codex (not-configured" in guard

    _claude_guard(home / ".claude" / "settings.json")
    _write_json(
        home / ".codex" / "hooks.json",
        codex_hook(
            identity="P/codex",
            uri="ws://localhost:8876",
            ready_timeout=3.0,
            token_file=None,
            synapse_bin=THIS_SYNAPSE,
        ),
    )
    guarded = _checks(home, project, env=env)
    assert guarded["mcp-claim-guard"].detail == "claim guard configured for Claude Code, Codex"


def test_unreadable_sources_are_reported_not_skipped(layout: tuple[Path, Path]) -> None:
    home, project = layout
    (home / ".claude.json").write_text("{not json", encoding="utf-8")
    (project / ".mcp.json").write_text("[1, 2]", encoding="utf-8")
    (home / ".codex").mkdir()
    (home / ".codex" / "config.toml").write_text("[broken", encoding="utf-8")
    sources = _checks(home, project)["mcp-host-sources"]
    assert sources.status == "warn"
    assert f"cannot inspect {home / '.claude.json'}" in sources.detail
    assert f"cannot inspect {project / '.mcp.json'}: not a JSON object" in sources.detail
    assert f"cannot inspect {home / '.codex' / 'config.toml'}" in sources.detail


def test_an_unparsable_guard_file_is_reported_as_the_guard_state(
    layout: tuple[Path, Path],
) -> None:
    home, project = layout
    _write_json(
        home / ".claude.json",
        {"mcpServers": {"synapse": {"command": THIS_SYNAPSE, "args": ["mcp"]}}},
    )
    (home / ".claude").mkdir()
    (home / ".claude" / "settings.json").write_text("{broken", encoding="utf-8")
    guard = _checks(home, project)["mcp-claim-guard"]
    assert guard.status == "warn"
    assert "Claude Code (invalid: cannot safely inspect" in guard.detail


def test_module_launches_and_an_unresolvable_installation(
    tmp_path: Path, layout: tuple[Path, Path]
) -> None:
    other_python = _executable(tmp_path / "py", "python3")

    def verdict(command: str, *args: str, binary: Path | None) -> tuple[str, str]:
        item = classify_definition(
            McpServerDefinition(
                host="codex",
                scope="profile",
                source=tmp_path / "config.toml",
                name="synapse",
                command=command,
                args=args,
                url=None,
            ),
            synapse_binary=binary,
            python_executable=Path(sys.executable),
            which=shutil.which,
        )
        return item.status, item.reason

    module = ("-m", "synapse_channel", "mcp")
    assert verdict(sys.executable, *module, binary=None)[0] == "genuine"
    assert verdict(str(other_python), *module, binary=None) == (
        "other-install",
        f"python -m synapse_channel under {other_python.resolve()}",
    )
    # Without a resolvable installation nothing is genuine; a synapse name is only "other".
    assert verdict(THIS_SYNAPSE, "mcp", binary=None)[0] == "other-install"
    home, project = layout
    _write_json(
        home / ".claude.json",
        {"mcpServers": {"synapse": {"command": THIS_SYNAPSE, "args": ["mcp"]}}},
    )
    unresolved = _checks(home, project, synapse_bin="no-such-synapse-installation")
    assert unresolved["mcp-host-sources"].status == "warn"
    assert "a different synapse executable" in unresolved["mcp-host-sources"].detail


def test_the_project_defaults_to_the_git_top_level(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    nested = repo / "a" / "b"
    nested.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    assert default_project(nested) == repo.resolve()
    outside = tmp_path / "plain"
    outside.mkdir()
    assert default_project(outside) in (outside, outside.resolve())
    no_git = tmp_path / "no-git-bin"
    no_git.mkdir()
    assert default_project(nested, search_path=str(no_git)) == nested
    broken = tmp_path / "broken-git-bin"
    _executable(broken, "git", body="#!/nonexistent/interpreter\n")
    assert default_project(nested, search_path=str(broken)) == nested


async def test_doctor_runs_both_checks_against_the_given_home(
    layout: tuple[Path, Path],
) -> None:
    home, project = layout
    _write_json(
        home / ".claude.json",
        {"mcpServers": {"synapse": {"command": "/bin/sh", "args": ["-c", "true"]}}},
    )
    _code, lines, diagnoses = await _diagnose(
        uri="ws://127.0.0.1:1",
        project="P",
        agent_id="doctor",
        token=None,
        ready_timeout=0.1,
        env={},
        mcp_home=home,
        mcp_project=project,
    )
    by_check = {diagnosis.check: diagnosis for diagnosis in diagnoses}
    assert by_check["mcp-host-sources"].status == "warn"
    assert by_check["mcp-claim-guard"].status == "warn"
    assert any("mcp-host-sources" in line for line in lines)


def test_oversized_sources_and_other_projects_are_not_read_as_this_project(
    tmp_path: Path, layout: tuple[Path, Path]
) -> None:
    home, project = layout
    _write_json(
        home / ".claude.json",
        {
            "projects": {
                str(tmp_path / "another"): {
                    "mcpServers": {"synapse": {"command": "/bin/sh", "args": ["x"]}}
                }
            }
        },
    )
    relocated = tmp_path / "claude-dir"
    relocated.mkdir()
    with (relocated / ".claude.json").open("wb") as handle:
        handle.truncate(17 * 1_048_576)
    sources = _checks(home, project, env={"CLAUDE_CONFIG_DIR": str(relocated)})["mcp-host-sources"]
    assert sources.status == "warn"
    assert f"cannot inspect {relocated / '.claude.json'}: larger than" in sources.detail
    assert "'synapse'" not in sources.detail
