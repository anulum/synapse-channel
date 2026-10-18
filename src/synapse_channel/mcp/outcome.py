# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — MCP scoped execution observations
"""Preserve legacy tool text while carrying explicit machine-readable outcomes."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from contextvars import ContextVar

from synapse_channel.execution_outcome import ExecutionOutcome, OutcomeState

Sender = Callable[[], Awaitable[None]]

MCP_OUTCOME_ID: ContextVar[str] = ContextVar("synapse_mcp_outcome_id", default="")
"""Observation identity for this caller; never an authority grant or replay instruction."""


class OutcomeText(str):
    """A compatible text result with a separate execution observation."""

    outcome: ExecutionOutcome

    def __new__(cls, text: str, outcome: ExecutionOutcome) -> OutcomeText:
        """Retain the original string value and attach its scoped observation."""
        result = super().__new__(cls, text)
        result.outcome = outcome
        return result


class McpOutcomeFailure(ValueError):
    """A transport failure whose mutation outcome must not be guessed or replayed."""

    def __init__(self, outcome: ExecutionOutcome) -> None:
        """Keep a fixed caller-safe error and its exact operation observation."""
        message = (
            "hub connection unavailable"
            if outcome.state is OutcomeState.NOT_STARTED
            else "operation failed; outcome unknown; inquire before retrying"
        )
        super().__init__(message)
        self.outcome = outcome


class McpMutation:
    """Identify one exchange independently of the shared bridge's concurrent calls."""

    def __init__(self) -> None:
        """Allocate an opaque id before validation or sending begins."""
        self.operation_id = MCP_OUTCOME_ID.get() or "mcp-" + uuid.uuid4().hex

    def result(
        self,
        text: str,
        state: OutcomeState,
        observation: str,
        *,
        external_effect: OutcomeState = OutcomeState.UNKNOWN,
    ) -> OutcomeText:
        """Return unchanged text and an explicitly scoped machine-readable result."""
        return OutcomeText(
            text,
            ExecutionOutcome(
                self.operation_id,
                "mcp_exchange",
                state,
                observation,
                external_effect=external_effect,
            ),
        )

    def not_started(self, text: str) -> OutcomeText:
        """Report a validation refusal before any exchange or external effect."""
        return self.result(
            text,
            OutcomeState.NOT_STARTED,
            "request_not_invoked",
            external_effect=OutcomeState.NOT_STARTED,
        )

    def unknown(self, text: str) -> OutcomeText:
        """Keep an unanswered request unknown without suggesting mutation replay."""
        return self.result(text, OutcomeState.UNKNOWN, "exchange_unconfirmed")

    def finished(
        self,
        text: str,
        observation: str = "matching_hub_reply",
        *,
        external_effect: OutcomeState = OutcomeState.UNKNOWN,
    ) -> OutcomeText:
        """Confirm the reply exchange without inferring downstream business effects."""
        return self.result(
            text, OutcomeState.CONFIRMED_DONE, observation, external_effect=external_effect
        )

    def sender(self, send: Sender) -> Sender:
        """Wrap a single invocation without retrying after a possible send."""

        async def invoke() -> None:
            """Classify send exceptions conservatively and preserve their local cause."""
            try:
                await send()
            except Exception as exc:
                outcome = ExecutionOutcome(
                    self.operation_id,
                    "mcp_exchange",
                    OutcomeState.UNKNOWN,
                    "send_unconfirmed",
                )
                raise McpOutcomeFailure(outcome) from exc

        return invoke


def local_result(action: Callable[[], str]) -> OutcomeText:
    """Observe a local task-store call without inferring model or business success."""
    operation_id = "app-" + uuid.uuid4().hex
    try:
        text = action()
    except Exception as exc:
        raise McpOutcomeFailure(
            ExecutionOutcome(
                operation_id, "app_task_store", OutcomeState.UNKNOWN, "store_call_unconfirmed"
            )
        ) from exc
    return OutcomeText(
        text,
        ExecutionOutcome(
            operation_id, "app_task_store", OutcomeState.CONFIRMED_DONE, "store_call_returned"
        ),
    )
