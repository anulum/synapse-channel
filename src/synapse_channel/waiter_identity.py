# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the "-rx" waiter-sidecar naming convention in one place
"""The waiter-sidecar identity conventions.

A durable mailbox listener connects as ``<identity>-rx`` and a provider pane
bridge as ``<identity>-pane-rx``. Both are *sidecars* of the identity they wake,
not agents of their own. Distinct names let durable gap replay coexist with live
pane injection without takeover churn. This module is the single definition used
to recover either sidecar's owner and keep roster/dispatch views honest.
"""

from __future__ import annotations

from collections.abc import Iterable

from synapse_channel.core.agent_liveness import (
    PANE_WAITER_SUFFIX,
    WAITER_SUFFIX,
)
from synapse_channel.core.agent_liveness import (
    waiter_owner as _core_waiter_owner,
)

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
    """Return whether ``name`` follows the waiter-sidecar convention.

    A bare ``"-rx"`` names nobody's sidecar, so it does not count.
    """
    return name.endswith(WAITER_SUFFIX) and len(name) > len(WAITER_SUFFIX)


def waiter_owner(name: str) -> str:
    """Return the identity a waiter wakes; a non-waiter name is returned unchanged."""
    return _core_waiter_owner(name)


def waiter_name(owner: str) -> str:
    """Return the conventional durable mailbox sidecar name for ``owner``."""
    return f"{owner}{WAITER_SUFFIX}"


def pane_waiter_name(owner: str) -> str:
    """Return the distinct provider pane-bridge sidecar name for ``owner``."""
    return f"{owner}{PANE_WAITER_SUFFIX}"


def legacy_project_scoped_terminal_sidecar(connect_name: str, for_name: str) -> str | None:
    """Return the exact terminal identity behind an old broad project-scoped arm.

    Early fleets armed one waiter per *project*: the sidecar connected as
    ``<project>/terminal-<id>-rx`` while waking for the bare ``<project>``,
    which woke it on every message anyone sent into the project. The
    exact-identity convention replaced that shape, so ``arm`` and ``wait``
    refuse such a request and point the caller at the exact terminal identity
    to re-arm for instead.

    Parameters
    ----------
    connect_name : str
        The name the waiter would connect under (normally the ``-rx`` sidecar
        of the identity it wakes).
    for_name : str
        The identity whose messages the waiter would wake on.

    Returns
    -------
    str or None
        The exact identity (``<project>/terminal-<id>``) the caller should
        re-arm for, or ``None`` when the request is not a legacy broad
        project-scoped arm.
    """
    owner = waiter_owner(connect_name)
    if owner != connect_name and owner.startswith(f"{for_name}/terminal-"):
        return owner
    return None


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
