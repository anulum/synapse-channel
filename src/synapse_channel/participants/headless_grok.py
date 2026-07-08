# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — headless Grok CLI participant driver
"""Drive a Grok CLI session headlessly as a bus participant.

.. warning::

   **Grok support is ready.** The driver is built and unit-tested. Prior workstation-level
   reliability issues with the Grok CLI were reported in June 2026 escalations (freezes and
   memory pressure on the target Linux machine). As of observed releases (0.2.91+), the binary
   is present at ``/home/anulum/.local/bin/grok`` (``grok --version`` reports stable) and is
   detected by ``synapse participant list``. The main remaining gate is schema verification:
   the streaming-json output was not captured from a real run (see
   :mod:`~synapse_channel.participants.grok_stream` and
   :data:`~synapse_channel.participants.grok_stream.GROK_SCHEMA_VERIFIED`). The argv is
   verified against ``grok --help``; the parsed event shape follows the assumed
   Claude-Code-family convention and should be re-verified against a current stable trace before
   fully enabling the gated smoke (triple-gated and currently skipped).

A fifth concrete :class:`~synapse_channel.participants.participant.Participant`, on the
``HEADLESS`` channel: the bus owns the invocation, spawning ``grok --single <prompt>
--output-format streaming-json`` (adding ``-r <id>`` to resume a session) and reading its event
stream. Grok is a Claude-Code-family CLI, so unlike Codex/Kimi/Ollama it has a real
system-prompt append (``--rules``), which carries the shared bus context — role, ground rules,
and any fenced peer contribution — without folding it into the user prompt, and it runs in
read-only plan mode (``--permission-mode plan``) so a reasoning turn cannot modify the
workspace.

As with the other headless drivers, the heavy logic is a synchronous, dependency-injected
:meth:`GrokParticipant.run_turn` (hermetically testable with a fake runner) and the async
:meth:`GrokParticipant.take_turn` is a thin ``asyncio.to_thread`` wrapper. A missing binary, a
non-zero exit with no answer, or a timeout becomes an error result, never a raised exception.
"""

from __future__ import annotations

import asyncio
import shutil

# The Grok CLI is this module's controlled subprocess boundary; argv is built from typed
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
from synapse_channel.participants.grok_stream import parse_grok_stream
from synapse_channel.participants.participant import (
    ParticipantChannel,
    ParticipantHealth,
)

DEFAULT_BINARY = "grok"
"""Default Grok executable name resolved on ``PATH``."""

DEFAULT_TIMEOUT = 600.0
"""Default wall-clock ceiling, in seconds, for one headless turn."""

DEFAULT_PERMISSION_MODE = "plan"
"""Default Grok permission mode; a reasoning participant never needs to write the workspace."""


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


def build_grok_argv(
    *,
    prompt: str,
    binary: str = DEFAULT_BINARY,
    model: str = "",
    rules: str = "",
    resume_session: str = "",
    permission_mode: str = DEFAULT_PERMISSION_MODE,
) -> list[str]:
    """Build the headless Grok command line for one turn.

    Every flag is verified against ``grok --help`` (Grok 0.2.64); the *output schema* the
    resulting stream carries is the part that is unverified (see the module warning).

    Parameters
    ----------
    prompt : str
        The turn's question, passed as the single-turn prompt via ``--single``.
    binary : str, optional
        The Grok executable name or path.
    model : str, optional
        Model id for ``--model``; omitted when empty so the provider default applies.
    rules : str, optional
        Shared bus context appended to the system prompt via ``--rules``; omitted when empty.
        This is Grok's system-prompt append, so peer-supplied text never arrives as the user
        prompt.
    resume_session : str, optional
        Session id to resume via ``--resume <id>``; omitted when empty to start fresh.
    permission_mode : str, optional
        Grok permission mode; defaults to ``plan`` (read-only).

    Returns
    -------
    list[str]
        The argv, always requesting ``streaming-json`` output and a read-only permission mode.
    """
    argv = [
        binary,
        "--single",
        prompt,
        "--output-format",
        "streaming-json",
        "--permission-mode",
        permission_mode,
    ]
    if model:
        argv += ["--model", model]
    if rules:
        argv += ["--rules", rules]
    if resume_session:
        argv += ["--resume", resume_session]
    return argv


class GrokParticipant:
    """A Grok CLI session driven headlessly as a uniform bus participant.

    Built for completeness; not run on this machine (see the module warning). Parameters mirror
    the other headless drivers.

    Parameters
    ----------
    identity : str
        The participant's bus identity.
    model : str, optional
        Model id passed to every turn; empty uses the provider default.
    binary : str, optional
        Grok executable name or path.
    runner : CommandRunner, optional
        Subprocess runner; injectable so tests drive turns with a fake, never a real model
        call.
    timeout : float, optional
        Per-turn wall-clock ceiling, in seconds.
    permission_mode : str, optional
        Grok permission mode for each turn; defaults to read-only ``plan``.
    """

    def __init__(
        self,
        identity: str,
        *,
        model: str = "",
        binary: str = DEFAULT_BINARY,
        runner: CommandRunner = subprocess.run,
        timeout: float = DEFAULT_TIMEOUT,
        permission_mode: str = DEFAULT_PERMISSION_MODE,
    ) -> None:
        self._identity = identity
        self._model = model
        self._binary = binary
        self._runner = runner
        self._timeout = timeout
        self._permission_mode = permission_mode

    @property
    def identity(self) -> str:
        """Return the participant's bus identity."""
        return self._identity

    @property
    def channel(self) -> ParticipantChannel:
        """Return :attr:`ParticipantChannel.HEADLESS`."""
        return ParticipantChannel.HEADLESS

    def health(self) -> ParticipantHealth:
        """Report whether the Grok binary resolves on ``PATH``.

        Returns
        -------
        ParticipantHealth
            ``available`` is true when the configured binary is found. The binary resolving
            says nothing about whether a turn will run: Grok participant real smoke
            is schema-gated, and prior CLI reliability issues are resolved.
        """
        resolved = shutil.which(self._binary)
        return ParticipantHealth(
            identity=self._identity,
            channel=ParticipantChannel.HEADLESS,
            available=resolved is not None,
            detail=f"grok binary at {resolved}"
            if resolved is not None
            else f"grok binary {self._binary!r} not found on PATH",
        )

    def run_turn(self, request: TurnRequest) -> TurnResult:
        """Run one turn synchronously and return its typed result.

        Parameters
        ----------
        request : TurnRequest
            The turn to run. Its ``context`` is appended to the system prompt via ``--rules``;
            its ``resume_session`` continues a prior session when set.

        Returns
        -------
        TurnResult
            The parsed outcome, or an error result when the provider could not be run.
        """
        argv = build_grok_argv(
            prompt=request.prompt,
            binary=self._binary,
            model=self._model,
            rules=request.context,
            resume_session=request.resume_session,
            permission_mode=self._permission_mode,
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
        outcome = parse_grok_stream((completed.stdout or "").splitlines())
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
