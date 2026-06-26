# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — provider-neutral worker-session launcher
"""Run a provider command with a cheap Synapse wake sidecar."""

from __future__ import annotations

import os
import shlex

# Provider, tmux, and synapse subprocesses are this module's controlled boundary.
import subprocess  # nosec B404
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from tempfile import gettempdir

from synapse_channel.client.agent import DEFAULT_HUB_URI
from synapse_channel.codex_tmux import CodexTmuxConfig, start_session

SIDECAR_SHUTDOWN_TIMEOUT_SECONDS = 5.0
"""Seconds to wait for a wake sidecar to exit after graceful termination."""

INTERACTIVE_TMUX_PROVIDERS = frozenset({"codex", "claude", "kimi", "grok"})
"""Provider command names that default to persistent tmux-backed terminals."""

TERMINAL_TMUX_MODES = frozenset({"auto", "on", "off"})
"""Supported tmux autostart policy values."""


def _project_from_identity(identity: str) -> str:
    """Return the project segment of a ``project`` or ``project/worker`` identity."""
    return identity.split("/", 1)[0].strip()


def _safe_key(identity: str) -> str:
    """Return the filesystem-safe key for ``identity``."""
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in identity)


def _sidecar_log_path(identity: str, env: Mapping[str, str]) -> Path:
    """Return the quiet sidecar log path for a worker-session identity."""
    runtime = Path(env.get("XDG_RUNTIME_DIR") or gettempdir()) / "synapse-worker-session"
    runtime.mkdir(parents=True, exist_ok=True)
    return runtime / f"{_safe_key(identity)}.log"


def _terminal_tmux_runtime_dir(env: Mapping[str, str]) -> Path:
    """Return the runtime directory for tmux provider waiters."""
    runtime = Path(env.get("XDG_RUNTIME_DIR") or gettempdir()) / "synapse-provider-tmux"
    runtime.mkdir(parents=True, exist_ok=True)
    return runtime


def _default_tmux_session(identity: str) -> str:
    """Return the default tmux session name for a provider identity."""
    return f"synapse-{_safe_key(identity)}"


def _provider_command_name(command: Sequence[str]) -> str:
    """Return the executable basename for a provider command."""
    if not command:
        return ""
    return Path(command[0]).name


def _terminal_tmux_enabled(
    *,
    command: Sequence[str],
    mode: str,
    env: Mapping[str, str],
) -> bool:
    """Return whether ``command`` should launch through persistent tmux."""
    if mode not in TERMINAL_TMUX_MODES:
        raise ValueError("terminal_tmux must be one of: auto, on, off")
    if mode == "off" or env.get("SYNAPSE_PROVIDER_TMUX") == "0":
        return False
    if _provider_command_name(command) not in INTERACTIVE_TMUX_PROVIDERS:
        return False
    if mode == "on":
        return True
    return sys.stdin.isatty() and sys.stdout.isatty()


def _waiter_paths(identity: str, env: Mapping[str, str]) -> tuple[Path, Path]:
    """Return pidfile and logfile paths for a persistent tmux waiter."""
    runtime = _terminal_tmux_runtime_dir(env)
    safe = _safe_key(identity)
    return runtime / f"{safe}.pid", runtime / f"{safe}.log"


def _pid_is_alive(pidfile: Path) -> bool:
    """Return whether ``pidfile`` points at a live process."""
    try:
        pid_text = pidfile.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return False
    if not pid_text:
        return False
    try:
        os.kill(int(pid_text), 0)
    except (OSError, ValueError):
        return False
    return True


