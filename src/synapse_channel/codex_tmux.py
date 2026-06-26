# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tmux-backed Codex wake transport
"""Tmux-backed wake transport for an existing Codex terminal session."""

from __future__ import annotations

import json
import os
import shlex

# Tmux and synapse subprocesses are this module's controlled process boundary.
import subprocess  # nosec B404
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import gettempdir
from typing import Protocol

from synapse_channel.client.agent import DEFAULT_HUB_URI

CODEX_PANE_COMMANDS = frozenset({"codex", "node"})
"""Pane command names that indicate a live Codex terminal stack."""


class CommandRunner(Protocol):
    """Callable compatible with :func:`subprocess.run` for injectable tests."""

    def __call__(
        self,
        args: Sequence[str],
        *,
        capture_output: bool = False,
        text: bool = False,
        check: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        """Run ``args`` and return the completed process."""


@dataclass(frozen=True)
class CodexTmuxConfig:
    """Configuration for one tmux-backed Codex wake target."""

    identity: str
    session: str
    cwd: Path
    codex_command: tuple[str, ...] = ("codex",)
    tmux_bin: str = "tmux"
    synapse_bin: str = "synapse"
    uri: str = DEFAULT_HUB_URI
    token: str | None = None
    registry_dir: Path | None = None


@dataclass(frozen=True)
class CodexTmuxWakeResult:
    """Result returned by tmux start and wake operations."""

    injected: bool
    started: bool
    returncode: int
    detail: str


@dataclass(frozen=True)
class CodexTmuxStatus:
    """Health snapshot for one tmux-backed Codex wake target."""

    identity: str
    session: str
    session_exists: bool
    pane_command: str | None
    pane_start_command: str | None
    codex_active: bool


@dataclass(frozen=True)
class RegistryRecord:
    """Local registry record for one tmux-backed Codex wake target."""

    identity: str
    session: str
    cwd: str
    updated_at: float = field(default_factory=time.time)
    last_start_returncode: int | None = None
    last_inject_returncode: int | None = None


def _safe_key(identity: str) -> str:
    """Return the filesystem-safe registry key for ``identity``."""
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in identity)


def _project_from_identity(identity: str) -> str:
    """Return the project segment for an identity."""
    return identity.split("/", 1)[0].strip()


def _registry_dir(config: CodexTmuxConfig) -> Path:
    """Return the registry directory for ``config``."""
    if config.registry_dir is not None:
        return config.registry_dir
    return Path(os.environ.get("XDG_RUNTIME_DIR") or gettempdir()) / "synapse-codex-tmux"


def registry_path(config: CodexTmuxConfig) -> Path:
    """Return the registry file path for ``config``."""
    return _registry_dir(config) / f"{_safe_key(config.identity)}.json"


