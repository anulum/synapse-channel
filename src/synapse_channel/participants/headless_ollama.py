# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — headless Ollama participant driver
"""Drive a local Ollama model headlessly as a bus participant.

A fourth concrete :class:`~synapse_channel.participants.participant.Participant`, alongside the
Claude, Codex, and Kimi drivers, on the ``HEADLESS`` channel: the bus owns the invocation,
spawning ``ollama run <model> <prompt>`` and reading the model's plain-text reply. It is the
one provider that runs **entirely locally** — free, offline, and with no terms-of-service or
account gate — which makes it a dependable participant for tests and offline fan-out.

Three contract differences from the Claude driver, all handled here and worth stating:

- **A model name is mandatory.** ``ollama run`` always names the model to load, so the
  participant requires a model rather than falling back to a provider default.
- **No system-prompt channel and no session.** Ollama's ``run`` mode has neither, so the
  shared bus context (including any fenced peer contribution) is prepended to the prompt under
  a separator, and the participant carries no resume token — continuity for an Ollama seat
  comes from the conversation's fenced context, not provider-side memory. A ``resume_session``
  on the request is accepted and ignored, so the participant still composes with a
  :class:`~synapse_channel.participants.continuity.ContinuitySeat` (which simply has nothing to
  thread).
- **No reported cost.** A local turn has no monetary cost, so every Ollama
  :class:`~synapse_channel.participants.envelope.TurnResult` carries ``cost_usd == 0.0``.

As with the other headless drivers, the heavy logic is a synchronous, dependency-injected
:meth:`OllamaParticipant.run_turn` (hermetically testable with a fake runner) and the async
:meth:`OllamaParticipant.take_turn` is a thin ``asyncio.to_thread`` wrapper. A missing binary,
a non-zero exit with no answer, or a timeout becomes an error result, never a raised exception.
"""

from __future__ import annotations

import asyncio
import shutil

# The Ollama CLI is this module's controlled subprocess boundary; argv is built from typed
# fields and never from a shell string.
import subprocess  # nosec B404
from collections.abc import Sequence
from typing import Protocol

from synapse_channel.participants.envelope import (
    TurnRequest,
    TurnResult,
    build_turn_result,
    error_turn_result,
)
from synapse_channel.participants.ollama_output import parse_ollama_output
from synapse_channel.participants.participant import (
    ParticipantChannel,
    ParticipantHealth,
)

DEFAULT_BINARY = "ollama"
"""Default Ollama executable name resolved on ``PATH``."""

DEFAULT_TIMEOUT = 600.0
"""Default wall-clock ceiling, in seconds, for one local turn."""


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


def compose_ollama_prompt(context: str, prompt: str) -> str:
    """Combine shared context and the turn prompt into one Ollama prompt.

    Ollama's ``run`` mode has no separate system channel, so the context is prepended to the
    prompt under a separator. When there is no context the prompt is returned unchanged.

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


def build_ollama_argv(
    *,
    prompt: str,
    model: str,
    binary: str = DEFAULT_BINARY,
    hide_thinking: bool = True,
) -> list[str]:
    """Build the headless Ollama command line for one turn.

    Parameters
    ----------
    prompt : str
        The fully composed prompt (see :func:`compose_ollama_prompt`), passed positionally.
    model : str
        The Ollama model to load (e.g. ``"gemma3:1b"``); required, as ``ollama run`` always
        names a model.
    binary : str, optional
        The Ollama executable name or path.
    hide_thinking : bool, optional
        Pass ``--hidethinking`` so a thinking-capable model's reasoning does not pollute the
        reply; harmless on models without a thinking mode. True by default.

    Returns
    -------
    list[str]
        The argv for ``ollama run <model> [--hidethinking] <prompt>``.
    """
    argv = [binary, "run", model]
    if hide_thinking:
        argv.append("--hidethinking")
    argv.append(prompt)
    return argv


class OllamaParticipant:
    """A local Ollama model driven headlessly as a uniform bus participant.

    Parameters
    ----------
    identity : str
        The participant's bus identity.
    model : str
        The Ollama model to load for every turn (required).
    binary : str, optional
        Ollama executable name or path.
    runner : CommandRunner, optional
        Subprocess runner; injectable so tests drive turns with a fake, never a real model
        call.
    timeout : float, optional
        Per-turn wall-clock ceiling, in seconds.
    hide_thinking : bool, optional
        Whether to pass ``--hidethinking`` (see :func:`build_ollama_argv`); true by default.
    """

    def __init__(
        self,
        identity: str,
        *,
        model: str,
        binary: str = DEFAULT_BINARY,
        runner: CommandRunner = subprocess.run,
        timeout: float = DEFAULT_TIMEOUT,
        hide_thinking: bool = True,
    ) -> None:
        self._identity = identity
        self._model = model
        self._binary = binary
        self._runner = runner
        self._timeout = timeout
        self._hide_thinking = hide_thinking

    @property
    def identity(self) -> str:
        """Return the participant's bus identity."""
        return self._identity

    @property
    def channel(self) -> ParticipantChannel:
        """Return :attr:`ParticipantChannel.HEADLESS`."""
        return ParticipantChannel.HEADLESS

    def health(self) -> ParticipantHealth:
        """Report whether the Ollama binary resolves on ``PATH``.

        Returns
        -------
        ParticipantHealth
            ``available`` is true when the configured binary is found. This does not probe
            whether the model is pulled; a missing model surfaces as an error turn instead.
        """
        resolved = shutil.which(self._binary)
        return ParticipantHealth(
            identity=self._identity,
            channel=ParticipantChannel.HEADLESS,
            available=resolved is not None,
            detail=f"ollama binary at {resolved} (model {self._model})"
            if resolved is not None
            else f"ollama binary {self._binary!r} not found on PATH",
        )

    def run_turn(self, request: TurnRequest) -> TurnResult:
        """Run one turn synchronously and return its typed result.

        Parameters
        ----------
        request : TurnRequest
            The turn to run. Its ``context`` is prepended to the prompt (Ollama has no system
            channel); its ``resume_session`` is ignored (the ``run`` CLI is stateless).

        Returns
        -------
        TurnResult
            The parsed outcome, or an error result when the provider could not be run.
        """
        argv = build_ollama_argv(
            prompt=compose_ollama_prompt(request.context, request.prompt),
            model=self._model,
            binary=self._binary,
            hide_thinking=self._hide_thinking,
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
                reason=f"local turn exceeded {self._timeout:g}s timeout",
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return error_turn_result(
                participant=self._identity,
                channel=ParticipantChannel.HEADLESS,
                request=request,
                reason=f"failed to run {self._binary!r}: {exc}",
            )
        outcome = parse_ollama_output(completed.stdout or "")
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
        return await asyncio.to_thread(self.run_turn, request)
