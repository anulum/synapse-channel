# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — A2A bridge CLI compatibility surface
"""Compatibility exports for Agent2Agent bridge command surfaces."""

from __future__ import annotations

from synapse_channel.cli_a2a_card import _a2a_card, _cmd_a2a_card, _print_agent_card
from synapse_channel.cli_a2a_parsers import add_parsers
from synapse_channel.cli_a2a_serve import (
    _a2a_inbound_handler,
    _cmd_a2a_serve,
    _fetch_manifest,
)
from synapse_channel.cli_a2a_types import (
    A2ACardRunner,
    AsyncRunner,
    BridgeFactory,
    CardBuilder,
    ManifestFetcher,
    RuntimeFactory,
    ServerRunner,
    StoreFactory,
)

__all__ = [
    "A2ACardRunner",
    "AsyncRunner",
    "BridgeFactory",
    "CardBuilder",
    "ManifestFetcher",
    "RuntimeFactory",
    "ServerRunner",
    "StoreFactory",
    "_a2a_card",
    "_a2a_inbound_handler",
    "_cmd_a2a_card",
    "_cmd_a2a_serve",
    "_fetch_manifest",
    "_print_agent_card",
    "add_parsers",
]
