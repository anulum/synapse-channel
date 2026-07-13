# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — OpenCode acceptance fixture compatibility facade
"""Re-export the responsibility-split OpenCode real-process acceptance helpers."""

from fixtures.opencode.acp import acp_initialize
from fixtures.opencode.llm import ScriptedLlmServer
from fixtures.opencode.process import (
    OPENCODE_VERSION,
    TEST_MODEL,
    find_opencode,
    isolated_environment,
    run_opencode,
)
from fixtures.opencode.server import OpenCodeServer, running_opencode_server

__all__ = [
    "OPENCODE_VERSION",
    "TEST_MODEL",
    "OpenCodeServer",
    "ScriptedLlmServer",
    "acp_initialize",
    "find_opencode",
    "isolated_environment",
    "run_opencode",
    "running_opencode_server",
]
