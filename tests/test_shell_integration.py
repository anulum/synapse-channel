# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for automatic shell integration

from __future__ import annotations

import os
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


def _clean_shell_environment() -> dict[str, str]:
    """Remove host Synapse and shell startup state from real-shell tests."""
    return {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("SYN") and key not in {"BASH_ENV", "ENV"}
    }


def test_render_shell_hook_auto_arms_and_wraps_default_providers() -> None:
    hook = render_shell_hook(shell="bash")
    assert 'synapse arm --name="$identity-rx" --for="$identity" --directed-only' in hook
    # the waiter is leashed to the arming shell so it cannot outlive the terminal
    assert "--owner-pid $$" in hook
    assert 'synapse worker-session --project="$SYN_PROJECT"' in hook
    assert "SYNAPSE_AUTO_CONNECT=0 synapse worker-session" in hook
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


def test_shell_hooks_prefer_private_cache_over_shared_tmp() -> None:
    """SCH-H-NEW-09: no world-shared /tmp/synapse-shell when XDG runtime is absent."""
    for shell in ("bash", "zsh", "fish"):
        hook = render_shell_hook(shell=shell, provider_commands=())
        assert '/tmp/synapse-shell"' not in hook
        assert "/tmp/synapse-shell'" not in hook
        assert 'set runtime "/tmp/synapse-shell"' not in hook
        assert "XDG_CACHE_HOME" in hook or "XDG_CACHE" in hook
        assert "mkdir -p -m 700" in hook or "mkdir -p -m 700" in hook.replace("  ", " ")
        # uid-keyed last resort only
        if shell == "fish":
            assert 'set runtime "/tmp/synapse-shell-"(id -u)' in hook
        else:
            assert "synapse-shell-$(id -u)" in hook


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
        env=_clean_shell_environment(),
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    # The waker is live, so no passive arm was recorded and no shell pidfile written.
    assert not record.exists() or record.read_text(encoding="utf-8").strip() == ""
    assert not (run_dir / "synapse-shell" / f"{key}.pid").exists()


