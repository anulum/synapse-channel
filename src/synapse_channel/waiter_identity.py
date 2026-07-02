# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the "-rx" waiter-sidecar naming convention in one place
"""The waiter-sidecar identity convention.

A wake listener connects as ``<identity>-rx`` — a *sidecar* of the identity it
wakes, not an agent of its own. The convention was previously re-implemented at
every consumer (send strips it to reply as the owner, accounting and approvals
strip it to act as the owner, ``arm`` composes it, ``who --identity`` composes
it); this module is the single definition, and the roster views use it to stop
counting sidecars as agents — the defect that let a workstation with ~30 real
terminals report 200 "online agents".
"""

from __future__ import annotations

from collections.abc import Iterable

WAITER_SUFFIX = "-rx"

__all__ = [
    "WAITER_SUFFIX",
    "is_waiter",
    "split_roster",
    "waiter_name",
    "waiter_owner",
]


def is_waiter(name: str) -> bool:
    """Return whether ``name`` follows the waiter-sidecar convention.

    A bare ``"-rx"`` names nobody's sidecar, so it does not count.
    """
    return name.endswith(WAITER_SUFFIX) and len(name) > len(WAITER_SUFFIX)


def waiter_owner(name: str) -> str:
    """Return the identity a waiter wakes; a non-waiter name is returned unchanged."""
    if is_waiter(name):
        return name[: -len(WAITER_SUFFIX)]
    return name


def waiter_name(owner: str) -> str:
    """Return the sidecar name for ``owner``'s wake listener."""
    return f"{owner}{WAITER_SUFFIX}"


def split_roster(roster: Iterable[str]) -> tuple[list[str], list[str]]:
    """Split an online roster into sorted (agents, waiters).

    Agents are identities someone acts as; waiters are their wake-listener
    sidecars. Rendering the two apart keeps the agent count honest — every
    waiter holds a live socket, but a socket is presence, not an agent.
    """
    agents: list[str] = []
    waiters: list[str] = []
    for name in roster:
        (waiters if is_waiter(name) else agents).append(name)
    return sorted(agents), sorted(waiters)
