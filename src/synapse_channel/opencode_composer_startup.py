# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — qualify new native clients before publishing wake targets
"""Keep unknown keybindings away from existing sessions and owner drafts."""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from synapse_channel.opencode_composer import LAUNCH_ENV, PROTOCOL
from synapse_channel.opencode_composer_files import (
    ComposerError,
    probe_lock,
    read_document,
    write_document,
)
from synapse_channel.opencode_composer_probe import (
    editor_environment,
    footer_is_probeable,
    profile_directory,
    qualify_startup_composer,
)

if TYPE_CHECKING:
    from synapse_channel.agent_tmux import AgentTmuxConfig, CommandRunner


class _CommandResult(Protocol):
    """Captured text and exit status supplied by the owned command boundary."""

    @property
    def returncode(self) -> int:
        """Return the completed command's exit status."""

    @property
    def stdout(self) -> str:
        """Return captured standard output."""

    @property
    def stderr(self) -> str:
        """Return captured standard error."""


@dataclass(frozen=True)
class _Refusal:
    """Keep admission refusal independent from process execution."""

    returncode: int = 4
    stdout: str = ""
    stderr: str = "native startup refused: the new client did not prove its editor shortcut"


Launch = Callable[[str, Sequence[str]], _CommandResult]
_STARTUP_SECONDS = 15.0


def _require_new_client(command: tuple[str, ...]) -> None:
    """Refuse initial input or history adoption before creating the private client."""
    flags = {"--mini", "--pure", "--print-logs"}
    values = {"--model", "-m", "--agent", "--variant", "--log-level"}
    remaining = iter(command[1:])
    for argument in remaining:
        if argument in flags:
            continue
        if argument in values:
            value = next(remaining, "")
            if value and not value.startswith("-"):
                continue
        elif any(
            argument.startswith(option + "=") and argument != option + "=" for option in values
        ):
            continue
        raise ComposerError("native readback startup requires a new client without initial input")


def _control(config: AgentTmuxConfig, runner: CommandRunner, *arguments: str) -> _CommandResult:
    """Run one bounded native session-control exchange."""
    return runner([config.tmux_bin, *arguments], capture_output=True, text=True, check=False)


def _initial_screen(config: AgentTmuxConfig, target: str, runner: CommandRunner) -> str:
    """Wait for the new client's normal initial composer without typing text."""
    deadline = time.monotonic() + _STARTUP_SECONDS
    while time.monotonic() < deadline:
        result = _control(config, runner, "capture-pane", "-t", target, "-p", "-J")
        if result.returncode:
            raise ComposerError("new native client exited before qualification")
        if "Ask anything..." in result.stdout and footer_is_probeable(result.stdout):
            return result.stdout
        time.sleep(0.05)
    raise ComposerError("new native client did not reach its initial composer")


def _retire_owned_startup(
    config: AgentTmuxConfig, target: str, nonce: str, runner: CommandRunner
) -> None:
    """Retire only the unpublished session retaining our exact launch marker."""
    observed = _control(config, runner, "show-environment", "-t", target, LAUNCH_ENV)
    if observed.returncode:
        if _control(config, runner, "has-session", "-t", target).returncode:
            return
        raise ComposerError("unpublished native startup ownership is unavailable")
    if observed.stdout.strip() != f"{LAUNCH_ENV}={nonce}":
        raise ComposerError("unpublished native startup ownership changed")
    retired = _control(config, runner, "kill-session", "-t", target)
    if retired.returncode:
        raise ComposerError("owned native startup session could not be retired")


def _recover_startup(config: AgentTmuxConfig, runner: CommandRunner) -> None:
    """Recover only a durable nonce-bound unpublished launch after caller death."""
    record = profile_directory(config) / "startup.json"
    try:
        record.lstat()
    except FileNotFoundError:
        return
    document = read_document(record)
    nonce = document.get("nonce")
    target = document.get("target")
    if (
        document.get("protocol") != PROTOCOL
        or document.get("identity") != config.identity
        or document.get("session") != config.session
        or document.get("tmux_bin") != config.tmux_bin
        or not isinstance(nonce, str)
        or not re.fullmatch(r"[0-9a-f]{32}", nonce)
        or target != "synapse-native-start-" + nonce
    ):
        raise ComposerError("native startup recovery record is invalid")
    _retire_owned_startup(config, str(target), nonce, runner)


def launch_editor_session(
    config: AgentTmuxConfig,
    environment: Sequence[str],
    *,
    launch: Launch,
    runner: CommandRunner,
) -> _CommandResult:
    """Qualify a new empty client's actual keymap, then publish its session.

    Parameters
    ----------
    config : AgentTmuxConfig
        Explicitly opted-in new pinned OpenCode mini client. Existing sessions
        are handled by the caller before this lifecycle begins.
    environment : Sequence[str]
        Managed identity variables, without altering owner keybinding files.
    launch : Callable
        Creates one session under the supplied private name and environment.
    runner : CommandRunner
        Bounded tmux commands; injected runners must provide their own deadline.

    Returns
    -------
    _CommandResult
        Success only after native readback and renaming that same process to
        the requested target. Refusal retires only its nonce-bound temporary
        session and never sends a model prompt or touches another session.
    """
    target = ""
    nonce = ""
    created = False
    published = False
    try:
        _require_new_client(config.agent_command)
        root = profile_directory(config)
        with probe_lock(root, name="wake.lock"):
            if not _control(config, runner, "has-session", "-t", config.session).returncode:
                raise ComposerError("managed target appeared during native startup")
            _recover_startup(config, runner)
            native_environment = [*environment, *editor_environment(config)]
            profile = read_document(root / "profile.json")
            value = profile.get("launch_nonce")
            if not isinstance(value, str):
                raise ComposerError("native startup marker is unavailable")
            nonce = value
            target = "synapse-native-start-" + nonce
            write_document(
                root,
                "startup.json",
                {
                    "protocol": PROTOCOL,
                    "identity": config.identity,
                    "session": config.session,
                    "tmux_bin": config.tmux_bin,
                    "target": target,
                    "nonce": nonce,
                },
            )
            result = launch(target, native_environment)
            if result.returncode:
                return result
            created = True
            try:
                screen = _initial_screen(config, target, runner)
                qualify_startup_composer(config, target, screen, runner=runner)
                renamed = _control(config, runner, "rename-session", "-t", target, config.session)
                if renamed.returncode:
                    raise ComposerError("native target could not be published")
                published = True
                return result
            finally:
                if created and not published:
                    _retire_owned_startup(config, target, nonce, runner)
    except (ValueError, OSError):
        return _Refusal()