def _write_registry(
    config: CodexTmuxConfig,
    *,
    last_start_returncode: int | None = None,
    last_inject_returncode: int | None = None,
) -> None:
    """Write the local wake-target registry atomically."""
    path = registry_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = RegistryRecord(
        identity=config.identity,
        session=config.session,
        cwd=str(config.cwd),
        last_start_returncode=last_start_returncode,
        last_inject_returncode=last_inject_returncode,
    )
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(record.__dict__, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(path)


def build_wake_prompt(identity: str) -> str:
    """Build the fixed prompt injected into the Codex tmux pane.

    The prompt contains only routing metadata. It deliberately excludes any
    Synapse message payload so a remote sender cannot inject terminal text.
    """
    return (
        "Synapse wake: read your Synapse inbox for "
        f"{identity}, handle the newest directed message under the current "
        "repository rules, report status to Synapse, then stop and wait."
    )


def _has_session(config: CodexTmuxConfig, *, runner: CommandRunner) -> bool:
    """Return whether the configured tmux session exists."""
    proc = runner(
        [config.tmux_bin, "has-session", "-t", config.session],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode == 0


def start_session(
    config: CodexTmuxConfig,
    *,
    runner: CommandRunner = subprocess.run,
) -> CodexTmuxWakeResult:
    """Start the tmux session when it is missing."""
    if _has_session(config, runner=runner):
        _write_registry(config, last_start_returncode=0)
        return CodexTmuxWakeResult(
            injected=False,
            started=False,
            returncode=0,
            detail=f"tmux session {config.session} already exists",
        )

    command = shlex.join(
        [
            "env",
            f"SYN_PROJECT={_project_from_identity(config.identity)}",
            f"SYN_IDENTITY={config.identity}",
            *config.codex_command,
        ]
    )
    proc = runner(
        [
            config.tmux_bin,
            "new-session",
            "-d",
            "-s",
            config.session,
            "-c",
            str(config.cwd),
            command,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    _write_registry(config, last_start_returncode=proc.returncode)
    return CodexTmuxWakeResult(
        injected=False,
        started=proc.returncode == 0,
        returncode=proc.returncode,
        detail="started" if proc.returncode == 0 else (proc.stderr or proc.stdout).strip(),
    )


def inject_wake(
    config: CodexTmuxConfig,
    *,
    runner: CommandRunner = subprocess.run,
    unsafe_payload: str | None = None,
) -> CodexTmuxWakeResult:
    """Inject the fixed wake prompt into the configured tmux pane."""
    del unsafe_payload
    proc = runner(
        [
            config.tmux_bin,
            "send-keys",
            "-t",
            config.session,
            build_wake_prompt(config.identity),
            "C-m",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    _write_registry(config, last_inject_returncode=proc.returncode)
    return CodexTmuxWakeResult(
        injected=proc.returncode == 0,
        started=False,
        returncode=proc.returncode,
        detail="injected" if proc.returncode == 0 else (proc.stderr or proc.stdout).strip(),
    )


def status(
    config: CodexTmuxConfig,
    *,
    runner: CommandRunner = subprocess.run,
) -> CodexTmuxStatus:
    """Return the tmux session and Codex pane status for ``config``."""
    if not _has_session(config, runner=runner):
        return CodexTmuxStatus(
            identity=config.identity,
            session=config.session,
            session_exists=False,
            pane_command=None,
            pane_start_command=None,
            codex_active=False,
        )
    proc = runner(
        [
            config.tmux_bin,
            "display-message",
            "-p",
            "-t",
            config.session,
            "#{pane_current_command}\t#{pane_start_command}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    output = proc.stdout.strip() if proc.returncode == 0 else ""
    pane_command: str | None = None
    pane_start_command: str | None = None
    if output:
        pane_command, _, pane_start_command = output.partition("\t")
        pane_start_command = pane_start_command or None
    start_parts = shlex.split(pane_start_command.strip('"')) if pane_start_command else []
    started_with_codex = any(part == "codex" or part.endswith("/codex") for part in start_parts)
    return CodexTmuxStatus(
        identity=config.identity,
        session=config.session,
        session_exists=True,
        pane_command=pane_command,
        pane_start_command=pane_start_command,
        codex_active=pane_command in CODEX_PANE_COMMANDS or started_with_codex,
    )


def _wait_command(config: CodexTmuxConfig) -> list[str]:
    """Build the one-shot ``synapse wait`` command for ``config``."""
    command = [
        config.synapse_bin,
        "wait",
        "--name",
        f"{config.identity}-rx",
        "--for",
        config.identity,
        "--timeout",
        "0",
        "--directed-only",
    ]
    if config.uri != DEFAULT_HUB_URI:
        command.extend(["--uri", config.uri])
    if config.token:
        command.extend(["--token", config.token])
    return command


def wait_and_wake(
    config: CodexTmuxConfig,
    *,
    runner: CommandRunner = subprocess.run,
    max_wakes: int | None = None,
) -> int:
    """Run the wait loop and inject the fixed prompt after successful wakes."""
    wakes = 0
    while max_wakes is None or wakes < max_wakes:
        wait_proc = runner(_wait_command(config), capture_output=True, text=True, check=False)
        if wait_proc.returncode != 0:
            return wait_proc.returncode
        wake = inject_wake(config, runner=runner, unsafe_payload=wait_proc.stdout)
        if wake.returncode != 0:
            return wake.returncode
        wakes += 1
    return 0
