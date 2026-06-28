# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for automatic shell integration

from __future__ import annotations

import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from synapse_channel import cli, cli_shell
from synapse_channel.shell_integration import (
    END_MARKER,
    START_MARKER,
    install_shell_hook,
    render_rc_block,
    render_shell_hook,
    shell_rc_path,
)


def _write_fake_synapse(tmp_path: Path) -> Path:
    """Write a fake synapse executable that records the real shell hook argv."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    synapse = bindir / "synapse"
    synapse.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$*" >> "$SYNAPSE_RECORD"\n',
        encoding="utf-8",
    )
    synapse.chmod(0o755)
    return bindir


def test_render_shell_hook_auto_arms_and_wraps_default_providers() -> None:
    hook = render_shell_hook(shell="bash")
    assert 'synapse arm --name "$identity-rx" --for "$project" --directed-only' in hook
    assert 'synapse worker-session --project "$SYN_PROJECT"' in hook
    assert "SYNAPSE_DEFAULT_PROJECT:-user" in hook
    assert ".synapse/project" in hook
    assert "SYNAPSE_AUTO_PROJECT_FROM_CWD" in hook
    assert "codex()" in hook
    assert "claude()" in hook
    assert "kimi()" in hook
    assert "grok()" in hook
    assert "gemini()" in hook
    assert "agent()" in hook
    assert "ask()" in hook
    assert "ollama()" in hook
    assert "PROMPT_COMMAND" in hook


def test_bash_auto_arm_skips_arming_when_a_provider_tmux_waker_is_live(tmp_path: Path) -> None:
    # Real bash execution: with a live worker-session tmux waker recorded in the
    # provider-tmux pidfile, __synapse_auto_arm must yield and record no arm.
    bindir = _write_fake_synapse(tmp_path)
    run_dir = tmp_path / "run"
    record = tmp_path / "record"
    hook_path = tmp_path / "hook.sh"
    hook_path.write_text(render_shell_hook(shell="bash", provider_commands=()), encoding="utf-8")
    identity = "user/terminal-fixed"
    key = "user_terminal-fixed"
    provider_dir = run_dir / "synapse-provider-tmux"
    provider_dir.mkdir(parents=True)

    script = (
        f"export PATH={shlex.quote(str(bindir))}:$PATH\n"
        f"export XDG_RUNTIME_DIR={shlex.quote(str(run_dir))}\n"
        f"export SYNAPSE_RECORD={shlex.quote(str(record))}\n"
        f"export SYN_PROJECT=user SYN_IDENTITY={identity}\n"
        "sleep 30 &\n"
        f"printf '%s' \"$!\" > {shlex.quote(str(provider_dir / (key + '.pid')))}\n"
        f"source {shlex.quote(str(hook_path))}\n"
        "__synapse_auto_arm\n"
    )
    proc = subprocess.run(
        ["bash", "--noprofile", "--norc", "-c", script],
        text=True,
        capture_output=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    # The waker is live, so no passive arm was recorded and no shell pidfile written.
    assert not record.exists() or record.read_text(encoding="utf-8").strip() == ""
    assert not (run_dir / "synapse-shell" / f"{key}.pid").exists()


def test_bash_hook_yields_to_an_active_provider_tmux_waker() -> None:
    # The prompt auto-arm must not arm a passive waiter on <identity>-rx when a
    # worker-session tmux waker already owns it, or the injecting waker is locked out.
    hook = render_shell_hook(shell="bash")
    assert "synapse-provider-tmux/$key.pid" in hook
    assert "__synapse_release_waiter() {" in hook
    # The provider wrapper releases the passive waiter before worker-session.
    release_index = hook.index("__synapse_release_waiter || true")
    worker_index = hook.index('synapse worker-session --project "$SYN_PROJECT"')
    assert release_index < worker_index


def test_fish_hook_yields_to_an_active_provider_tmux_waker() -> None:
    hook = render_shell_hook(shell="fish", provider_commands=("kimi",))
    assert "synapse-provider-tmux" in hook
    assert "function __synapse_release_waiter" in hook
    assert "__synapse_release_waiter >/dev/null 2>&1; or true" in hook
    release_index = hook.index("__synapse_release_waiter >/dev/null 2>&1; or true")
    worker_index = hook.index('synapse worker-session --project "$SYN_PROJECT"')
    assert release_index < worker_index


def test_render_shell_hook_zsh_uses_precmd_hook() -> None:
    hook = render_shell_hook(shell="zsh", provider_commands=("codex",))
    assert "add-zsh-hook precmd __synapse_auto_arm" in hook
    assert "codex()" in hook
    assert "gemini()" not in hook


def test_render_shell_hook_fish_uses_prompt_event() -> None:
    hook = render_shell_hook(shell="fish", provider_commands=("codex",))
    assert "function __synapse_auto_arm --on-event fish_prompt" in hook
    assert ".synapse/project" in hook
    assert "SYNAPSE_AUTO_PROJECT_FROM_CWD" in hook
    assert "function codex --wraps codex" in hook
    assert 'synapse worker-session --project "$SYN_PROJECT"' in hook
    assert "disown $last_pid" in hook


def test_bash_shell_hook_uses_neutral_project_without_marker(tmp_path: Path) -> None:
    bindir = _write_fake_synapse(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, stdout=subprocess.DEVNULL, check=True)
    hook_path = tmp_path / "hook.sh"
    record = tmp_path / "record"
    hook_path.write_text(render_shell_hook(shell="bash", provider_commands=()), encoding="utf-8")

    proc = subprocess.run(
        [
            "bash",
            "--noprofile",
            "--norc",
            "-c",
            "export PATH="
            + shlex.quote(str(bindir))
            + ":$PATH; export XDG_RUNTIME_DIR="
            + shlex.quote(str(tmp_path / "run"))
            + "; export SYNAPSE_RECORD="
            + shlex.quote(str(record))
            + "; cd "
            + shlex.quote(str(repo))
            + "; source "
            + shlex.quote(str(hook_path))
            + "; for _ in {1..100}; do [ -s "
            + shlex.quote(str(record))
            + " ] && break; sleep 0.02; done",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    text = record.read_text(encoding="utf-8")
    assert "arm --name user/terminal-" in text
    assert "--for user" in text
    assert "repo" not in text


def test_bash_shell_hook_uses_marker_project_when_opted_in(tmp_path: Path) -> None:
    bindir = _write_fake_synapse(tmp_path)
    repo = tmp_path / "repo"
    marker_dir = repo / ".synapse"
    marker_dir.mkdir(parents=True)
    (marker_dir / "project").write_text("SYNAPSE-CHANNEL\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=repo, stdout=subprocess.DEVNULL, check=True)
    hook_path = tmp_path / "hook.sh"
    record = tmp_path / "record"
    hook_path.write_text(render_shell_hook(shell="bash", provider_commands=()), encoding="utf-8")

    proc = subprocess.run(
        [
            "bash",
            "--noprofile",
            "--norc",
            "-c",
            "export PATH="
            + shlex.quote(str(bindir))
            + ":$PATH; export XDG_RUNTIME_DIR="
            + shlex.quote(str(tmp_path / "run"))
            + "; export SYNAPSE_RECORD="
            + shlex.quote(str(record))
            + "; cd "
            + shlex.quote(str(repo))
            + "; source "
            + shlex.quote(str(hook_path))
            + "; for _ in {1..100}; do [ -s "
            + shlex.quote(str(record))
            + " ] && break; sleep 0.02; done",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    text = record.read_text(encoding="utf-8")
    assert "arm --name SYNAPSE-CHANNEL/terminal-" in text
    assert "--for SYNAPSE-CHANNEL" in text


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

    fish = shutil.which("fish")
    if fish is None:
        pytest.skip("fish shell is not installed")

    syntax = subprocess.run(
        [fish, "--no-execute", "-c", proc.stdout],
        text=True,
        capture_output=True,
        check=False,
    )
    assert syntax.returncode == 0, syntax.stderr
