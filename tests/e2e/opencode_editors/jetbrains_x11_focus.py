# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — fail-closed JetBrains X11 focus ownership
"""Prove that keyboard focus remains inside the validated IDEA frame."""

from __future__ import annotations

from collections.abc import Callable

_MAX_FOCUS_ANCESTRY_DEPTH = 16
ParentLookup = Callable[[int], tuple[int | None, int | None]]


def focus_belongs_to_project(
    project_window: int,
    focused_window: int,
    parent_lookup: ParentLookup,
    *,
    max_depth: int = _MAX_FOCUS_ANCESTRY_DEPTH,
) -> bool:
    """Return whether an X11 focus owner is the IDEA frame or its descendant.

    Parameters
    ----------
    project_window:
        Validated top-level IDEA project-frame XID.
    focused_window:
        XID reported by ``xdotool getwindowfocus`` after composer input.
    parent_lookup:
        Callback returning ``(root_xid, parent_xid)`` for one XID.
    max_depth:
        Maximum number of parent links to inspect.

    Returns
    -------
    bool
        ``True`` only when a bounded, complete parent chain reaches the
        validated project frame.
    """
    if project_window <= 0 or focused_window <= 0 or max_depth <= 0:
        return False
    if focused_window == project_window:
        return True

    current = focused_window
    visited = {current}
    for _ in range(max_depth):
        root, parent = parent_lookup(current)
        if root is None or parent is None or root <= 0 or parent <= 0:
            return False
        if parent == project_window:
            return True
        if parent == root or parent in visited:
            return False
        visited.add(parent)
        current = parent
    return False
