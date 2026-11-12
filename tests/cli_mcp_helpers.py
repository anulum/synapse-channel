# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — parsed arguments for direct calls of the MCP command
"""Build the namespace ``synapse mcp`` parses, for tests that call its handler directly."""

from __future__ import annotations

import argparse
from typing import Any

from hub_e2e_helpers import _free_port
from synapse_channel.mcp.server import DEFAULT_REQUEST_TIMEOUT


def mcp_namespace(**overrides: Any) -> argparse.Namespace:
    """Return the command's arguments with a free local port and the given changes.

    Parameters
    ----------
    **overrides : Any
        Values that replace or extend the defaults, such as ``uri`` or ``name``.

    Returns
    -------
    argparse.Namespace
        The arguments, as the parser would hand them to the handler.
    """
    base: dict[str, Any] = {
        "uri": f"ws://localhost:{_free_port()}",
        "name": "bridge",
        "token": None,
        "token_file": None,
        "request_timeout": DEFAULT_REQUEST_TIMEOUT,
        "ready_timeout": 0.1,
    }
    base.update(overrides)
    return argparse.Namespace(**base)
