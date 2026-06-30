# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — headless Codex CLI participant driver
"""Drive a Codex CLI session headlessly as a bus participant.

The second concrete :class:`~synapse_channel.participants.participant.Participant`, alongside
the Claude driver, on the ``HEADLESS`` channel: the bus owns the invocation, spawning
``codex exec --json`` (or ``codex exec resume <id>`` to continue a session) and reading its
JSONL event stream. Codex runs model-generated shell commands inside a sandbox; for a
reasoning participant the default policy is ``read-only`` so a turn cannot modify the
workspace.

Two contract differences from the Claude driver, both handled here and worth stating:

- **No system-prompt channel.** Codex has no ``--append-system-prompt`` equivalent, so the
  shared bus context (role, ground rules, and any fenced peer contribution) is prepended to
  the turn's prompt under a clear separator. The peer-injection fence still labels peer text
  as data, so this does not weaken the boundary, but the context travels in the prompt rather
  than a separate system channel.
- **No reported cost.** Codex emits token usage but not a monetary cost, so every Codex
  :class:`~synapse_channel.participants.envelope.TurnResult` carries ``cost_usd == 0.0`` and a
  conversation's cost budget cannot bound a Codex turn — only the round cap can.

As with the Claude driver, the heavy logic is a synchronous, dependency-injected
:meth:`CodexParticipant.run_turn` (hermetically testable with a fake runner) and the async
:meth:`CodexParticipant.take_turn` is a thin ``asyncio.to_thread`` wrapper. A missing binary,
a non-zero exit, or a timeout becomes an error result, never a raised exception.
"""

from __future__ import annotations

import asyncio
import shutil

# The Codex CLI is this module's controlled subprocess boundary; argv is built from typed
# fields and never from a shell string.
import subprocess  # nosec B404
from collections.abc import Sequence
from typing import Protocol

from synapse_channel.participants.codex_stream import parse_codex_stream
from synapse_channel.participants.envelope import (
    TurnRequest,
    TurnResult,
    build_turn_result,
    error_turn_result,
)
from synapse_channel.participants.participant import (
    ParticipantChannel,
    ParticipantHealth,
)

DEFAULT_BINARY = "codex"
"""Default Codex executable name resolved on ``PATH``."""

DEFAULT_TIMEOUT = 600.0
"""Default wall-clock ceiling, in seconds, for one headless turn."""

DEFAULT_SANDBOX = "read-only"
"""Default Codex sandbox policy; a reasoning participant never needs to write the workspace."""


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


def compose_codex_prompt(context: str, prompt: str) -> str:
    """Combine shared context and the turn prompt into one Codex prompt.

    Codex has no separate system channel, so the context is prepended to the prompt under a
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


def build_codex_argv(
    *,
    prompt: str,
    binary: str = DEFAULT_BINARY,
    model: str = "",
    resume_session: str = "",
    sandbox: str = DEFAULT_SANDBOX,
    persist_session: bool = False,
) -> list[str]:
    """Build the headless Codex command line for one turn.

    Parameters
    ----------
    prompt : str
        The fully composed prompt (see :func:`compose_codex_prompt`), passed positionally.
    binary : str, optional
        The Codex executable name or path.
    model : str, optional
        Model id for ``--model``; omitted when empty so the provider default applies.
    resume_session : str, optional
        Session id to resume via ``codex exec resume <id>``. When set, the resumed session
        keeps its original sandbox policy (resume takes no ``--sandbox``).
    sandbox : str, optional
        Sandbox policy for a fresh turn (``read-only`` / ``workspace-write`` /
        ``danger-full-access``). Ignored when resuming.
    persist_session : bool, optional
        Keep the session on disk. Defaults to false, adding ``--ephemeral`` for a clean
        one-shot turn; a continuity seat sets this true so a later turn can resume.

    Returns
    -------
    list[str]
        The argv, always requesting JSONL output with ``--json`` and skipping the git-repo
        check so a participant can run outside a repository.
    """
    if resume_session:
        argv = [binary, "exec", "resume", "--json", "--skip-git-repo-check"]
        if model:
            argv += ["--model", model]
        if not persist_session:
            argv.append("--ephemeral")
        argv += [resume_session, prompt]
        return argv

    argv = [binary, "exec", "--json", "--skip-git-repo-check", "--sandbox", sandbox]
    if model:
        argv += ["--model", model]
    if not persist_session:
        argv.append("--ephemeral")
    argv.append(prompt)
    return argv


class CodexParticipant:
    """A Codex CLI session driven headlessly as a uniform bus participant.

    Parameters
    ----------
    identity : str
        The participant's bus identity.
    model : str, optional
        Model id passed to every turn; empty uses the provider default.
    binary : str, optional
        Codex executable name or path.
    runner : CommandRunner, optional
        Subprocess runner; injectable so tests drive turns with a fake, never a real model call.
    timeout : float, optional
        Per-turn wall-clock ceiling, in seconds.
    sandbox : str, optional
        Sandbox policy for fresh turns (see :func:`build_codex_argv`).
    persist_session : bool, optional
        Whether fresh turns keep their session on disk (required for later resumption).
    """

    def __init__(
        self,
        identity: str,
        *,
        model: str = "",
        binary: str = DEFAULT_BINARY,
        runner: CommandRunner = subprocess.run,
        timeout: float = DEFAULT_TIMEOUT,
        sandbox: str = DEFAULT_SANDBOX,
        persist_session: bool = False,
    ) -> None:
        self._identity = identity
        self._model = model
        self._binary = binary
        self._runner = runner
        self._timeout = timeout
        self._sandbox = sandbox
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
        """Report whether the Codex binary resolves on ``PATH``.

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
            detail=f"codex binary at {resolved}"
            if resolved is not None
            else f"codex binary {self._binary!r} not found on PATH",
        )

    def run_turn(self, request: TurnRequest) -> TurnResult:
        """Run one turn synchronously and return its typed result.

        Parameters
        ----------
        request : TurnRequest
            The turn to run. Its ``context`` is prepended to the prompt (Codex has no system
            channel); its ``resume_session`` continues a prior session when set.

        Returns
        -------
        TurnResult
            The parsed outcome, or an error result when the provider could not be run.
        """
        argv = build_codex_argv(
            prompt=compose_codex_prompt(request.context, request.prompt),
            binary=self._binary,
            model=self._model,
            resume_session=request.resume_session,
            sandbox=self._sandbox,
            persist_session=self._persist_session,
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
        outcome = parse_codex_stream((completed.stdout or "").splitlines())
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
