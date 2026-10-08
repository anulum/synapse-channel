# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — deferred callbacks for the external hub composition root
"""Bind collaborators to one hub without running callbacks during construction."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from synapse_channel.core.dark_seat import ClaimSource, TaskSource


@dataclass(frozen=True, kw_only=True)
class HubComponentCallbacks:
    """One target's deferred transport and state callbacks.

    The owner is an identity check, never a callable dependency. A graph built
    for one target cannot be installed into another. Collaborator constructors
    retain these callbacks; only runtime operations may invoke them.
    """

    owner: object
    system: Callable[..., dict[str, Any]]
    send_json: Callable[[Any, dict[str, Any]], Awaitable[None]]
    online_agents: Callable[[], list[str]]
    handle_message: Callable[[str | bytes, Any], Awaitable[None]]
    broadcast: Callable[[dict[str, Any]], Awaitable[object]]
    broadcast_presence: Callable[[str, str | None], Awaitable[None]]
    drop_waits: Callable[[str], None]
    agent_left: Callable[[str], None]
    claims: ClaimSource
    tasks: TaskSource
