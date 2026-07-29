# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — canonical waiter-sidecar identity conventions
"""Canonical naming and ownership rules for waiter sidecars."""

from __future__ import annotations

from collections.abc import Iterable

from synapse_channel.core.agent_liveness import PANE_WAITER_SUFFIX, WAITER_SUFFIX
from synapse_channel.core.agent_liveness import waiter_owner as _core_waiter_owner

__all__ = [
    "PANE_WAITER_SUFFIX",
    "WAITER_SUFFIX",
    "is_waiter",
    "legacy_project_scoped_terminal_sidecar",
    "pane_waiter_name",
    "split_roster",
    "waiter_name",
    "waiter_owner",
]


def is_waiter(name: str) -> bool:
    """Return whether ``name`` follows the waiter-sidecar convention."""
    return name.endswith(WAITER_SUFFIX) and len(name) > len(WAITER_SUFFIX)


def waiter_owner(name: str) -> str:
    """Return the identity a waiter wakes; leave a non-waiter unchanged."""
    return _core_waiter_owner(name)


def waiter_name(owner: str) -> str:
    """Return the conventional durable mailbox sidecar name for ``owner``."""
    return f"{owner}{WAITER_SUFFIX}"


def pane_waiter_name(owner: str) -> str:
    """Return the distinct provider pane-bridge sidecar name for ``owner``."""
    return f"{owner}{PANE_WAITER_SUFFIX}"


def legacy_project_scoped_terminal_sidecar(connect_name: str, for_name: str) -> str | None:
    """Return the exact terminal identity behind an old broad project arm."""
    owner = waiter_owner(connect_name)
    if owner != connect_name and owner.startswith(f"{for_name}/terminal-"):
        return owner
    return None


def split_roster(roster: Iterable[str]) -> tuple[list[str], list[str]]:
    """Split an online roster into sorted agent and waiter identities."""
    agents: list[str] = []
    waiters: list[str] = []
    for name in roster:
        (waiters if is_waiter(name) else agents).append(name)
    return sorted(agents), sorted(waiters)
