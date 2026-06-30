# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — headless Claude Code participant driver
"""Drive a Claude Code session headlessly as a bus participant.

This is the first concrete :class:`~synapse_channel.participants.participant.Participant`
and the reference for the ``HEADLESS`` channel: the bus owns the invocation, spawning
``claude -p … --output-format stream-json --verbose`` in non-interactive mode and reading
its structured event stream rather than scraping a terminal. The turn's question is the
provider's user prompt; the shared bus context — role, ground rules, and any fenced peer
contributions — is injected through ``--append-system-prompt`` so peer-supplied text can
never arrive as the operator's ask.

The heavy logic lives in a synchronous, dependency-injected
:meth:`HeadlessClaudeParticipant.run_turn` so it is hermetically testable with a fake
runner and no real model call; the async
:meth:`HeadlessClaudeParticipant.take_turn` required by the protocol is a thin
``asyncio.to_thread`` wrapper. Argv construction is a separate pure function so the exact
command line is asserted without spawning anything. A missing binary, a non-zero exit, or
a timeout becomes an error :class:`~synapse_channel.participants.envelope.TurnResult`,
never a raised exception, so one bad turn cannot strand a conversation.
"""

from __future__ import annotations

import asyncio
import shutil

# The Claude CLI is this module's controlled subprocess boundary; argv is built from
# typed fields and never from a shell string.
import subprocess  # nosec B404
from collections.abc import Sequence
from typing import Protocol

from synapse_channel.participants.envelope import (
    TurnRequest,
    TurnResult,
    build_turn_result,
    error_turn_result,
    stamp_model,
)
from synapse_channel.participants.participant import (
    ParticipantChannel,
    ParticipantHealth,
)
from synapse_channel.participants.stream_json import parse_claude_stream

DEFAULT_BINARY = "claude"
"""Default Claude Code executable name resolved on ``PATH``."""

DEFAULT_TIMEOUT = 600.0
"""Default wall-clock ceiling, in seconds, for one headless turn."""


