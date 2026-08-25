# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — fail-closed JetBrains ACP selector lifecycle
"""Own the bounded X11 lifecycle of the pinned JetBrains ACP selector."""

from __future__ import annotations

import time
from collections.abc import Callable

from e2e.opencode_editors import jetbrains_x11_driver as x11
from e2e.opencode_editors.jetbrains_selector_windows import (
    _SelectorGeometryPhase as _SelectorGeometryPhase,
)
from e2e.opencode_editors.jetbrains_selector_windows import (
    is_agent_selector_popup as is_agent_selector_popup,
)
from e2e.opencode_editors.jetbrains_selector_windows import (
    owned_agent_selector_popups as owned_agent_selector_popups,
)
from e2e.opencode_editors.jetbrains_selector_windows import (
    visible_agent_selector_popups as visible_agent_selector_popups,
)
from e2e.opencode_editors.jetbrains_selector_windows import (
    visible_jetbrains_window_rectangles as visible_jetbrains_window_rectangles,
)

AGENT_NAME = "SYNAPSE OpenCode E2E"

_AGENT_SELECTOR_OPEN_RETRY_SECONDS = 5.0
_AGENT_SELECTOR_CLOSED_SNAPSHOTS = 2


def find_agent_selector_popup(
    deadline: float,
    project: str,
    *,
    retry: Callable[[], None] | None = None,
    retry_interval_seconds: float = _AGENT_SELECTOR_OPEN_RETRY_SECONDS,
    guard: Callable[[], object] | None = None,
) -> str:
    """Wait for one selector while safely retrying its idempotent opener.

    Parameters
    ----------
    deadline:
        Absolute monotonic deadline for discovery.
    project:
        Pinned project-frame X11 identifier.
    retry:
        Optional idempotent opener invoked only while no selector exists.
    retry_interval_seconds:
        Minimum interval between opener retries.
    guard:
        Optional lifecycle invariant checked before each snapshot.

    Returns
    -------
    str
        The sole owner-proven selector window identifier.

    Raises
    ------
    ValueError
        If ``retry_interval_seconds`` is not positive.
    RuntimeError
        If discovery is ambiguous or the deadline expires.
    """
    if retry_interval_seconds <= 0:
        raise ValueError("selector retry interval must be positive")
    next_retry = 0.0
    while time.monotonic() < deadline:
        if guard is not None:
            guard()
        try:
            matches = visible_agent_selector_popups(project, deadline=deadline)
        except x11.X11WindowDisappeared:
            x11._bounded_poll_sleep(deadline)
            continue
        if len(matches) > 1:
            raise RuntimeError(
                "IntelliJ IDEA exposed multiple pinned ACP agent selector popups: "
                f"count={len(matches)}"
            )
        if matches:
            return matches[0]
        now = time.monotonic()
        if retry is not None and now >= next_retry:
            retry()
            next_retry = now + retry_interval_seconds
        x11._bounded_poll_sleep(deadline)
    raise RuntimeError("IntelliJ IDEA did not expose the pinned ACP agent selector popup")


def _wait_for_owned_agent_selector(
    project: str,
    *,
    deadline: float,
    phase: str,
    guard: Callable[[], object] | None = None,
) -> str:
    """Reacquire exactly one owner-proven selector through an absolute deadline."""
    while time.monotonic() < deadline:
        if guard is not None:
            guard()
        try:
            matches = visible_agent_selector_popups(
                project,
                deadline=deadline,
                geometry_phase=_SelectorGeometryPhase.FILTERED_READY,
            )
        except x11.X11WindowDisappeared:
            x11._bounded_poll_sleep(deadline)
            continue
        if len(matches) > 1:
            raise RuntimeError(
                f"refusing ambiguous JetBrains ACP agent selection {phase}: matches={matches!r}"
            )
        if matches:
            return matches[0]
        x11._bounded_poll_sleep(deadline)
    raise RuntimeError(f"JetBrains ACP agent selector did not stabilise {phase}")


