# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — headless Gemini CLI participant driver
"""Drive a Gemini CLI session headlessly as a bus participant.

.. note::

   **Gemini is a first-class headless provider.** The ``stream-json`` envelope is
   verified against a capture from the installed ``gemini`` 0.47.0 binary's real
   emitter (the ``--fake-responses-non-strict`` harness substitutes only the model
   API client); see :mod:`~synapse_channel.participants.gemini_stream` and
   :data:`~synapse_channel.participants.gemini_stream.GEMINI_SCHEMA_VERIFIED`. Turns
   are enabled while that flag is true. Note the account-side constraint: the CLI's
   OAuth-personal tier fails setup with ``IneligibleTierError`` (individuals were
   moved to Antigravity), so live model turns need an API-key or eligible account —
   the driver reports that failure as an ordinary error result.

A sixth concrete :class:`~synapse_channel.participants.participant.Participant`, on the
``HEADLESS`` channel: the bus owns the invocation, spawning ``gemini -p <prompt>
--output-format stream-json --approval-mode plan`` (adding ``--resume <value>`` to resume)
and reading its event stream via
:func:`~synapse_channel.participants.gemini_stream.parse_gemini_stream`. Gemini exposes no
system-prompt append flag, so like Codex/Kimi/Ollama the shared bus context — role, ground
rules, and any fenced peer contribution — is prepended to the user prompt under an explicit
separator, and each turn runs in the read-only ``plan`` approval mode so a reasoning turn
cannot modify the workspace.

As with the other headless drivers, the heavy logic is a synchronous, dependency-injected
:meth:`GeminiParticipant.run_turn` (hermetically testable with a fake runner) and the async
:meth:`GeminiParticipant.take_turn` is a thin ``asyncio.to_thread`` wrapper. A missing
binary, a non-zero exit with no answer, or a timeout becomes an error result, never a
raised exception.
"""

from __future__ import annotations

import asyncio
import shutil

# The Gemini CLI is this module's controlled subprocess boundary; argv is built from typed
# fields and never from a shell string.
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
from synapse_channel.participants.gemini_stream import parse_gemini_stream
from synapse_channel.participants.participant import (
    ParticipantChannel,
    ParticipantHealth,
)

DEFAULT_BINARY = "gemini"
"""Default Gemini executable name resolved on ``PATH``."""

DEFAULT_TIMEOUT = 600.0
"""Default wall-clock ceiling, in seconds, for one headless turn."""

