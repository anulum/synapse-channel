# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — provider-neutral headless subprocess execution kernel
"""Common execution semantics for bus-owned headless provider processes.

Provider drivers keep their argv construction, context placement, resume rules,
and output parser.  This kernel owns only the behavior that must be identical:
identity/channel projection, binary health, bounded subprocess execution,
process-failure conversion, typed result construction, and off-loop dispatch.
OpenCode is deliberately excluded because its attach/auth/stream contract is a
separate execution model rather than one of these stateless command turns.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess  # nosec B404
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from synapse_channel.participants.envelope import (
    TurnRequest,
    TurnResult,
    build_turn_result,
    error_turn_result,
    stamp_model,
)
from synapse_channel.participants.participant import ParticipantChannel, ParticipantHealth
from synapse_channel.participants.process_error import (
    format_process_failure,
    format_process_start_failure,
)
from synapse_channel.participants.stream_json import StreamOutcome

CommandRunner = Callable[..., subprocess.CompletedProcess[str]]
"""Callable compatible with :func:`subprocess.run` for injectable tests."""


CompletedParser = Callable[[subprocess.CompletedProcess[str]], StreamOutcome]
"""Provider-owned adapter from a completed process to one stream outcome."""


@dataclass(frozen=True)
class HeadlessExecutionKernel:
    """Provider-neutral process boundary for one headless participant."""

    identity: str
    provider: str
    model: str
    binary: str
    runner: CommandRunner
    timeout: float
    timeout_subject: str = "headless"
    health_available_suffix: str = ""

    @property
    def channel(self) -> ParticipantChannel:
        """Return the shared headless participant channel."""
        return ParticipantChannel.HEADLESS

    def health(self) -> ParticipantHealth:
        """Report whether the configured provider binary resolves on ``PATH``."""
        resolved = shutil.which(self.binary)
        return ParticipantHealth(
            identity=self.identity,
            channel=self.channel,
            available=resolved is not None,
            detail=(
                f"{self.provider} binary at {resolved}{self.health_available_suffix}"
                if resolved is not None
                else f"{self.provider} binary {self.binary!r} not found on PATH"
            ),
        )

    def run_turn(
        self,
        request: TurnRequest,
        *,
        argv: Sequence[str],
        parser: CompletedParser,
        empty_stdin: bool,
    ) -> TurnResult:
        """Run one provider command and convert every process outcome to a turn result."""
        kwargs: dict[str, object] = {
            "capture_output": True,
            "text": True,
            "check": False,
            "timeout": self.timeout,
        }
        if empty_stdin:
            kwargs["input"] = ""
        try:
            completed = self.runner(argv, **kwargs)
        except subprocess.TimeoutExpired:
            return error_turn_result(
                participant=self.identity,
                channel=self.channel,
                request=request,
                reason=f"{self.timeout_subject} turn exceeded {self.timeout:g}s timeout",
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return error_turn_result(
                participant=self.identity,
                channel=self.channel,
                request=request,
                reason=format_process_start_failure(binary=self.binary, error=exc),
            )
        outcome = parser(completed)
        if completed.returncode != 0 and outcome.answer == "":
            return error_turn_result(
                participant=self.identity,
                channel=self.channel,
                request=request,
                reason=format_process_failure(
                    provider=self.provider,
                    binary=self.binary,
                    returncode=completed.returncode,
                    stderr=completed.stderr or "",
                ),
            )
        return build_turn_result(
            participant=self.identity,
            channel=self.channel,
            request=request,
            outcome=outcome,
        )

    async def take_turn(
        self,
        run_turn: Callable[[TurnRequest], TurnResult],
        request: TurnRequest,
    ) -> TurnResult:
        """Execute the blocking provider turn in a worker thread and stamp its model."""
        result = await asyncio.to_thread(run_turn, request)
        return stamp_model(result, self.model)
