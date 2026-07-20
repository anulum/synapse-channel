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

# The Ollama CLI is this module's controlled subprocess boundary; argv is built from typed
# fields and never from a shell string.
import subprocess  # nosec B404

from synapse_channel.participants.envelope import TurnRequest, TurnResult
from synapse_channel.participants.headless_kernel import (
    CommandRunner,
    HeadlessExecutionKernel,
)
from synapse_channel.participants.ollama_output import parse_ollama_output
from synapse_channel.participants.participant import ParticipantChannel, ParticipantHealth

DEFAULT_BINARY = "ollama"
"""Default Ollama executable name resolved on ``PATH``."""

DEFAULT_TIMEOUT = 600.0
"""Default wall-clock ceiling, in seconds, for one local turn."""


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
        self._kernel = HeadlessExecutionKernel(
            identity=identity,
            provider="ollama",
            model=model,
            binary=binary,
            runner=runner,
            timeout=timeout,
            timeout_subject="local",
            health_available_suffix=f" (model {model})",
        )
        self._hide_thinking = hide_thinking

    @property
    def identity(self) -> str:
        """Return the participant's bus identity."""
        return self._kernel.identity

    @property
    def channel(self) -> ParticipantChannel:
        """Return :attr:`ParticipantChannel.HEADLESS`."""
        return self._kernel.channel

    def health(self) -> ParticipantHealth:
        """Report whether the Ollama binary resolves on ``PATH``.

        Returns
        -------
        ParticipantHealth
            ``available`` is true when the configured binary is found. This does not probe
            whether the model is pulled; a missing model surfaces as an error turn instead.
        """
        return self._kernel.health()

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
            model=self._kernel.model,
            binary=self._kernel.binary,
            hide_thinking=self._hide_thinking,
        )
        return self._kernel.run_turn(
            request=request,
            argv=argv,
            parser=lambda completed: parse_ollama_output(completed.stdout or ""),
            empty_stdin=True,
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
        return await self._kernel.take_turn(self.run_turn, request)
