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
import threading
from collections.abc import Mapping, Sequence
from pathlib import Path

from synapse_channel.agent_tmux import AgentTmuxConfig, start_session
from synapse_channel.client.agent import DEFAULT_HUB_URI
from synapse_channel.core.errors import SynapseError
from synapse_channel.execution_outcome import OutcomeState
from synapse_channel.launch_record import (
    make_record,
    record_dir_for,
    write_record,
)
from synapse_channel.worker_outcome import WorkerExit, WorkerObservation

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


def _private_sidecar_runtime(env: Mapping[str, str], leaf: str) -> Path:
    """Return a private runtime dir for sidecars (SCH-H-NEW-12 / REV-SEC-10).

    Prefer ``XDG_RUNTIME_DIR``; otherwise use the same private-cache parent as
    the shell hooks (never a shared world-writable ``/tmp/synapse-*``). The leaf
    is materialised through :func:`~synapse_channel.core.private_dir.ensure_private_dir`
    so a pre-existing symlink, foreign-owned, or loose directory fails closed.
    """
    from synapse_channel.core.private_dir import ensure_private_dir
    from synapse_channel.reap import private_runtime_parent

    root = env.get("XDG_RUNTIME_DIR", "").strip()
    if root:
        runtime = Path(root) / leaf
    else:
        runtime = private_runtime_parent(env) / leaf
    return ensure_private_dir(
        runtime,
        parents=True,
        purpose=f"worker-session sidecar ({leaf})",
    )


def _sidecar_log_path(identity: str, env: Mapping[str, str]) -> Path:
    """Return the quiet sidecar log path for a worker-session identity."""
    runtime = _private_sidecar_runtime(env, "synapse-worker-session")
    return runtime / f"{_safe_key(identity)}.log"


def _terminal_tmux_runtime_dir(env: Mapping[str, str]) -> Path:
    """Return the runtime directory for tmux provider waiters."""
    return _private_sidecar_runtime(env, "synapse-provider-tmux")


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
    """Return whether ``pidfile`` points at a live non-zombie process."""
    try:
        pid_text = pidfile.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return False
    if not pid_text:
        return False
    try:
        pid = int(pid_text)
    except ValueError:
        return False
    # Shared with shell_integration: reject zombies so a defunct agent-tmux does
    # not block waiter restart (kill(pid,0) is true for zombies).
    from synapse_channel.shell_integration import pid_is_live_process

    return pid_is_live_process(pid)


