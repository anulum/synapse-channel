# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Model Context Protocol compatibility imports
"""Compatibility import surface for the Model Context Protocol face.

The implementation lives in focused internal modules:

* :mod:`synapse_channel.mcp.bridge` translates MCP-facing operations to hub verbs.
* :mod:`synapse_channel.mcp.registration` registers FastMCP tools and resources.
* :mod:`synapse_channel.mcp.stdio` owns the stdio lifecycle.

Importing from :mod:`synapse_channel.mcp.server` remains supported for the CLI,
tests, and downstream callers.
"""

from __future__ import annotations

from synapse_channel.mcp.bridge import (
    DEFAULT_BRIDGE_NAME,
    DEFAULT_REQUEST_TIMEOUT,
    AgentFactory,
    Matcher,
    Sender,
    SynapseHubBridge,
)
from synapse_channel.mcp.registration import MCP_EXTRA_HINT, _require_fastmcp, build_mcp_server
from synapse_channel.mcp.stdio import serve_stdio

__all__ = [
    "DEFAULT_BRIDGE_NAME",
    "DEFAULT_REQUEST_TIMEOUT",
    "MCP_EXTRA_HINT",
    "AgentFactory",
    "Matcher",
    "Sender",
    "SynapseHubBridge",
    "_require_fastmcp",
    "build_mcp_server",
    "serve_stdio",
]