def open_agent_selector(
    window: str,
    *,
    deadline: float,
    guard: Callable[[], object] | None = None,
) -> str:
    """Open the selector from a pinned project frame.

    Parameters
    ----------
    window:
        Pinned project-frame X11 identifier.
    deadline:
        Absolute monotonic deadline for focus, input, and discovery.
    guard:
        Optional lifecycle invariant checked during discovery.

    Returns
    -------
    str
        The sole owner-proven selector window identifier.

    Raises
    ------
    RuntimeError
        If the project frame, focus, input, or selector discovery contract
        cannot be proven.
    """

    def click_selector() -> None:
        geometry = x11._window_geometry(window, deadline=deadline)
        root_child = x11._window_is_root_child(window, deadline=deadline)
        if geometry != x11._PROJECT_SELECTOR_GEOMETRY or not root_child:
            rendered = "?x?" if geometry is None else f"{geometry[0]}x{geometry[1]}"
            raise RuntimeError(
                "refusing JetBrains selector input outside the pinned project frame: "
                f"geometry={rendered}, root_child={root_child}"
            )
        x11._focus_window_for_input(window, deadline=deadline)
        x11._checked_xdotool(
            "invoke the pinned JetBrains agent selector action",
            "key",
            "ctrl+alt+shift+k",
            deadline=deadline,
        )

    return find_agent_selector_popup(
        deadline,
        window,
        retry=click_selector,
        guard=guard,
    )


def select_pinned_agent(
    selector: str,
    project: str,
    *,
    deadline: float,
    guard: Callable[[], object] | None = None,
    capture_filtered_selector: Callable[[], None] | None = None,
) -> None:
    """Select the exact pinned ACP agent and prove stable selector closure.

    Parameters
    ----------
    selector:
        Initial owner-proven selector X11 identifier.
    project:
        Pinned project-frame X11 identifier.
    deadline:
        Absolute monotonic deadline for filtering, confirmation, and closure.
    guard:
        Optional lifecycle invariant checked during the operation.
    capture_filtered_selector:
        Optional evidence capture invoked after filtered selector reacquisition.

    Raises
    ------
    RuntimeError
        If selector identity, ownership, cardinality, focus, input, or stable
        closure cannot be proven before the deadline.
    """
    if not is_agent_selector_popup(selector, project, deadline=deadline):
        raise RuntimeError("refusing input outside the pinned ACP agent selector popup")
    matches = visible_agent_selector_popups(project, deadline=deadline)
    if matches != (selector,):
        raise RuntimeError(f"refusing ambiguous JetBrains ACP agent selection: matches={matches!r}")
    x11._focus_window_for_input(selector, deadline=deadline)
    x11._checked_xdotool(
        "clear the JetBrains ACP agent filter",
        "key",
        "ctrl+a",
        deadline=deadline,
    )
    x11._checked_xdotool(
        "filter the exact SYNAPSE OpenCode ACP agent",
        "type",
        "--delay",
        "1",
        "--",
        AGENT_NAME,
        deadline=deadline,
    )
    x11._bounded_poll_sleep(deadline)
    selector = _wait_for_owned_agent_selector(
        project,
        deadline=deadline,
        phase="while filtering the pinned agent",
        guard=guard,
    )
    if capture_filtered_selector is not None:
        capture_filtered_selector()
    if guard is not None:
        guard()
    x11._focus_window_for_input(selector, deadline=deadline)
    x11._checked_xdotool(
        "confirm the exact SYNAPSE OpenCode ACP agent",
        "key",
        "Return",
        deadline=deadline,
    )
    closed_snapshots = 0
    while time.monotonic() < deadline:
        if guard is not None:
            guard()
        rectangles = visible_jetbrains_window_rectangles(deadline=deadline)
        try:
            matches = owned_agent_selector_popups(
                rectangles,
                int(project),
                deadline=deadline,
                geometry_phase=_SelectorGeometryPhase.FILTERED_VISIBLE,
            )
        except x11.X11WindowDisappeared:
            closed_snapshots = 0
            x11._bounded_poll_sleep(deadline)
            continue
        if len(matches) > 1:
            raise RuntimeError(
                "JetBrains ACP agent selector cardinality changed after confirmation: "
                f"matches={matches!r}"
            )
        visible_windows = {rectangle.window for rectangle in rectangles}
        if matches:
            selector = matches[0]
            closed_snapshots = 0
        elif selector in visible_windows:
            raise RuntimeError(
                "JetBrains ACP agent selector lost pinned ownership after confirmation"
            )
        else:
            closed_snapshots += 1
            if closed_snapshots >= _AGENT_SELECTOR_CLOSED_SNAPSHOTS:
                return
        x11._bounded_poll_sleep(deadline)
    raise RuntimeError("JetBrains ACP agent selector remained open after confirmation")
