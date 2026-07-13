# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — pinned OpenCode headless participant
"""Drive source-verified OpenCode 1.17.20 locally or through ``run --attach``."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess  # nosec B404
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol

from synapse_channel.participants.envelope import (
    TurnRequest,
    TurnResult,
    build_turn_result,
    error_turn_result,
    stamp_model,
)
from synapse_channel.participants.opencode_auth import load_password_file, validate_endpoint
from synapse_channel.participants.opencode_stream import (
    OPENCODE_SCHEMA_VERIFIED,
    OPENCODE_SCHEMA_VERSION,
    parse_opencode_stream,
)
from synapse_channel.participants.participant import ParticipantChannel, ParticipantHealth
from synapse_channel.participants.process_error import (
    format_process_failure,
    format_process_start_failure,
)

DEFAULT_BINARY = "opencode"
DEFAULT_TIMEOUT = 600.0


class CommandRunner(Protocol):
    """Subprocess runner interface used at the OpenCode process boundary."""

    def __call__(
        self,
        args: Sequence[str],
        *,
        capture_output: bool,
        text: bool,
        check: bool,
        timeout: float | None,
        input: str | None = None,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Run one argv without a shell and return its completed process."""


def compose_opencode_prompt(context: str, prompt: str) -> str:
    """Prepend fenced participant context when OpenCode has no separate system input."""
    return prompt if not context else f"{context}\n\n----- TASK -----\n\n{prompt}"


def build_opencode_argv(
    *,
    prompt: str,
    directory: Path,
    binary: str = DEFAULT_BINARY,
    model: str = "",
    resume_session: str = "",
    attach: str = "",
    thinking: bool = False,
) -> list[str]:
    """Build one safe OpenCode JSONL invocation; never enable ``--auto``."""
    argv = [binary, "run", "--format", "json", "--dir", str(directory)]
    if model:
        argv.extend(["--model", model])
    if resume_session:
        argv.extend(["--session", resume_session])
    if attach:
        argv.extend(["--attach", attach])
    if thinking:
        argv.append("--thinking")
    argv.append(prompt)
    return argv


class OpenCodeParticipant:
    """A pinned OpenCode CLI participant for local and attached server turns."""

    def __init__(
        self,
        identity: str,
        *,
        directory: str | Path = ".",
        model: str = "",
        binary: str = DEFAULT_BINARY,
        attach: str = "",
        username: str = "opencode",
        password_file: str | None = None,
        allow_insecure_http: bool = False,
        thinking: bool = False,
        runner: CommandRunner = subprocess.run,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self._identity = identity
        self._directory = Path(directory).expanduser().resolve()
        self._model = model
        self._binary = binary
        self._attach = (
            validate_endpoint(attach, allow_insecure_http=allow_insecure_http) if attach else ""
        )
        self._username = username
        self._password_file = password_file
        self._thinking = thinking
        self._runner = runner
        self._timeout = timeout

    @property
    def identity(self) -> str:
        """Return the participant identity."""
        return self._identity

    @property
    def channel(self) -> ParticipantChannel:
        """Return the headless transport channel."""
        return ParticipantChannel.HEADLESS

    def _version(self) -> str:
        completed = self._runner(
            [self._binary, "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=min(self._timeout, 10.0),
            input="",
            cwd=str(self._directory),
            env=None,
        )
        return (completed.stdout or "").strip() if completed.returncode == 0 else ""

    def health(self) -> ParticipantHealth:
        """Require both a resolvable binary and the exact verified emitter version."""
        resolved = shutil.which(self._binary)
        if resolved is None:
            return ParticipantHealth(
                self._identity, ParticipantChannel.HEADLESS, False, "opencode binary not found"
            )
        try:
            version = self._version()
        except (OSError, subprocess.SubprocessError):
            version = ""
        available = OPENCODE_SCHEMA_VERIFIED and version == OPENCODE_SCHEMA_VERSION
        detail = (
            f"opencode {version} at {resolved}"
            if available
            else (
                f"opencode version {version or 'unknown'} is not verified {OPENCODE_SCHEMA_VERSION}"
            )
        )
        return ParticipantHealth(self._identity, ParticipantChannel.HEADLESS, available, detail)

    def _environment(self) -> Mapping[str, str] | None:
        if not self._attach or not self._password_file:
            return None
        environment = dict(os.environ)
        environment["OPENCODE_SERVER_USERNAME"] = self._username
        environment["OPENCODE_SERVER_PASSWORD"] = load_password_file(self._password_file)
        return environment

    def run_turn(self, request: TurnRequest) -> TurnResult:
        """Run one exact-version JSONL turn and normalize its typed result."""
        try:
            version = self._version()
        except (OSError, subprocess.SubprocessError) as exc:
            return error_turn_result(
                participant=self._identity,
                channel=self.channel,
                request=request,
                reason=format_process_start_failure(binary=self._binary, error=exc),
            )
        if version != OPENCODE_SCHEMA_VERSION or not OPENCODE_SCHEMA_VERIFIED:
            return error_turn_result(
                participant=self._identity,
                channel=self.channel,
                request=request,
                reason=(
                    f"OpenCode {version or 'unknown'} is outside verified schema "
                    f"{OPENCODE_SCHEMA_VERSION}"
                ),
            )
        argv = build_opencode_argv(
            prompt=compose_opencode_prompt(request.context, request.prompt),
            directory=self._directory,
            binary=self._binary,
            model=self._model,
            resume_session=request.resume_session,
            attach=self._attach,
            thinking=self._thinking,
        )
        try:
            completed = self._runner(
                argv,
                capture_output=True,
                text=True,
                check=False,
                timeout=self._timeout,
                input="",
                cwd=str(self._directory),
                env=self._environment(),
            )
        except subprocess.TimeoutExpired:
            return error_turn_result(
                participant=self._identity,
                channel=self.channel,
                request=request,
                reason=f"headless turn exceeded {self._timeout:g}s timeout",
            )
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            return error_turn_result(
                participant=self._identity,
                channel=self.channel,
                request=request,
                reason=format_process_start_failure(binary=self._binary, error=exc),
            )
        outcome = parse_opencode_stream((completed.stdout or "").splitlines())
        if completed.returncode != 0:
            return error_turn_result(
                participant=self._identity,
                channel=self.channel,
                request=request,
                reason=format_process_failure(
                    provider="opencode",
                    binary=self._binary,
                    returncode=completed.returncode,
                    stderr=completed.stderr or "",
                ),
            )
        return build_turn_result(
            participant=self._identity, channel=self.channel, request=request, outcome=outcome
        )

    async def take_turn(self, request: TurnRequest) -> TurnResult:
        """Run the blocking process boundary in a worker thread."""
        return stamp_model(await asyncio.to_thread(self.run_turn, request), self._model)
