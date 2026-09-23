# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — pinned pi participant with owner-local sessions
"""Drive a pinned pi RPC child through one bounded participant turn."""

from __future__ import annotations

import math
import os
import shutil
import stat
import subprocess  # nosec B404
import uuid
from dataclasses import dataclass
from pathlib import Path

from synapse_channel.participants.envelope import (
    TurnRequest,
    TurnResult,
    build_turn_result,
    error_turn_result,
    stamp_model,
)
from synapse_channel.participants.participant import ParticipantChannel, ParticipantHealth
from synapse_channel.participants.pi_process import PiRpcProcess
from synapse_channel.participants.pi_rpc import PI_RPC_VERSION, PiRpcError
from synapse_channel.participants.stream_json import StreamOutcome
from synapse_channel.pi_claim_guard import PiGuardContext

DEFAULT_BINARY = "pi"
DEFAULT_TIMEOUT = 600.0
PI_GUARD_COMMAND = "synapse-claim-guard-health"


@dataclass(frozen=True)
class PiClaimBinding:
    """Opt-in coding-tool claim context fixed for one participant launch."""

    project: str
    repository: Path
    task_id: str
    epoch: int
    uri: str
    extension: Path
    synapse_binary: str = "synapse"
    token_file: Path | None = None


def _session_id(token: str) -> str:
    """Accept only an exact UUID resume token, never a host path or partial ID."""
    try:
        parsed = uuid.UUID(token)
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("pi resume session must be an exact UUID") from exc
    if str(parsed) != token:
        raise ValueError("pi resume session must use canonical UUID spelling")
    return token