class CommandRunner(Protocol):
    """Callable compatible with :func:`subprocess.run` for injectable tests."""

    def __call__(
        self,
        args: Sequence[str],
        *,
        capture_output: bool = False,
        text: bool = False,
        check: bool = False,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Run ``args`` and return the completed process."""


def build_claude_argv(
    *,
    prompt: str,
    binary: str = DEFAULT_BINARY,
    model: str = "",
    append_system_prompt: str = "",
    resume_session: str = "",
    persist_session: bool = False,
) -> list[str]:
    """Build the headless Claude command line for one turn.

    Parameters
    ----------
    prompt : str
        The turn's question, passed as the provider's user prompt via ``-p``.
    binary : str, optional
        The Claude executable name or path.
    model : str, optional
        Model id for ``--model``; omitted when empty so the provider default applies.
    append_system_prompt : str, optional
        Shared bus context injected via ``--append-system-prompt``; omitted when empty.
    resume_session : str, optional
        Provider session id for ``--resume``. When set, the session must persist, so it
        overrides ``persist_session`` and suppresses ``--no-session-persistence``.
    persist_session : bool, optional
        Keep the provider session on disk. Defaults to false, adding
        ``--no-session-persistence`` for a clean one-shot turn; ignored when resuming.

    Returns
    -------
    list[str]
        The argv, always requesting ``stream-json`` with ``--verbose`` (which the CLI
        requires for streamed JSON under ``-p``).
    """
    argv = [binary, "-p", prompt, "--output-format", "stream-json", "--verbose"]
    if model:
        argv += ["--model", model]
    if append_system_prompt:
        argv += ["--append-system-prompt", append_system_prompt]
    if resume_session:
        argv += ["--resume", resume_session]
    elif not persist_session:
        argv += ["--no-session-persistence"]
    return argv


class HeadlessClaudeParticipant:
    """A Claude Code session driven headlessly as a uniform bus participant.

    Parameters
    ----------
    identity : str
        The participant's bus identity.
    model : str, optional
        Model id passed to every turn; empty uses the provider default.
    binary : str, optional
        Claude executable name or path.
    runner : CommandRunner, optional
        Subprocess runner; injectable so tests drive turns with a fake, never a real
        model call.
    timeout : float, optional
        Per-turn wall-clock ceiling, in seconds.
    persist_session : bool, optional
        Whether fresh turns keep their provider session on disk (see :func:`build_claude_argv`).
    """

    def __init__(
        self,
        identity: str,
        *,
        model: str = "",
        binary: str = DEFAULT_BINARY,
        runner: CommandRunner = subprocess.run,
        timeout: float = DEFAULT_TIMEOUT,
        persist_session: bool = False,
    ) -> None:
        self._identity = identity
        self._model = model
        self._binary = binary
        self._runner = runner
        self._timeout = timeout
        self._persist_session = persist_session

    @property
    def identity(self) -> str:
        """Return the participant's bus identity."""
        return self._identity

    @property
    def channel(self) -> ParticipantChannel:
        """Return :attr:`ParticipantChannel.HEADLESS`."""
        return ParticipantChannel.HEADLESS

    def health(self) -> ParticipantHealth:
        """Report whether the Claude binary resolves on ``PATH``.

        Returns
        -------
        ParticipantHealth
            ``available`` is true when the configured binary is found; headless turns
            spawn on demand, so there is no long-lived process to probe beyond that.
        """
        resolved = shutil.which(self._binary)
        return ParticipantHealth(
            identity=self._identity,
            channel=ParticipantChannel.HEADLESS,
            available=resolved is not None,
            detail=f"claude binary at {resolved}"
            if resolved is not None
            else f"claude binary {self._binary!r} not found on PATH",
        )

    def run_turn(self, request: TurnRequest) -> TurnResult:
        """Run one turn synchronously and return its typed result.

        Builds the argv, runs the provider once, and parses its ``stream-json`` output.
        A missing binary, a subprocess error, or a timeout is converted into an error
        result rather than raised.

        Parameters
        ----------
        request : TurnRequest
            The turn to run.

        Returns
        -------
        TurnResult
            The parsed outcome, or an error result when the provider could not be run.
        """
        argv = build_claude_argv(
            prompt=request.prompt,
            binary=self._binary,
            model=self._model,
            append_system_prompt=request.context,
            resume_session=request.resume_session,
            persist_session=self._persist_session,
        )
        try:
            completed = self._runner(
                argv,
                capture_output=True,
                text=True,
                check=False,
                timeout=self._timeout,
            )
        except subprocess.TimeoutExpired:
            return error_turn_result(
                participant=self._identity,
                channel=ParticipantChannel.HEADLESS,
                request=request,
                reason=f"headless turn exceeded {self._timeout:g}s timeout",
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return error_turn_result(
                participant=self._identity,
                channel=ParticipantChannel.HEADLESS,
                request=request,
                reason=f"failed to run {self._binary!r}: {exc}",
            )
        outcome = parse_claude_stream((completed.stdout or "").splitlines())
        if completed.returncode != 0 and outcome.answer == "":
            return error_turn_result(
                participant=self._identity,
                channel=ParticipantChannel.HEADLESS,
                request=request,
                reason=f"{self._binary!r} exited {completed.returncode}: "
                f"{(completed.stderr or '').strip() or 'no output'}",
            )
        return build_turn_result(
            participant=self._identity,
            channel=ParticipantChannel.HEADLESS,
            request=request,
            outcome=outcome,
        )

    async def take_turn(self, request: TurnRequest) -> TurnResult:
        """Take one turn off the event loop via :meth:`run_turn`.

        Parameters
        ----------
        request : TurnRequest
            The turn to run.

        Returns
        -------
        TurnResult
            The same result :meth:`run_turn` produces, computed in a worker thread so the
            blocking subprocess never stalls the bus event loop.
        """
        result = await asyncio.to_thread(self.run_turn, request)
        return stamp_model(result, self._model)
