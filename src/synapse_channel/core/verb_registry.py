# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — immutable declarations for hub verb families
"""Describe verbs beside their handlers and validate their collected registry.

This module imports neither handlers nor enforcement. The handler package owns
collection; consumers derive routing and guards from its validated declarations.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from synapse_channel.core.acl import Target
from synapse_channel.core.protocol import WIRE_PROTOCOL_VERSION

if TYPE_CHECKING:
    from synapse_channel.core.hub import SynapseHub

Handler = Callable[["SynapseHub", str, dict[str, Any], Any], Awaitable[None]]
"""Concrete hub callable that checks each structural handler's conformance."""

AccessMapper = Callable[[dict[str, Any]], list[tuple[str, Target]]]
"""Resolve the accesses required by the actual fields a handler consumes."""


@dataclass(frozen=True)
class VerbSpec:
    """One handler's request aliases, wire metadata and independent guards.

    ``mutates`` describes state changes, including acknowledgement watermarks.
    ``replay_protected`` selects the existing idempotency replay boundary.
    ``mutation_guarded`` preserves the legacy shared ACL/journal guard: it also
    includes attachment reads, while history reads use only ``accesses``.
    These dispositions are independent; registry construction does not silently
    broaden a verb's existing security policy.
    """

    request_types: tuple[str, ...]
    """Inbound wire types served by this handler, including resource aliases."""
    handler: Handler
    """Live coroutine invoked with the concrete hub and resolved sender."""
    reply_types: tuple[str, ...]
    """Family-specific replies and notifications; generic errors are implicit."""
    mutates: bool
    """Whether the handler can change authoritative state or durable metadata."""
    replay_protected: bool
    """Whether an actor/type/key duplicate is checked before dispatch."""
    mutation_guarded: bool
    """Whether the legacy ACL/journal mutation guard includes this request."""
    accesses: AccessMapper | None
    """ACL mapping, or None when the central ACL gate has no mapping."""
    event_kinds: tuple[str, ...]
    """Durable events this verb may emit, excluding generic operation records."""
    minimum_wire_version: int
    """Wire introduction floor; descriptive, not a new server-side gate."""
    commands: tuple[str, ...]
    """Existing CLI command paths serving this operation, empty for peer frames."""


def build_registry(families: Iterable[Iterable[VerbSpec]]) -> Mapping[str, VerbSpec]:
    """Collect immutable specs, refusing duplicate or incomplete registration.

    Raise ValueError before a hub can route when a request alias collides, a
    guarded verb lacks an ACL mapper, or its declared wire floor is invalid.
    The returned mapping preserves declared order and cannot be modified.
    """
    registry: dict[str, VerbSpec] = {}
    for family in families:
        for spec in family:
            if not spec.request_types or any(not name for name in spec.request_types):
                raise ValueError("verb spec requires non-empty request types")
            if spec.mutation_guarded and spec.accesses is None:
                raise ValueError("guarded verb spec requires an ACL mapping")
            if (
                type(spec.minimum_wire_version) is not int
                or not 1 <= spec.minimum_wire_version <= WIRE_PROTOCOL_VERSION
            ):
                raise ValueError("verb spec wire floor is outside the supported vocabulary")
            for name in spec.request_types:
                if name in registry:
                    raise ValueError(f"duplicate verb registration: {name}")
                registry[name] = spec
    return MappingProxyType(registry)