DEFAULT_APPROVAL_MODE = "plan"
"""Default Gemini approval mode; a reasoning participant never needs to write the workspace."""


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
        input: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Run ``args`` and return the completed process."""


def compose_gemini_prompt(context: str, prompt: str) -> str:
    """Combine shared context and the turn prompt into one Gemini prompt.

    Gemini has no system-prompt append flag (``--help`` on 0.47.0 offers none), so the
    context is prepended to the prompt under a separator. When there is no context the
    prompt is returned unchanged.

    Parameters
    ----------
    context : str
        Shared framing (role, ground rules, fenced peer contributions).
    prompt : str
        The turn's question.

    Returns
    -------
    str
        ``context`` then the prompt, or just the prompt when context is empty.
    """
    if not context:
        return prompt
    return f"{context}\n\n----- TASK -----\n\n{prompt}"


def build_gemini_argv(
    *,
    prompt: str,
    binary: str = DEFAULT_BINARY,
    model: str = "",
    resume_session: str = "",
    approval_mode: str = DEFAULT_APPROVAL_MODE,
) -> list[str]:
    """Build the headless Gemini command line for one turn.

    Every flag is verified against ``gemini --help`` (Gemini CLI 0.47.0): ``-p/--prompt``
    is documented as "Run in non-interactive (headless) mode with the given prompt",
    ``--output-format`` offers ``stream-json``, ``--approval-mode`` offers the read-only
    ``plan``, ``-m/--model`` selects the model, and ``-r/--resume`` is documented as
    "Resume a previous session. Use 'latest' for most recent or index number".

    Parameters
    ----------
    prompt : str
        The turn's full prompt (context already folded in), passed via ``--prompt``.
    binary : str, optional
        The Gemini executable name or path.
    model : str, optional
        Model id for ``--model``; omitted when empty so the provider default applies.
    resume_session : str, optional
        Session selector for ``--resume``; omitted when empty to start fresh. The CLI
        documents ``latest`` and index values; whether a session UUID resolves too is
        unverified until a live capture exists.
    approval_mode : str, optional
        Gemini approval mode; defaults to ``plan`` (read-only).

    Returns
    -------
    list[str]
        The argv, always requesting ``stream-json`` output and a read-only approval mode.
    """
    argv = [
        binary,
        "--prompt",
        prompt,
        "--output-format",
        "stream-json",
        "--approval-mode",
        approval_mode,
    ]
    if model:
        argv += ["--model", model]
    if resume_session:
        argv += ["--resume", resume_session]
    return argv


class GeminiParticipant:
    """A Gemini CLI session driven headlessly as a uniform bus participant.

    Built against the installed 0.47.0 bundle-source event shape; turns are refused at
    the operator surface until
    :data:`~synapse_channel.participants.gemini_stream.GEMINI_SCHEMA_VERIFIED` is true
    (see the module note). Parameters mirror the other headless drivers.

    Parameters
    ----------
    identity : str
        The participant's bus identity.
    model : str, optional
        Model id passed to every turn; empty uses the provider default.
    binary : str, optional
        Gemini executable name or path.
    runner : CommandRunner, optional
        Subprocess runner; injectable so tests drive turns with a fake, never a real
        model call.
    timeout : float, optional
        Per-turn wall-clock ceiling, in seconds.
    approval_mode : str, optional
        Gemini approval mode for each turn; defaults to read-only ``plan``.
    """

    def __init__(
        self,
        identity: str,
        *,
        model: str = "",
        binary: str = DEFAULT_BINARY,
        runner: CommandRunner = subprocess.run,
        timeout: float = DEFAULT_TIMEOUT,
        approval_mode: str = DEFAULT_APPROVAL_MODE,
    ) -> None:
        self._identity = identity
        self._model = model
        self._binary = binary
        self._runner = runner
        self._timeout = timeout
        self._approval_mode = approval_mode

    @property
    def identity(self) -> str:
        """Return the participant's bus identity."""
        return self._identity

    @property
    def channel(self) -> ParticipantChannel:
        """Return :attr:`ParticipantChannel.HEADLESS`."""
        return ParticipantChannel.HEADLESS

    def health(self) -> ParticipantHealth:
        """Report whether the Gemini binary resolves on ``PATH``.

        Returns
        -------
        ParticipantHealth
            ``available`` is true when the configured binary is found. Turn enablement
            is separate: ``synapse ask --provider gemini`` is gated on
            :data:`~synapse_channel.participants.gemini_stream.GEMINI_SCHEMA_VERIFIED`,
            and OAuth-personal accounts additionally fail CLI setup with
            ``IneligibleTierError`` regardless of this probe.
        """
        resolved = shutil.which(self._binary)
        return ParticipantHealth(
            identity=self._identity,
            channel=ParticipantChannel.HEADLESS,
            available=resolved is not None,
            detail=f"gemini binary at {resolved}"
            if resolved is not None
            else f"gemini binary {self._binary!r} not found on PATH",
        )

    def run_turn(self, request: TurnRequest) -> TurnResult:
        """Run one turn synchronously and return its typed result.

        Parameters
        ----------
        request : TurnRequest
            The turn to run. Its ``context`` is folded into the prompt via
            :func:`compose_gemini_prompt`; its ``resume_session`` continues a prior
            session when set.

        Returns
        -------
        TurnResult
            The parsed outcome, or an error result when the provider could not be run.
        """
        argv = build_gemini_argv(
            prompt=compose_gemini_prompt(request.context, request.prompt),
            binary=self._binary,
            model=self._model,
            resume_session=request.resume_session,
            approval_mode=self._approval_mode,
        )
        try:
            completed = self._runner(
                argv,
                capture_output=True,
                text=True,
                check=False,
                timeout=self._timeout,
                input="",
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
        outcome = parse_gemini_stream((completed.stdout or "").splitlines())
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