def _private_session_dir(path: Path) -> Path:
    """Create or verify an owner-only, non-symlink session directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError("pi session directory cannot be a symlink")
    path.mkdir(mode=0o700, exist_ok=True)
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise ValueError("pi session directory must be owned by the current user")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise ValueError("pi session directory must be private")
    return path.resolve(strict=True)


def _require_resume_file(directory: Path, session_id: str) -> None:
    """Refuse an unknown resume ID instead of silently creating a fresh session."""
    matches = list(directory.glob(f"*_{session_id}.jsonl"))
    if len(matches) != 1 or matches[0].is_symlink():
        raise ValueError("pi resume session is missing or ambiguous")
    info = matches[0].stat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
        raise ValueError("pi resume session must be an owner-owned file")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise ValueError("pi resume session must be private")


class PiParticipant:
    """One pinned pi RPC provider session per turn, with safe tool defaults."""

    def __init__(
        self,
        identity: str,
        *,
        directory: str | Path = ".",
        model: str = "",
        binary: str = DEFAULT_BINARY,
        session_dir: str | Path | None = None,
        claim_binding: PiClaimBinding | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        if not math.isfinite(timeout) or timeout <= 0 or timeout > 3600:
            raise ValueError("pi timeout must be finite and within (0, 3600]")
        self._identity = identity
        self._directory = Path(directory).expanduser().resolve(strict=True)
        self._model = model
        self._binary = binary
        state = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
        self._session_dir = (
            Path(session_dir).expanduser()
            if session_dir is not None
            else state / "synapse-channel" / "pi"
        )
        self._timeout = timeout
        self._claim_binding = claim_binding

    @property
    def identity(self) -> str:
        """Return the bus identity of this pi participant."""
        return self._identity

    @property
    def channel(self) -> ParticipantChannel:
        """Declare the subprocess transport used for pi RPC."""
        return ParticipantChannel.HEADLESS

    def health(self) -> ParticipantHealth:
        """Require the exact verified pi release before accepting a turn."""
        resolved = shutil.which(self._binary)
        if resolved is None:
            return ParticipantHealth(self._identity, self.channel, False, "pi binary not found")
        try:
            completed = subprocess.run(  # nosec B603
                [resolved, "--version"],
                capture_output=True,
                text=True,
                check=False,
                timeout=min(self._timeout, 10.0),
                cwd=self._directory,
            )
            version = completed.stdout.strip() if completed.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            version = ""
        ready = version == PI_RPC_VERSION
        detail = (
            f"pi {version} at {resolved}"
            if ready
            else f"pi version {version or 'unknown'} is not verified {PI_RPC_VERSION}"
        )
        return ParticipantHealth(self._identity, self.channel, ready, detail)

    async def take_turn(self, request: TurnRequest) -> TurnResult:
        """Run one turn; a response receipt is never treated as completion."""
        health = self.health()
        if not health.available:
            return error_turn_result(
                participant=self._identity,
                channel=self.channel,
                request=request,
                reason=health.detail,
            )
        resolved_binary = shutil.which(self._binary)
        if resolved_binary is None:
            return error_turn_result(
                participant=self._identity,
                channel=self.channel,
                request=request,
                reason="pi binary disappeared after version check",
            )
        model = request.model or self._model
        if not model:
            return error_turn_result(
                participant=self._identity,
                channel=self.channel,
                request=request,
                reason="pi model must be explicit",
            )
        try:
            session_id = (
                _session_id(request.resume_session) if request.resume_session else str(uuid.uuid4())
            )
            session_dir = _private_session_dir(self._session_dir)
            if request.resume_session:
                _require_resume_file(session_dir, session_id)
            argv = [
                resolved_binary,
                "--mode",
                "rpc",
                "--model",
                model,
                "--session-id",
                session_id,
                "--session-dir",
                str(session_dir),
                "--no-context-files",
                "--no-extensions",
                "--no-approve",
                "--offline",
            ]
            environment = dict(os.environ, PI_OFFLINE="1", PI_TELEMETRY="0")
            binding = self._claim_binding
            if binding is None:
                argv.append("--no-tools")
            else:
                PiGuardContext(
                    identity=self._identity,
                    project=binding.project,
                    repository=binding.repository,
                    task_id=binding.task_id,
                    epoch=binding.epoch,
                    session_id=session_id,
                ).validate()
                extension = binding.extension.resolve(strict=True)
                extension_info = extension.stat()
                if (
                    not stat.S_ISREG(extension_info.st_mode)
                    or extension_info.st_uid != os.getuid()
                    or stat.S_IMODE(extension_info.st_mode) & 0o022
                    or not binding.uri
                ):
                    raise ValueError("pi extension and hub URI must be valid")
                argv.extend(
                    ["--tools", "read,grep,find,ls,write,edit,bash", "--extension", str(extension)]
                )
                repository = binding.repository.resolve(strict=True)
                environment.update(
                    SYN_PROJECT=binding.project,
                    SYN_IDENTITY=self._identity,
                    SYNAPSE_PI_IDENTITY=self._identity,
                    SYNAPSE_PI_PROJECT=binding.project,
                    SYNAPSE_PI_REPOSITORY=str(repository),
                    SYNAPSE_PI_TASK_ID=binding.task_id,
                    SYNAPSE_PI_EPOCH=str(binding.epoch),
                    SYNAPSE_PI_SESSION_ID=session_id,
                    SYNAPSE_PI_HUB_URI=binding.uri,
                    SYNAPSE_PI_BIN=binding.synapse_binary,
                )
                if binding.token_file is not None:
                    token_file = binding.token_file.expanduser().resolve(strict=True)
                    if token_file.is_relative_to(repository):
                        raise ValueError("pi hub token file must be outside the repository")
                    token_info = token_file.stat()
                    if (
                        not stat.S_ISREG(token_info.st_mode)
                        or token_info.st_uid != os.getuid()
                        or stat.S_IMODE(token_info.st_mode) & 0o077
                    ):
                        raise ValueError("pi hub token file must be owner-private")
                    environment["SYNAPSE_PI_TOKEN_FILE"] = str(token_file)
            async with PiRpcProcess(
                argv,
                cwd=self._directory,
                environment=environment,
                timeout=self._timeout,
            ) as child:
                state = await child.command("get_state")
                data = state.get("data")
                if not isinstance(data, dict) or data.get("sessionId") != session_id:
                    raise PiRpcError("pi session identity did not match the requested session")
                if binding is not None:
                    commands_response = await child.command("get_commands")
                    commands_data = commands_response.get("data")
                    commands = (
                        commands_data.get("commands") if isinstance(commands_data, dict) else None
                    )
                    loaded = isinstance(commands, list) and any(
                        isinstance(command, dict)
                        and command.get("name") == PI_GUARD_COMMAND
                        and command.get("source") == "extension"
                        and isinstance(command.get("sourceInfo"), dict)
                        and command["sourceInfo"].get("path") == str(extension)
                        for command in commands
                    )
                    if not loaded:
                        raise PiRpcError("pi claim guard extension did not load")
                prompt = (
                    request.prompt
                    if not request.context
                    else f"{request.context}\n\n----- TASK -----\n\n{request.prompt}"
                )
                await child.command("prompt", message=prompt)
                turn = await child.next_settled()
            outcome = StreamOutcome(
                answer=turn.answer,
                rationale="",
                session_id=session_id,
                is_error=turn.is_error,
                subtype=(
                    "pi tool call was blocked or failed"
                    if turn.tool_error
                    else turn.stop_reason
                    if turn.is_error
                    else "success"
                ),
                cost_usd=turn.cost_usd,
                num_turns=1,
                stop_reason=turn.stop_reason,
                input_tokens=turn.input_tokens,
                output_tokens=turn.output_tokens,
            )
            result = build_turn_result(
                participant=self._identity,
                channel=self.channel,
                request=request,
                outcome=outcome,
            )
            return stamp_model(result, model)
        except (OSError, ValueError, PiRpcError, subprocess.SubprocessError) as exc:
            return error_turn_result(
                participant=self._identity,
                channel=self.channel,
                request=request,
                reason=f"pi RPC turn failed: {exc}",
            )
