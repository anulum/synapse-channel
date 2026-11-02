# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — MCP Git claims accept only string paths
"""A non-string path in an MCP Git claim is refused before any Git command runs."""

from __future__ import annotations

from typing import cast

import pytest

from synapse_channel.mcp.git_claim import McpGitClaimError, resolve_mcp_git_claim_scope


@pytest.mark.parametrize("bad", [7, None, b"a.py", ["a.py"]])
def test_non_string_paths_are_refused_without_running_git(bad: object) -> None:
    """Mixed path types are refused as a whole; Git is never consulted."""
    paths = cast("list[str]", ["src/a.py", bad])
    with pytest.raises(McpGitClaimError, match="^MCP Git claim paths must be strings\\.$"):
        resolve_mcp_git_claim_scope(
            paths,
            base="main",
            auto_release_on="manual",
            runner=lambda arguments: pytest.fail(f"git ran for a refused claim: {arguments}"),
        )
