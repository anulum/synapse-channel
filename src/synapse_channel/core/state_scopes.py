# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — file-scope conflict scans for coordination state
"""File-scope conflict scanning over live claims."""

from __future__ import annotations

from collections.abc import Mapping

from synapse_channel.core.scoping import scopes_conflict
from synapse_channel.core.state_models import TaskClaim


def find_scope_conflict(
    claims: Mapping[str, TaskClaim],
    *,
    task: str,
    agent: str,
    worktree: str,
    paths: tuple[str, ...],
) -> tuple[str, str] | None:
    """Return the first other live claim whose file scope contends, if any."""
    for other_id, other in claims.items():
        if other_id == task or other.owner == agent:
            continue
        if scopes_conflict(worktree, paths, other.worktree, other.paths):
            return other_id, other.owner
    return None