def _start_tmux_waiter(
    *,
    identity: str,
    session: str,
    cwd: Path,
    command: Sequence[str],
    synapse_bin: str,
    uri: str,
    token: str | None,
    env: Mapping[str, str],
) -> None:
    """Start the persistent directed waiter for a provider tmux session."""
    pidfile, logfile = _waiter_paths(identity, env)
    if _pid_is_alive(pidfile):
        return

    wait_command = [
        synapse_bin,
        "codex-tmux",
        "wait",
        "--identity",
        identity,
        "--session",
        session,
        "--cwd",
        str(cwd),
        "--codex-command",
        shlex.join(command),
    ]
    if token:
        wait_command.extend(["--token", token])
    if uri != DEFAULT_HUB_URI:
        wait_command.extend(["--uri", uri])

    with logfile.open("ab") as log_handle:
        waiter = subprocess.Popen(  # nosec B603
            wait_command,
            env=dict(env),
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    pidfile.write_text(f"{waiter.pid}\n", encoding="utf-8")


def _attach_tmux_session(
    *,
    session: str,
    tmux_bin: str,
    env: Mapping[str, str],
) -> int:
    """Attach the current terminal to ``session``."""
    command = [tmux_bin, "switch-client" if env.get("TMUX") else "attach-session", "-t", session]
    return subprocess.run(  # nosec B603
        command, env=dict(env), check=False
    ).returncode


def _run_terminal_tmux_session(
    *,
    identity: str,
    command: Sequence[str],
    env: Mapping[str, str],
    uri: str,
    token: str | None,
    tmux_bin: str,
    synapse_bin: str,
    session: str | None,
    cwd: Path,
) -> int:
    """Run an interactive provider through a persistent tmux-backed terminal."""
    resolved_session = session or _default_tmux_session(identity)
    config = CodexTmuxConfig(
        identity=identity,
        session=resolved_session,
        cwd=cwd,
        codex_command=tuple(command),
        tmux_bin=tmux_bin,
        synapse_bin=synapse_bin,
        uri=uri,
        token=token,
    )
    started = start_session(config)
    if started.returncode != 0:
        print(started.detail)
        return started.returncode
    _start_tmux_waiter(
        identity=identity,
        session=resolved_session,
        cwd=cwd,
        command=command,
        synapse_bin=synapse_bin,
        uri=uri,
        token=token,
        env=env,
    )
    return _attach_tmux_session(session=resolved_session, tmux_bin=tmux_bin, env=env)


def run_worker_session(
    *,
    identity: str,
    command: Sequence[str],
    project: str | None = None,
    uri: str = DEFAULT_HUB_URI,
    syn_bin: str = "syn",
    token: str | None = None,
    token_file: str | None = None,
    arm: bool = True,
    terminal_tmux: str = "auto",
    tmux_bin: str = "tmux",
    synapse_bin: str = "synapse",
    tmux_session: str | None = None,
    cwd: Path | None = None,
    environ: Mapping[str, str] | None = None,
    sidecar_shutdown_timeout_seconds: float = SIDECAR_SHUTDOWN_TIMEOUT_SECONDS,
) -> int:
    """Run ``command`` with ``SYN_PROJECT``/``SYN_IDENTITY`` and an optional waker.

    The sidecar is a local ``syn arm`` process. It holds a socket and prints wake
    messages, but it does not call a model provider and therefore does not spend
    tokens while waiting.
    """
    if not command:
        raise ValueError("worker-session requires a provider command")
    env = dict(os.environ if environ is None else environ)
    resolved_project = (project or _project_from_identity(identity)).strip()
    env["SYN_PROJECT"] = resolved_project
    env["SYN_IDENTITY"] = identity.strip()
    if _terminal_tmux_enabled(command=command, mode=terminal_tmux, env=env):
        return _run_terminal_tmux_session(
            identity=env["SYN_IDENTITY"],
            command=command,
            env=env,
            uri=uri,
            token=token,
            tmux_bin=tmux_bin,
            synapse_bin=synapse_bin,
            session=tmux_session,
            cwd=Path.cwd() if cwd is None else cwd,
        )

    sidecar: subprocess.Popen[bytes] | None = None
    if arm:
        arm_cmd = [syn_bin, "arm", "--uri", uri]
        if token:
            arm_cmd.extend(["--token", token])
        if token_file:
            arm_cmd.extend(["--token-file", token_file])
        sidecar_log = _sidecar_log_path(env["SYN_IDENTITY"], env)
        log_handle = sidecar_log.open("ab")
        try:
            sidecar = subprocess.Popen(  # nosec B603
                arm_cmd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        finally:
            log_handle.close()

    try:
        return subprocess.run(  # nosec B603
            list(command), env=env, check=False
        ).returncode
    finally:
        if sidecar is not None and sidecar.poll() is None:
            sidecar.terminate()
            try:
                sidecar.wait(timeout=sidecar_shutdown_timeout_seconds)
            except subprocess.TimeoutExpired:
                sidecar.kill()
                sidecar.wait()
