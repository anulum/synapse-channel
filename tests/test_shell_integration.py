# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for automatic shell integration

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

from synapse_channel import cli, cli_shell
from synapse_channel.shell_integration import (
    END_MARKER,
    START_MARKER,
    install_shell_hook,
    render_rc_block,
    render_shell_hook,
    shell_rc_path,
)


def test_render_shell_hook_auto_arms_and_wraps_default_providers() -> None:
    hook = render_shell_hook(shell="bash")
    assert 'synapse arm --name "$identity-rx" --for "$project" --directed-only' in hook
    assert 'synapse worker-session --project "$SYN_PROJECT"' in hook
    assert "codex()" in hook
    assert "claude()" in hook
    assert "gemini()" in hook
    assert "agent()" in hook
    assert "ask()" in hook
    assert "ollama()" in hook
    assert "PROMPT_COMMAND" in hook


def test_render_shell_hook_zsh_uses_precmd_hook() -> None:
    hook = render_shell_hook(shell="zsh", provider_commands=("codex",))
    assert "add-zsh-hook precmd __synapse_auto_arm" in hook
    assert "codex()" in hook
    assert "gemini()" not in hook


def test_render_shell_hook_fish_uses_prompt_event() -> None:
    hook = render_shell_hook(shell="fish", provider_commands=("codex",))
    assert "function __synapse_auto_arm --on-event fish_prompt" in hook
    assert "function codex --wraps codex" in hook
    assert 'synapse worker-session --project "$SYN_PROJECT"' in hook
    assert "disown $last_pid" in hook


def test_shell_rc_path_detects_auto_shell(tmp_path: Path) -> None:
    assert shell_rc_path("auto", home=tmp_path, env_shell="/bin/zsh") == tmp_path / ".zshrc"
    assert shell_rc_path("auto", home=tmp_path, env_shell="/bin/bash") == tmp_path / ".bashrc"
    assert (
        shell_rc_path("auto", home=tmp_path, env_shell="/usr/bin/fish")
        == tmp_path / ".config" / "fish" / "config.fish"
    )


def test_render_rc_block_loads_live_shell_hook() -> None:
    block = render_rc_block(shell="bash", synapse_bin="/opt/bin/synapse")
    assert START_MARKER in block
    assert 'eval "$(/opt/bin/synapse shell-hook --shell bash)"' in block
    assert END_MARKER in block


def test_render_rc_block_loads_fish_shell_hook() -> None:
    block = render_rc_block(shell="fish", synapse_bin="/opt/bin/synapse")
    assert START_MARKER in block
    assert "/opt/bin/synapse shell-hook --shell fish | source" in block
    assert END_MARKER in block


def test_install_shell_hook_is_idempotent(tmp_path: Path) -> None:
    bashrc = tmp_path / ".bashrc"
    bashrc.write_text("export EXISTING=1\n", encoding="utf-8")

    first = install_shell_hook(shell="bash", synapse_bin="synapse", home=tmp_path)
    second = install_shell_hook(shell="bash", synapse_bin="synapse", home=tmp_path)
    text = bashrc.read_text(encoding="utf-8")

    assert first == [f"installed shell hook in {bashrc}", "open a new terminal or source the file"]
    assert second == [f"already installed in {bashrc}"]
    assert text.count(START_MARKER) == 1
    assert "export EXISTING=1" in text


def test_parser_shell_hook_dispatches() -> None:
    args = cli.build_parser().parse_args(["shell-hook", "--shell", "fish", "--provider", "codex"])
    assert args.func is cli_shell._cmd_shell_hook
    assert args.shell == "fish"
    assert args.provider == ["codex"]


def test_parser_install_shell_hook_dispatches() -> None:
    args = cli.build_parser().parse_args(
        ["install-shell-hook", "--shell", "bash", "--synapse-bin", "/bin/synapse"]
    )
    assert args.func is cli_shell._cmd_install_shell_hook
    assert args.shell == "bash"
    assert args.synapse_bin == "/bin/synapse"


def test_cmd_shell_hook_prints_rendered_hook(capsys: Any) -> None:
    ns = cli.build_parser().parse_args(["shell-hook", "--provider", "codex"])
    assert cli_shell._cmd_shell_hook(ns) == 0
    out = capsys.readouterr().out
    assert "codex()" in out
    assert "synapse worker-session" in out


def test_cmd_install_shell_hook_reports_install(capsys: Any) -> None:
    captured: dict[str, Any] = {}

    def installer(**kwargs: Any) -> list[str]:
        captured.update(kwargs)
        return ["installed shell hook in /home/u/.bashrc"]

    ns = cli.build_parser().parse_args(["install-shell-hook", "--shell", "bash"])
    assert cli_shell._cmd_install_shell_hook(ns, installer=installer) == 0
    assert captured == {"shell": "bash", "synapse_bin": "synapse"}
    assert "installed shell hook" in capsys.readouterr().out


def test_cmd_install_shell_hook_reports_unsupported_shell(capsys: Any) -> None:
    def installer(**kwargs: Any) -> list[str]:
        raise ValueError("shell integration supports bash, fish, and zsh")

    ns = cli.build_parser().parse_args(["install-shell-hook", "--shell", "fish"])
    assert cli_shell._cmd_install_shell_hook(ns, installer=installer) == 2
    assert "bash, fish, and zsh" in capsys.readouterr().out


def test_shell_hook_cli_emits_bash_that_parses() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "synapse_channel.cli", "shell-hook", "--provider", "codex"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0
    assert "codex()" in proc.stdout

    syntax = subprocess.run(
        ["bash", "-n"],
        input=proc.stdout,
        text=True,
        capture_output=True,
        check=False,
    )
    assert syntax.returncode == 0, syntax.stderr


def test_shell_hook_cli_emits_fish_that_parses() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "synapse_channel.cli", "shell-hook", "--shell", "fish"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0
    assert "function __synapse_auto_arm --on-event fish_prompt" in proc.stdout

    syntax = subprocess.run(
        ["fish", "--no-execute", "-c", proc.stdout],
        text=True,
        capture_output=True,
        check=False,
    )
    assert syntax.returncode == 0, syntax.stderr