def _start_tmux_waiter(
    *,
    identity: str,
    session: str,
    cwd: Path,
    command: Sequence[str],
    synapse_bin: str,
    tmux_bin: str = "tmux",
    uri: str,
    token: str | None,
    token_file: str | None = None,
    env: Mapping[str, str],
) -> None:
    """Start the persistent directed waiter for a provider tmux session."""
    pidfile, logfile = _waiter_paths(identity, env)
    if _pid_is_alive(pidfile):
        return

    wait_command = [
        synapse_bin,
        "agent-tmux",
        "wait",
        "--identity",
        identity,
        "--session",
        session,
        "--cwd",
        str(cwd),
        "--agent-command",
        shlex.join(command),
        "--synapse-bin",
        synapse_bin,
        "--tmux-bin",
        tmux_bin,
    ]
    # Prefer --token-file so the child argv never carries a process-visible secret
    # (SCH-H-NEW-16). Inline --token remains only when no file was supplied.
    if token_file:
        wait_command.extend(["--token-file", token_file])
    elif token:
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
    try:
        pidfile.write_text(f"{waiter.pid}\n", encoding="utf-8")
        threading.Thread(target=waiter.wait, name="synapse-waiter-reaper", daemon=True).start()
    except BaseException:
        waiter.terminate()
        try:
            waiter.wait(timeout=SIDECAR_SHUTDOWN_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            waiter.kill()
            waiter.wait()
        raise


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


def _record_launch(
    identity: str, command: Sequence[str], pid: int, env: Mapping[str, str], *, cwd: Path
) -> None:
    """Keep the record of one launch; say so and go on when it cannot be kept.

    The session matters more than its record: a record that cannot be made or
    written is reported on standard error and the launch continues. An
    environment that names no state or home directory has no place for
    records, and none is written.
    """
    directory = record_dir_for(env)
    if directory is None:
        return
    try:
        write_record(
            directory, make_record(seat=identity, command=command, pid=pid, environ=env, cwd=cwd)
        )
    except (SynapseError, OSError) as exc:
        print(f"synapse worker-session: launch record not written: {exc}", file=sys.stderr)


def _run_provider(
    command: Sequence[str], env: Mapping[str, str], *, cwd: Path, outcome: WorkerObservation
) -> WorkerExit:
    """Observe one native child; confirmed exit never establishes external effect undo."""
    outcome.observe(OutcomeState.STARTED, "provider_launch_invoked")
    try:
        provider = subprocess.Popen(list(command), env=dict(env), cwd=cwd)  # nosec B603
    except OSError:
        outcome.observe(OutcomeState.NOT_STARTED, "provider_launch_refused")
        raise
    with provider:
        try:
            outcome.observe(OutcomeState.STARTED, "provider_started")
            _record_launch(env["SYN_IDENTITY"], command, provider.pid, env, cwd=cwd)
            returncode = provider.wait()
        except BaseException:
            # An interrupted wait must not leave the provider behind, running or unreaped.
            provider.kill()
            provider.wait()
            outcome.observe(
                OutcomeState.UNKNOWN,
                "provider_wait_interrupted",
                stop_requested=True,
                stop_confirmed=True,
            )
            raise
    return outcome.finish(returncode)


def _run_terminal_tmux_session(
    *,
    identity: str,
    command: Sequence[str],
    env: Mapping[str, str],
    uri: str,
    token: str | None,
    token_file: str | None = None,
    tmux_bin: str,
    synapse_bin: str,
    session: str | None,
    cwd: Path,
) -> int:
    """Run an interactive provider through a persistent tmux-backed terminal."""
    resolved_session = session or _default_tmux_session(identity)
    config = AgentTmuxConfig(
        identity=identity,
        session=resolved_session,
        cwd=cwd,
        agent_command=tuple(command),
        tmux_bin=tmux_bin,
        synapse_bin=synapse_bin,
        uri=uri,
        token=token,
    )
    started = start_session(config)
    if started.returncode != 0:
        print(started.detail)
        return started.returncode
    pane = subprocess.run(  # nosec B603
        [tmux_bin, "display-message", "-p", "-t", resolved_session, "#{pane_pid}"],
        env=dict(env),
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    if pane.isdigit():
        _record_launch(identity, command, int(pane), env, cwd=cwd)
    _start_tmux_waiter(
        identity=identity,
        session=resolved_session,
        cwd=cwd,
        command=command,
        synapse_bin=synapse_bin,
        tmux_bin=tmux_bin,
        uri=uri,
        token=token,
        token_file=token_file,
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
    outcome_file: Path | None = None,
) -> WorkerExit:
    """Run ``command`` with ``SYN_PROJECT``/``SYN_IDENTITY`` and an optional waker.

    The sidecar is a local ``syn arm`` process. It holds a socket and prints wake
    messages, but it does not call a model provider and therefore does not spend
    tokens while waiting. ``cwd`` sets the provider working directory in both
    direct and tmux modes; it defaults to the directory at the time of this call.
    That same directory resolves POSIX executable paths in the launch record.
    """
    if not command:
        raise ValueError("worker-session requires a provider command")
    resolved_cwd = Path.cwd() if cwd is None else cwd.absolute()
    env = dict(os.environ if environ is None else environ)
    resolved_project = (project or _project_from_identity(identity)).strip()
    env["SYN_PROJECT"] = resolved_project
    env["SYN_IDENTITY"] = identity.strip()
    outcome = WorkerObservation(outcome_file)
    if _terminal_tmux_enabled(command=command, mode=terminal_tmux, env=env):
        env["SYN_TMUX_PROVIDER"] = "1"
        env["SYNAPSE_AUTO_CONNECT"] = "0"
        return outcome.tmux_result(
            lambda: _run_terminal_tmux_session(
                identity=env["SYN_IDENTITY"],
                command=command,
                env=env,
                uri=uri,
                token=token,
                token_file=token_file,
                tmux_bin=tmux_bin,
                synapse_bin=synapse_bin,
                session=tmux_session,
                cwd=resolved_cwd,
            )
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
        return _run_provider(command, env, cwd=resolved_cwd, outcome=outcome)
    finally:
        if sidecar is not None and sidecar.poll() is None:
            sidecar.terminate()
            try:
                sidecar.wait(timeout=sidecar_shutdown_timeout_seconds)
            except subprocess.TimeoutExpired:
                sidecar.kill()
                sidecar.wait()
