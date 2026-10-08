# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — structural context shared by hub handler families
"""Internal static capabilities shared by message handler families.

No runtime wrapper, registration, import dependency or public export is added.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Any, Protocol

    from synapse_channel.core.atomic_operations import AtomicExecution, OperationDraft
    from synapse_channel.core.hub_clients import HubClientRegistry
    from synapse_channel.core.hub_counters import HubCounters
    from synapse_channel.core.persistence import EventStore
    from synapse_channel.core.state import SynapseState
    from synapse_channel.core.state_transaction import SerializedStateMutationActor

    class HandlerContext(Protocol):
        """State, journal and transport capabilities shared by hub handlers.

        This internal structural contract is checked at dispatch registration.
        References are read-only; their owning collaborators still perform mutations.
        Methods remain late-bound on the concrete hub for each invocation.
        """

        async def broadcast(self, data: dict[str, Any]) -> frozenset[str]:
            """Fan out with bounded writes, returning successful bound socket names."""
            ...

        @property
        def clients(self) -> HubClientRegistry:
            """Return the clients used by this handler family."""
            ...

        @property
        def counters(self) -> HubCounters:
            """Return the counters used by this handler family."""
            ...

        @property
        def hub_id(self) -> str:
            """Return the hub id used by this handler family."""
            ...

        @property
        def journal(self) -> EventStore | None:
            """Return the journal used by this handler family."""
            ...

        def remember(self, data: dict[str, Any], response: dict[str, Any]) -> None:
            """Cache the response of an applied mutation under its idempotency key.

            Handler surface: the ledger guard owns the cache; a handler that applied
            a mutation outside the atomic-operation path records its response here.
            """
            ...

        async def run_atomic_operation(
            self,
            data: dict[str, Any],
            mutate: Callable[[Any], Any],
            prepare: Callable[[Any], OperationDraft | None],
            *,
            subject: Any | None = None,
            publish_candidate: Callable[[Any], None] | None = None,
            persist_uncommitted: Callable[[Any], None] | None = None,
            publish: Callable[[Any], None] | None = None,
        ) -> AtomicExecution | None:
            """Run a keyed journal-backed mutation through the atomic operation actor."""
            ...

        async def send_json(self, websocket: Any, data: dict[str, Any]) -> None:
            """Serialise and send one message to a single socket (handler surface)."""
            ...

        async def send_to_agent(self, agent: str, data: dict[str, Any]) -> bool:
            """Send to a named agent's socket; return whether the send succeeded."""
            ...

        async def settle_atomic_operation(self, data: dict[str, Any]) -> None:
            """Mark a committed evidence intent projected after successful transport."""
            ...

        @property
        def state(self) -> SynapseState:
            """Return the state used by this handler family."""
            ...

        @property
        def state_mutations(self) -> SerializedStateMutationActor:
            """Return the state mutations used by this handler family."""
            ...

        def system(self, payload: str, **extra: Any) -> dict[str, Any]:
            """Build a hub system message stamped with this hub's id."""
            ...
