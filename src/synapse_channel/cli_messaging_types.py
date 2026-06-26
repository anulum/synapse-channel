# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — messaging CLI runtime type aliases
"""Shared type aliases for messaging CLI command modules."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from synapse_channel.client.agent import SynapseAgent

AgentFactory = Callable[..., SynapseAgent]
JitterFunction = Callable[[float, float], float]
ListenRunner = Callable[..., Coroutine[Any, Any, int]]
AsyncRunner = Callable[[Coroutine[Any, Any, int]], int]