def test_bash_auto_arm_skips_arming_when_provider_auto_connect_is_disabled(
    tmp_path: Path,
) -> None:
    bindir = _write_fake_synapse(tmp_path)
    run_dir = tmp_path / "run"
    record = tmp_path / "record"
    hook_path = tmp_path / "hook.sh"
    hook_path.write_text(render_shell_hook(shell="bash", provider_commands=()), encoding="utf-8")

    script = (
        f"export PATH={shlex.quote(str(bindir))}:$PATH\n"
        f"export XDG_RUNTIME_DIR={shlex.quote(str(run_dir))}\n"
        f"export SYNAPSE_RECORD={shlex.quote(str(record))}\n"
        "export SYN_PROJECT=user SYN_IDENTITY=user/terminal-fixed\n"
        "export SYNAPSE_AUTO_CONNECT=0\n"
        f"source {shlex.quote(str(hook_path))}\n"
        "__synapse_auto_arm\n"
    )
    proc = subprocess.run(
        ["bash", "--noprofile", "--norc", "-c", script],
        text=True,
        capture_output=True,
        env=_clean_shell_environment(),
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    assert not record.exists() or record.read_text(encoding="utf-8").strip() == ""
    assert not (run_dir / "synapse-shell" / "user_terminal-fixed.pid").exists()


def test_bash_hook_yields_to_an_active_provider_tmux_waker() -> None:
    # The prompt auto-arm must not arm a passive waiter on <identity>-rx when a
    # worker-session tmux waker already owns it, or the injecting waker is locked out.
    hook = render_shell_hook(shell="bash")
    assert "synapse-provider-tmux" in hook
    assert 'provider_pidfile="$provider_runtime/$key.pid"' in hook
    assert "__synapse_release_waiter() {" in hook
    # The provider wrapper releases the passive waiter before worker-session.
    release_index = hook.index("__synapse_release_waiter || true")
    worker_index = hook.index('synapse worker-session --project="$SYN_PROJECT"')
    assert release_index < worker_index


def test_fish_hook_yields_to_an_active_provider_tmux_waker() -> None:
    hook = render_shell_hook(shell="fish", provider_commands=("kimi",))
    assert "synapse-provider-tmux" in hook
    assert "env SYNAPSE_AUTO_CONNECT=0 synapse worker-session" in hook
    assert "function __synapse_release_waiter" in hook
    assert "__synapse_release_waiter >/dev/null 2>&1; or true" in hook
    release_index = hook.index("__synapse_release_waiter >/dev/null 2>&1; or true")
    worker_index = hook.index('synapse worker-session --project="$SYN_PROJECT"')
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
    assert 'synapse worker-session --project="$SYN_PROJECT"' in hook
    assert "disown $last_pid" in hook
    # the waiter is leashed to the arming fish so it cannot outlive the terminal
    assert "--owner-pid $fish_pid" in hook


def test_shell_hook_repairs_and_guards_a_precreated_runtime_dir() -> None:
    """F6b: ``mkdir -m 700`` is not enough — a pre-existing runtime dir is repaired
    and a foreign-owned or symlinked one is refused, in every generated dialect."""
    bash = render_shell_hook(shell="bash", provider_commands=())
    zsh = render_shell_hook(shell="zsh", provider_commands=())
    for posix in (bash, zsh):
        assert 'chmod 700 "$runtime" 2>/dev/null' in posix
        assert '[ ! -d "$runtime" ] || [ -L "$runtime" ] || [ ! -O "$runtime" ]' in posix
    fish = render_shell_hook(shell="fish", provider_commands=())
    assert 'chmod 700 "$runtime" 2>/dev/null' in fish
    assert 'not test -d "$runtime"; or test -L "$runtime"; or not test -O "$runtime"' in fish


def test_shell_hook_verifies_the_waiter_pid_before_signalling() -> None:
    """F6b: the release path must confirm the PID is this identity's synapse arm
    waiter (via its argv) before ``kill`` — never signal a planted stranger PID."""
    bash = render_shell_hook(shell="bash", provider_commands=())
    assert "cmdline=\"$(tr '\\0' ' ' < \"/proc/$pid/cmdline\" 2>/dev/null)\"" in bash
    assert 'cmdline="$(ps -o args= -p "$pid" 2>/dev/null || true)"' in bash
    assert '*" arm "*"--name=$SYN_IDENTITY-rx"*) kill "$pid" 2>/dev/null ;;' in bash
    fish = render_shell_hook(shell="fish", provider_commands=())
    assert "set cmdline (tr '\\0' ' ' < \"/proc/$pid/cmdline\" 2>/dev/null)" in fish
    assert 'string match -q -- "* arm *--name=$SYN_IDENTITY-rx*" "$cmdline"' in fish


def _run_bash(script: str) -> subprocess.CompletedProcess[str]:
    """Run ``script`` under a hermetic bash, returning the completed process."""
    return subprocess.run(
        ["bash", "--noprofile", "--norc", "-c", script],
        text=True,
        capture_output=True,
        env=_clean_shell_environment(),
        check=False,
    )


def test_bash_auto_arm_repairs_a_precreated_world_writable_runtime(tmp_path: Path) -> None:
    # A precreated 0777 runtime directory (the mode -m 700 silently leaves alone)
    # must be re-tightened to 0700 before the hook writes its pidfile into it, and
    # arming must still proceed for a directory the operator owns.
    bindir = _write_fake_synapse(tmp_path)
    run_dir = tmp_path / "run"
    shell_dir = run_dir / "synapse-shell"
    shell_dir.mkdir(parents=True)
    shell_dir.chmod(0o777)
    record = tmp_path / "record"
    hook_path = tmp_path / "hook.sh"
    hook_path.write_text(render_shell_hook(shell="bash", provider_commands=()), encoding="utf-8")

    script = "\n".join(
        [
            f"export PATH={shlex.quote(str(bindir))}:$PATH",
            f"export XDG_RUNTIME_DIR={shlex.quote(str(run_dir))}",
            f"export SYNAPSE_RECORD={shlex.quote(str(record))}",
            "export SYN_PROJECT=user SYN_IDENTITY=user/terminal-fixed",
            f"source {shlex.quote(str(hook_path))}",
            "__synapse_auto_arm",
            "for _ in $(seq 1 100); do "
            f"[ -s {shlex.quote(str(record))} ] && break; sleep 0.02; done",
        ]
    )
    proc = _run_bash(script)

    assert proc.returncode == 0, proc.stderr
    assert (shell_dir.stat().st_mode & 0o777) == 0o700
    assert "arm --name=user/terminal-fixed-rx" in record.read_text(encoding="utf-8")


def test_bash_auto_arm_refuses_a_symlinked_runtime(tmp_path: Path) -> None:
    # A symlinked runtime directory (an attacker aiming the hook at a directory it
    # does not own) is refused outright: no arm is recorded and nothing is written
    # through the link into the target.
    bindir = _write_fake_synapse(tmp_path)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    target = tmp_path / "elsewhere"
    target.mkdir()
    (run_dir / "synapse-shell").symlink_to(target, target_is_directory=True)
    record = tmp_path / "record"
    hook_path = tmp_path / "hook.sh"
    hook_path.write_text(render_shell_hook(shell="bash", provider_commands=()), encoding="utf-8")

    script = "\n".join(
        [
            f"export PATH={shlex.quote(str(bindir))}:$PATH",
            f"export XDG_RUNTIME_DIR={shlex.quote(str(run_dir))}",
            f"export SYNAPSE_RECORD={shlex.quote(str(record))}",
            "export SYN_PROJECT=user SYN_IDENTITY=user/terminal-fixed",
            f"source {shlex.quote(str(hook_path))}",
            "__synapse_auto_arm",
            "sleep 0.1",
        ]
    )
    proc = _run_bash(script)

    assert proc.returncode == 0, proc.stderr
    assert not record.exists() or record.read_text(encoding="utf-8").strip() == ""
    assert list(target.iterdir()) == []


def test_bash_release_waiter_refuses_to_kill_a_non_waiter_pid(tmp_path: Path) -> None:
    # A pidfile planted with a stranger PID must never turn the release into a blind
    # kill: the process survives because its argv is not this identity's arm waiter.
    run_dir = tmp_path / "run"
    (run_dir / "synapse-shell").mkdir(parents=True)
    (run_dir / "synapse-shell").chmod(0o700)
    pidfile = run_dir / "synapse-shell" / "user_terminal-fixed.pid"
    outcome = tmp_path / "outcome"
    hook_path = tmp_path / "hook.sh"
    hook_path.write_text(render_shell_hook(shell="bash", provider_commands=()), encoding="utf-8")

    script = "\n".join(
        [
            f"export XDG_RUNTIME_DIR={shlex.quote(str(run_dir))}",
            "export SYN_PROJECT=user SYN_IDENTITY=user/terminal-fixed",
            "sleep 30 &",
            "stranger=$!",
            f"printf '%s' \"$stranger\" > {shlex.quote(str(pidfile))}",
            f"source {shlex.quote(str(hook_path))}",
            "__synapse_release_waiter",
            "sleep 0.2",
            f'if kill -0 "$stranger" 2>/dev/null; then echo alive > {shlex.quote(str(outcome))};'
            f" else echo killed > {shlex.quote(str(outcome))}; fi",
            'kill "$stranger" 2>/dev/null',
            'wait "$stranger" 2>/dev/null',
            "exit 0",
        ]
    )
    proc = _run_bash(script)

    assert proc.returncode == 0, proc.stderr
    assert outcome.read_text(encoding="utf-8").strip() == "alive"


def test_bash_release_waiter_kills_the_verified_waiter(tmp_path: Path) -> None:
    # The legitimate path is preserved: a process whose argv is this identity's
    # synapse arm waiter is signalled and stopped when the provider wrapper releases it.
    run_dir = tmp_path / "run"
    (run_dir / "synapse-shell").mkdir(parents=True)
    (run_dir / "synapse-shell").chmod(0o700)
    pidfile = run_dir / "synapse-shell" / "user_terminal-fixed.pid"
    outcome = tmp_path / "outcome"
    hook_path = tmp_path / "hook.sh"
    hook_path.write_text(render_shell_hook(shell="bash", provider_commands=()), encoding="utf-8")

    waiter_argv = (
        "synapse arm --name=user/terminal-fixed-rx --for=user/terminal-fixed "
        "--directed-only --owner-pid 1"
    )
    script = "\n".join(
        [
            f"export XDG_RUNTIME_DIR={shlex.quote(str(run_dir))}",
            "export SYN_PROJECT=user SYN_IDENTITY=user/terminal-fixed",
            f"( exec -a {shlex.quote(waiter_argv)} sleep 30 ) &",
            "waiter=$!",
            f"printf '%s' \"$waiter\" > {shlex.quote(str(pidfile))}",
            "sleep 0.2",
            f"source {shlex.quote(str(hook_path))}",
            "__synapse_release_waiter",
            "sleep 0.3",
            f'if kill -0 "$waiter" 2>/dev/null; then echo alive > {shlex.quote(str(outcome))};'
            f" else echo killed > {shlex.quote(str(outcome))}; fi",
            'kill "$waiter" 2>/dev/null',
            'wait "$waiter" 2>/dev/null',
            "exit 0",
        ]
    )
    proc = _run_bash(script)

    assert proc.returncode == 0, proc.stderr
    assert outcome.read_text(encoding="utf-8").strip() == "killed"


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
            # a developer terminal already inside the shell hook exports these;
            # a pinned SYN_IDENTITY would override the marker/neutral resolution
            "unset SYN_PROJECT SYN_IDENTITY __SYNAPSE_AUTO_PROJECT __SYNAPSE_AUTO_IDENTITY; "
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
        env=_clean_shell_environment(),
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    text = record.read_text(encoding="utf-8")
    assert "arm --name=user/terminal-" in text
    assert "--for=user/terminal-" in text
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
            # a developer terminal already inside the shell hook exports these;
            # a pinned SYN_IDENTITY would override the marker/neutral resolution
            "unset SYN_PROJECT SYN_IDENTITY __SYNAPSE_AUTO_PROJECT __SYNAPSE_AUTO_IDENTITY; "
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
        env=_clean_shell_environment(),
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    text = record.read_text(encoding="utf-8")
    assert "arm --name=SYNAPSE-CHANNEL/terminal-" in text
    assert "--for=SYNAPSE-CHANNEL" in text


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


def test_cmd_shell_hook_rejects_provider_function_injection(capsys: Any) -> None:
    ns = cli.build_parser().parse_args(["shell-hook", "--provider", "x; touch /tmp/injected #"])

    assert cli_shell._cmd_shell_hook(ns) == 2

    out = capsys.readouterr().out
    assert "provider command must be a bare name" in out
    assert "touch /tmp/injected" not in out


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


def test_render_shell_hook_rejects_an_unsupported_shell() -> None:
    with pytest.raises(ValueError, match="supports bash, fish, and zsh"):
        render_shell_hook(shell="powershell")


# --- delivery-integrity DEL-INT-C: distinct session identities by default ----
#
# The 2026-07-16 incident shape: environment layering (tmux server env,
# systemd user env) carried another session's auto identity into new shells
# while the mint-guard variable diverged, so the hook read it as a manual
# identity and every seat on the workstation coordinated as one shared
# user/terminal-<id>. A default-shape auto identity whose pid is outside this
# shell's lineage must be re-minted; manual, provider, and own-lineage
# identities stay untouched.


def _hook_script(tmp_path: Path, hook_path: Path, *exports: str) -> str:
    """Compose a hermetic bash script that sources the hook and reports identity."""
    bindir = _write_fake_synapse(tmp_path)
    lines = [
        f"export PATH={shlex.quote(str(bindir))}:$PATH",
        f"export XDG_RUNTIME_DIR={shlex.quote(str(tmp_path / 'run'))}",
        f"export SYNAPSE_RECORD={shlex.quote(str(tmp_path / 'record'))}",
        *exports,
        f"source {shlex.quote(str(hook_path))}",
        "__synapse_auto_arm",
        'printf "%s %s\\n" "$$" "$SYN_IDENTITY"',
    ]
    return "\n".join(lines) + "\n"


def test_bash_hook_remints_a_foreign_auto_identity(tmp_path: Path) -> None:
    hook_path = tmp_path / "hook.sh"
    hook_path.write_text(render_shell_hook(shell="bash", provider_commands=()), encoding="utf-8")
    proc = _run_bash(
        _hook_script(
            tmp_path,
            hook_path,
            "export SYN_PROJECT=user SYN_IDENTITY=user/terminal-99999999",
            "export __SYNAPSE_AUTO_PROJECT=user __SYNAPSE_AUTO_IDENTITY=user/terminal-88888888",
        )
    )
    assert proc.returncode == 0, proc.stderr
    shell_pid, identity = proc.stdout.strip().split()
    assert identity == f"user/terminal-{shell_pid}"
    assert "re-minting" in proc.stderr


def test_bash_hook_remints_when_the_foreign_terminal_id_is_also_inherited(
    tmp_path: Path,
) -> None:
    # The foreign identity usually rides in with the exported numeric terminal
    # id that minted it; trusting that id would re-create the same shared name.
    hook_path = tmp_path / "hook.sh"
    hook_path.write_text(render_shell_hook(shell="bash", provider_commands=()), encoding="utf-8")
    proc = _run_bash(
        _hook_script(
            tmp_path,
            hook_path,
            "export SYN_PROJECT=user SYN_IDENTITY=user/terminal-99999999",
            "export __SYNAPSE_AUTO_IDENTITY=user/terminal-88888888",
            "export SYNAPSE_TERMINAL_ID=99999999",
        )
    )
    assert proc.returncode == 0, proc.stderr
    shell_pid, identity = proc.stdout.strip().split()
    assert identity == f"user/terminal-{shell_pid}"


def test_bash_hook_keeps_a_manual_non_terminal_identity(tmp_path: Path) -> None:
    hook_path = tmp_path / "hook.sh"
    hook_path.write_text(render_shell_hook(shell="bash", provider_commands=()), encoding="utf-8")
    proc = _run_bash(
        _hook_script(
            tmp_path,
            hook_path,
            "export SYN_PROJECT=SYNAPSE-CHANNEL SYN_IDENTITY=SYNAPSE-CHANNEL/claude-a7c2",
            "export __SYNAPSE_AUTO_IDENTITY=user/terminal-88888888",
        )
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip().split()[1] == "SYNAPSE-CHANNEL/claude-a7c2"
    assert "re-minting" not in proc.stderr


def test_bash_hook_keeps_an_auto_identity_minted_by_a_live_ancestor(tmp_path: Path) -> None:
    # A nested shell under the minting shell shares its seat deliberately:
    # the embedded pid IS an ancestor, so the identity is not foreign.
    bindir = _write_fake_synapse(tmp_path)
    hook_path = tmp_path / "hook.sh"
    hook_path.write_text(render_shell_hook(shell="bash", provider_commands=()), encoding="utf-8")
    inner = (
        f'source {shlex.quote(str(hook_path))}; __synapse_auto_arm; printf "%s\\n" "$SYN_IDENTITY"'
    )
    script = (
        f"export PATH={shlex.quote(str(bindir))}:$PATH\n"
        f"export XDG_RUNTIME_DIR={shlex.quote(str(tmp_path / 'run'))}\n"
        f"export SYNAPSE_RECORD={shlex.quote(str(tmp_path / 'record'))}\n"
        'export SYN_PROJECT=user SYN_IDENTITY="user/terminal-$$"\n'
        "export __SYNAPSE_AUTO_IDENTITY=user/terminal-88888888\n"
        'printf "%s\\n" "$$"\n'
        f"bash --noprofile --norc -c {shlex.quote(inner)}\n"
    )
    proc = _run_bash(script)
    assert proc.returncode == 0, proc.stderr
    outer_pid, identity = proc.stdout.strip().splitlines()
    assert identity == f"user/terminal-{outer_pid}"
    assert "re-minting" not in proc.stderr


def test_bash_hook_provider_session_keeps_a_handed_down_identity(tmp_path: Path) -> None:
    hook_path = tmp_path / "hook.sh"
    hook_path.write_text(render_shell_hook(shell="bash", provider_commands=()), encoding="utf-8")
    proc = _run_bash(
        _hook_script(
            tmp_path,
            hook_path,
            "export SYN_PROJECT=user SYN_IDENTITY=user/terminal-99999999",
            "export __SYNAPSE_AUTO_IDENTITY=user/terminal-88888888",
            "export SYN_TMUX_PROVIDER=1",
        )
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip().split()[1] == "user/terminal-99999999"
    assert "re-minting" not in proc.stderr


def test_fish_hook_carries_the_foreign_auto_identity_guard() -> None:
    fish = render_shell_hook(shell="fish", provider_commands=())
    assert "__synapse_identity_is_foreign_auto" in fish
    assert "__synapse_pid_in_session_lineage" in fish
    assert "re-minting" in fish
