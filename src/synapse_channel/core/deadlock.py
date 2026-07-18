# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — wait-for cycle detection for hold-and-wait deadlock
"""Wait-for cycle detection over the hub's hold-and-wait graph.

Once claims are file-scoped, an agent can declare it is *waiting* for a region
another agent holds. That introduces classic resource-allocation deadlock: agent
A holds ``src/`` and waits for ``tests/`` while agent B holds ``tests/`` and waits
for ``src/`` — neither will ever proceed. The hub is the single point that sees
every wait, so it can refuse the request that would close the cycle.

The wait-for graph is multi-edge: one agent can wait for several independently
held tasks at once, so the edges form a mapping ``waiter -> holders``. Cycle
detection walks every reachable holder from the proposed edge. This module is
pure and deterministic.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping


def would_create_cycle(waits: Mapping[str, Collection[str]], waiter: str, holder: str) -> bool:
    """Return whether ``waiter`` waiting for ``holder`` would close a cycle.

    Parameters
    ----------
    waits : Mapping[str, Collection[str]]
        The current wait-for graph mapping each waiting agent to every agent it
        waits for. A scalar string value from the pre-multi-edge public API is
        treated as one holder.
    waiter : str
        The agent that wants to start waiting.
    holder : str
        The agent currently holding what ``waiter`` wants.

    Returns
    -------
    bool
        ``True`` if adding the edge ``waiter -> holder`` would create a cycle
        (including the degenerate self-wait ``waiter == holder``); ``False`` when
        the wait is safe to register.
    """
    if waiter == holder:
        return True
    pending = [holder]
    seen: set[str] = set()
    while pending:
        node = pending.pop()
        if node == waiter:
            return True
        if node in seen:
            continue
        seen.add(node)
        next_holders = waits.get(node, ())
        if isinstance(next_holders, str):
            pending.append(next_holders)
        else:
            pending.extend(next_holders)
    return False
