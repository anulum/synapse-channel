# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — A2A CLI runtime type aliases
"""Shared type aliases for the A2A CLI command modules."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

from synapse_channel.a2a_server import A2ABridge
from synapse_channel.a2a_store import A2ATaskStore

T = TypeVar("T")
AsyncRunner = Callable[[Coroutine[Any, Any, T]], T]
A2ACardRunner = Callable[..., Coroutine[Any, Any, int]]
ManifestFetcher = Callable[..., Coroutine[Any, Any, list[dict[str, Any]] | None]]
CardBuilder = Callable[..., dict[str, Any]]
RuntimeFactory = Callable[[Any], Any]
BridgeFactory = Callable[..., A2ABridge]
StoreFactory = Callable[..., A2ATaskStore]
ServerRunner = Callable[..., None]
