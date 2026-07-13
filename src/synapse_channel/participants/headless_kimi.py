# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — headless Kimi CLI participant driver
"""Drive a Kimi CLI session headlessly as a bus participant.

A third concrete :class:`~synapse_channel.participants.participant.Participant`, alongside the
Claude and Codex drivers, on the ``HEADLESS`` channel: the bus owns the invocation, spawning
``kimi --print --output-format stream-json`` (adding ``-r <id>`` to continue a session) and
reading its JSONL message stream.

Three contract differences from the Claude driver, all handled here and worth stating:

- **No system-prompt channel.** Kimi has no ``--append-system-prompt`` equivalent, so the
  shared bus context (role, ground rules, and any fenced peer contribution) is prepended to
  the turn's prompt under a clear separator, as the Codex driver does. The peer-injection
  fence still labels peer text as data, so this does not weaken the boundary.
- **Read-only by default.** Kimi's print mode auto-approves tool calls, so a reasoning
  participant runs in **plan mode** (``--plan``) by default: the turn can reason and reply but
  cannot modify the workspace, analogous to the Codex ``read-only`` sandbox.
- **No reported cost.** Kimi emits no monetary cost, so every Kimi
  :class:`~synapse_channel.participants.envelope.TurnResult` carries ``cost_usd == 0.0`` and a
  conversation's cost budget cannot bound a Kimi turn — only the round cap can.

Kimi persists sessions on disk by default and prints the resume token to stderr, so a
:class:`~synapse_channel.participants.continuity.ContinuitySeat` resumes a Kimi participant
without any extra persistence flag. As with the other headless drivers, the heavy logic is a
synchronous, dependency-injected :meth:`KimiParticipant.run_turn` (hermetically testable with
a fake runner) and the async :meth:`KimiParticipant.take_turn` is a thin
``asyncio.to_thread`` wrapper. A missing binary, a non-zero exit with no answer, or a timeout
becomes an error result, never a raised exception.
"""

from __future__ import annotations

import asyncio
import shutil

# The Kimi CLI is this module's controlled subprocess boundary; argv is built from typed
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
from synapse_channel.participants.kimi_stream import parse_kimi_stream
from synapse_channel.participants.participant import (
    ParticipantChannel,
    ParticipantHealth,
)
from synapse_channel.participants.process_error import (
    format_process_failure,
    format_process_start_failure,
)

DEFAULT_BINARY = "kimi"
"""Default Kimi executable name resolved on ``PATH``."""

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
        input: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Run ``args`` and return the completed process."""


def compose_kimi_prompt(context: str, prompt: str) -> str:
    """Combine shared context and the turn prompt into one Kimi prompt.

    Kimi has no separate system channel, so the context is prepended to the prompt under a
    separator. When there is no context the prompt is returned unchanged.

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


def build_kimi_argv(
    *,
    prompt: str,
    binary: str = DEFAULT_BINARY,
    model: str = "",
    resume_session: str = "",
    plan_mode: bool = True,
) -> list[str]:
    """Build the headless Kimi command line for one turn.

    Parameters
    ----------
    prompt : str
        The fully composed prompt (see :func:`compose_kimi_prompt`), passed via ``-p``.
    binary : str, optional
        The Kimi executable name or path.
    model : str, optional
        Model id for ``--model``; omitted when empty so the provider default applies.
    resume_session : str, optional
        Session id to resume via ``-r <id>``; omitted when empty to start fresh. Kimi
        persists sessions by default, so no extra flag is needed to make a turn resumable.
    plan_mode : bool, optional
        Run in plan mode (``--plan``) so the turn is read-only — the default for a reasoning
        participant. Set false only when the participant is meant to act on the workspace.

    Returns
    -------
    list[str]
        The argv, always requesting JSONL output with ``--print --output-format stream-json``.
    """
    argv = [binary, "--print", "--output-format", "stream-json"]
    if plan_mode:
        argv.append("--plan")
    if model:
        argv += ["--model", model]
    if resume_session:
        argv += ["-r", resume_session]
    argv += ["-p", prompt]
    return argv


class KimiParticipant:
    """A Kimi CLI session driven headlessly as a uniform bus participant.

    Parameters
    ----------
    identity : str
        The participant's bus identity.
    model : str, optional
        Model id passed to every turn; empty uses the provider default.
    binary : str, optional
        Kimi executable name or path.
    runner : CommandRunner, optional
        Subprocess runner; injectable so tests drive turns with a fake, never a real model
        call.
    timeout : float, optional
        Per-turn wall-clock ceiling, in seconds.
    plan_mode : bool, optional
        Whether turns run in read-only plan mode (see :func:`build_kimi_argv`); true by
        default for a reasoning participant.
    """

    def __init__(
        self,
        identity: str,
        *,
        model: str = "",
        binary: str = DEFAULT_BINARY,
        runner: CommandRunner = subprocess.run,
        timeout: float = DEFAULT_TIMEOUT,
        plan_mode: bool = True,
    ) -> None:
        self._identity = identity
        self._model = model
        self._binary = binary
        self._runner = runner
        self._timeout = timeout
        self._plan_mode = plan_mode

    @property
    def identity(self) -> str:
        """Return the participant's bus identity."""
        return self._identity

    @property
    def channel(self) -> ParticipantChannel:
        """Return :attr:`ParticipantChannel.HEADLESS`."""
        return ParticipantChannel.HEADLESS

    def health(self) -> ParticipantHealth:
        """Report whether the Kimi binary resolves on ``PATH``.

        Returns
        -------
        ParticipantHealth
            ``available`` is true when the configured binary is found.
        """
        resolved = shutil.which(self._binary)
        return ParticipantHealth(
            identity=self._identity,
            channel=ParticipantChannel.HEADLESS,
            available=resolved is not None,
            detail=f"kimi binary at {resolved}"
            if resolved is not None
            else f"kimi binary {self._binary!r} not found on PATH",
        )

    def run_turn(self, request: TurnRequest) -> TurnResult:
        """Run one turn synchronously and return its typed result.

        Parameters
        ----------
        request : TurnRequest
            The turn to run. Its ``context`` is prepended to the prompt (Kimi has no system
            channel); its ``resume_session`` continues a prior session when set.

        Returns
        -------
        TurnResult
            The parsed outcome, or an error result when the provider could not be run.
        """
        argv = build_kimi_argv(
            prompt=compose_kimi_prompt(request.context, request.prompt),
            binary=self._binary,
            model=self._model,
            resume_session=request.resume_session,
            plan_mode=self._plan_mode,
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
                reason=format_process_start_failure(binary=self._binary, error=exc),
            )
        outcome = parse_kimi_stream(
            (completed.stdout or "").splitlines(),
            stderr=completed.stderr or "",
        )
        if completed.returncode != 0 and outcome.answer == "":
            return error_turn_result(
                participant=self._identity,
                channel=ParticipantChannel.HEADLESS,
                request=request,
                reason=format_process_failure(
                    provider="kimi",
                    binary=self._binary,
                    returncode=completed.returncode,
                    stderr=completed.stderr or "",
                ),
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
