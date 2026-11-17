# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — headless Antigravity CLI (agy) participant
"""Drive an Antigravity CLI session headlessly as a bus participant.

.. note::

   **Antigravity CLI is a first-class headless provider.** It is the successor
   of Gemini CLI for personal Google accounts. Its ``stream-json`` envelope was
   captured from real turns of the installed ``agy`` 1.5.10; see
   :mod:`~synapse_channel.participants.agy_stream` and
   :data:`~synapse_channel.participants.agy_stream.AGY_SCHEMA_VERIFIED`. Turns
   are enabled while that flag is true.

A concrete :class:`~synapse_channel.participants.participant.Participant` on the
``HEADLESS`` channel: the bus owns the invocation, spawning ``agy --print
<prompt> --output-format stream-json --mode plan`` (adding
``--conversation=<id>`` to resume) and reading its event stream via
:func:`~synapse_channel.participants.agy_stream.parse_agy_stream`.

- **Context:** ``agy`` has no system-prompt append flag, so the shared bus
  context is prepended to the user prompt under an explicit separator, as for
  Gemini, Codex, Kimi and Ollama.
- **Mode:** turns request ``plan`` by default. The CLI reports that mode as
  ``request-review``. The driver never passes ``--dangerously-skip-permissions``.
- **Denied tools:** headless mode cannot ask for a permission. A tool that
  needs one, such as a shell command, is auto-denied, and a turn that ends
  without an answer is reported as an error naming the denied actions.

Effective permissions and external effects depend on the installed CLI, its
settings and the account, and need separate qualification. Prompt framing is
not proof of resistance to prompt injection.

As with the other headless drivers, the work is a synchronous,
dependency-injected :meth:`AgyParticipant.run_turn`, testable with a fake
runner, and :meth:`AgyParticipant.take_turn` is a thin ``asyncio.to_thread``
wrapper. A missing binary, a non-zero exit with no answer, or a timeout becomes
an error result, never a raised exception.
"""

from __future__ import annotations

from synapse_channel.participants.agy_stream import parse_agy_stream
from synapse_channel.participants.envelope import TurnRequest, TurnResult
from synapse_channel.participants.headless_gemini import compose_gemini_prompt
from synapse_channel.participants.headless_kernel import (
    DEFAULT_COMMAND_RUNNER,
    CommandRunner,
    HeadlessExecutionKernel,
)
from synapse_channel.participants.participant import ParticipantChannel, ParticipantHealth

DEFAULT_BINARY = "agy"
"""Default Antigravity CLI executable name resolved on ``PATH``."""

DEFAULT_TIMEOUT = 600.0
"""Default wall-clock ceiling, in seconds, for one headless turn."""

DEFAULT_MODE = "plan"
"""Default execution mode request; not an independent filesystem sandbox."""


def build_agy_argv(
    *,
    prompt: str,
    binary: str = DEFAULT_BINARY,
    model: str = "",
    conversation: str = "",
    mode: str = DEFAULT_MODE,
) -> list[str]:
    """Build the headless Antigravity CLI command line for one turn.

    Every flag is taken from ``agy --help`` (Antigravity CLI 1.5.10):
    ``--print`` runs "a single prompt non-interactively", ``--output-format``
    offers ``stream-json``, ``--mode`` offers ``plan``, ``--model`` selects the
    model, and ``--conversation`` resumes "a previous conversation by ID". The
    resume was captured with a conversation id from an earlier ``init`` event.

    Parameters
    ----------
    prompt : str
        The turn's full prompt, context already folded in, passed to ``--print``.
    binary : str, optional
        The ``agy`` executable name or path.
    model : str, optional
        Model for ``--model``; omitted when empty so the CLI's setting applies.
    conversation : str, optional
        Conversation id for ``--conversation``; omitted when empty to start a new
        conversation. It is joined with ``=`` so an id is never read as a flag.
    mode : str, optional
        Requested execution mode; defaults to ``plan``. Enforcement is the CLI's.

    Returns
    -------
    list[str]
        The argv, requesting ``stream-json`` output and the supplied mode.
    """
    argv = [binary, "--print", prompt, "--output-format", "stream-json", "--mode", mode]
    if model:
        argv += ["--model", model]
    if conversation:
        argv.append(f"--conversation={conversation}")
    return argv


class AgyParticipant:
    """An Antigravity CLI session driven headlessly as a uniform bus participant.

    Parameters
    ----------
    identity : str
        The participant's bus identity.
    model : str, optional
        Model passed to every turn; empty uses the CLI's configured model.
    binary : str, optional
        ``agy`` executable name or path.
    runner : CommandRunner, optional
        Subprocess runner, :func:`subprocess.run` by default; injectable so tests drive
        turns with a fake, never a real model call. The driver itself never starts a
        shell: every argument vector is fixed.
    timeout : float, optional
        Per-turn wall-clock ceiling, in seconds.
    mode : str, optional
        Requested execution mode for each turn; defaults to ``plan``.
    """

    def __init__(
        self,
        identity: str,
        *,
        model: str = "",
        binary: str = DEFAULT_BINARY,
        runner: CommandRunner = DEFAULT_COMMAND_RUNNER,
        timeout: float = DEFAULT_TIMEOUT,
        mode: str = DEFAULT_MODE,
    ) -> None:
        self._kernel = HeadlessExecutionKernel(
            identity=identity,
            provider="agy",
            model=model,
            binary=binary,
            runner=runner,
            timeout=timeout,
            health_available_suffix="; account entitlement not probed",
        )
        self._mode = mode

    @property
    def identity(self) -> str:
        """Return the participant's bus identity."""
        return self._kernel.identity

    @property
    def channel(self) -> ParticipantChannel:
        """Return :attr:`ParticipantChannel.HEADLESS`."""
        return self._kernel.channel

    def health(self) -> ParticipantHealth:
        """Report whether the ``agy`` binary resolves on ``PATH``.

        Returns
        -------
        ParticipantHealth
            ``available`` is true when the configured binary is found. The account's
            sign-in and quota are not probed and can fail a turn independently.
        """
        return self._kernel.health()

    def run_turn(self, request: TurnRequest) -> TurnResult:
        """Run one turn synchronously and return its typed result.

        Parameters
        ----------
        request : TurnRequest
            The turn to run. Its ``context`` is folded into the prompt; its
            ``resume_session`` continues that conversation when set.

        Returns
        -------
        TurnResult
            The parsed outcome, or an error result when the provider could not be run
            or produced no answer.
        """
        argv = build_agy_argv(
            prompt=compose_gemini_prompt(request.context, request.prompt),
            binary=self._kernel.binary,
            model=self._kernel.model,
            conversation=request.resume_session,
            mode=self._mode,
        )
        return self._kernel.run_turn(
            request=request,
            argv=argv,
            parser=lambda completed: parse_agy_stream((completed.stdout or "").splitlines()),
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
