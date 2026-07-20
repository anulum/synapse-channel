# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — classify owned JetBrains ACP selector windows
"""Discover and classify JetBrains ACP selector windows through bounded X11 queries."""

from __future__ import annotations

from enum import Enum, auto

from e2e.opencode_editors import jetbrains_x11_driver as x11
from e2e.opencode_editors.jetbrains_x11_geometry import (
    X11WindowRectangle,
    parse_window_rectangles,
)

_X11_SNAPSHOT_ATTEMPTS = 3
_AGENT_SELECTOR_TITLE = "win0"
_AGENT_SELECTOR_WIDTH = 310
_AGENT_SELECTOR_UNFILTERED_HEIGHT = 407
_AGENT_SELECTOR_SEARCH_ONLY_HEIGHT = 42


class _SelectorGeometryPhase(Enum):
    """Identify the selector geometry contract for one lifecycle phase."""

    UNFILTERED = auto()
    FILTERED_READY = auto()
    FILTERED_VISIBLE = auto()


def _selector_geometry_matches(
    geometry: tuple[int, int] | None,
    phase: _SelectorGeometryPhase,
) -> bool:
    """Return whether geometry proves the selector state required by ``phase``."""
    if geometry is None:
        return False
    width, height = geometry
    if width != _AGENT_SELECTOR_WIDTH:
        return False
    if phase is _SelectorGeometryPhase.UNFILTERED:
        return height == _AGENT_SELECTOR_UNFILTERED_HEIGHT
    if phase is _SelectorGeometryPhase.FILTERED_READY:
        return _AGENT_SELECTOR_SEARCH_ONLY_HEIGHT < height <= _AGENT_SELECTOR_UNFILTERED_HEIGHT
    return 0 < height <= _AGENT_SELECTOR_UNFILTERED_HEIGHT


def is_agent_selector_popup(
    window: str,
    project: str,
    *,
    deadline: float | None = None,
) -> bool:
    """Match the pinned agent selector transient owned by one project frame.

    Parameters
    ----------
    window:
        Candidate X11 window identifier.
    project:
        Pinned project-frame X11 identifier.
    deadline:
        Absolute monotonic deadline forwarded to X11 queries.

    Returns
    -------
    bool
        ``True`` only for the exact title, geometry, parentage, and transient
        ownership contract.
    """
    try:
        project_id = int(project)
    except ValueError:
        return False
    return _selector_geometry_matches(
        x11._window_geometry(window, deadline=deadline),
        _SelectorGeometryPhase.UNFILTERED,
    ) and _agent_selector_owner_matches(window, project_id, deadline=deadline)


def _agent_selector_owner_matches(
    window: str,
    project_id: int,
    *,
    deadline: float | None = None,
) -> bool:
    """Validate one selector candidate, rejecting unclassifiable X11 state."""
    return x11._required_window_name(
        window, deadline=deadline
    ) == _AGENT_SELECTOR_TITLE and _agent_selector_project_matches(
        window,
        project_id,
        deadline=deadline,
    )


def _agent_selector_project_matches(
    window: str,
    project_id: int,
    *,
    deadline: float | None = None,
) -> bool:
    """Return whether one selector is rooted in and transient for the project."""
    return (
        x11._required_window_is_root_child(window, deadline=deadline)
        and x11._required_window_transient_for(window, deadline=deadline) == project_id
    )


def visible_jetbrains_window_rectangles(
    *,
    deadline: float,
) -> tuple[X11WindowRectangle, ...]:
    """Return a validated snapshot of visible JetBrains windows.

    Parameters
    ----------
    deadline:
        Absolute monotonic deadline for every X11 query and retry.

    Returns
    -------
    tuple[X11WindowRectangle, ...]
        Parsed window rectangles, or an empty tuple for an exact empty search.

    Raises
    ------
    RuntimeError
        If X11 reports a non-canonical race, diagnostic, transport failure, or
        malformed geometry.
    """
    attempts_remaining = _X11_SNAPSHOT_ATTEMPTS
    while True:
        result = x11._xdotool(
            "search",
            "--onlyvisible",
            "--class",
            "jetbrains-.*",
            "getwindowgeometry",
            "--shell",
            "%@",
            deadline=deadline,
        )
        if not x11._is_disappearing_window_result(result):
            break
        attempts_remaining -= 1
        if attempts_remaining == 0:
            break
        x11._bounded_poll_sleep(deadline)
    if result.returncode == 1 and not result.stdout.strip() and not result.stderr.strip():
        return ()
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "no diagnostic"
        raise RuntimeError(f"xdotool could not snapshot visible JetBrains windows: {detail}")
    if result.stderr.strip() or not result.stdout.strip():
        detail = result.stderr.strip() or "empty geometry output"
        raise RuntimeError(f"xdotool returned an unclassifiable JetBrains snapshot: {detail}")
    try:
        return parse_window_rectangles(result.stdout)
    except ValueError as exc:
        raise RuntimeError("xdotool returned malformed batched selector geometry") from exc


def owned_agent_selector_popups(
    rectangles: tuple[X11WindowRectangle, ...],
    project_id: int,
    *,
    deadline: float,
    geometry_phase: _SelectorGeometryPhase = _SelectorGeometryPhase.UNFILTERED,
) -> tuple[str, ...]:
    """Return phase-valid selector candidates owned by one project frame.

    Parameters
    ----------
    rectangles:
        Validated visible JetBrains-window snapshot.
    project_id:
        Numeric X11 identifier of the pinned project frame.
    deadline:
        Absolute monotonic deadline for ownership queries.
    geometry_phase:
        Lifecycle-specific geometry contract. Initial discovery requires the
        exact unfiltered popup, filtered readiness excludes the search-field-only
        shell, and post-confirmation visibility accepts every positive bounded
        selector height so a collapsing popup cannot masquerade as closure.

    Returns
    -------
    tuple[str, ...]
        Distinct matching selector window identifiers.

    Raises
    ------
    RuntimeError
        If an exact selector title has phase-valid selector geometry but belongs
        outside the pinned project frame, or an owner-proven selector remap has
        phase-invalid geometry during post-confirmation closure.
    """
    matches: list[str] = []
    for rectangle in reversed(rectangles):
        window = rectangle.window
        geometry_matches = _selector_geometry_matches(
            rectangle.geometry,
            geometry_phase,
        )
        try:
            if (
                geometry_matches
                and window not in matches
                and _agent_selector_owner_matches(window, project_id, deadline=deadline)
            ):
                matches.append(window)
            elif (
                geometry_matches
                and window not in matches
                and x11._required_window_name(window, deadline=deadline) == _AGENT_SELECTOR_TITLE
            ):
                raise RuntimeError(
                    "refusing a JetBrains ACP agent selector outside the pinned project frame"
                )
            elif (
                geometry_phase is _SelectorGeometryPhase.FILTERED_VISIBLE
                and x11._required_window_name(window, deadline=deadline) == _AGENT_SELECTOR_TITLE
                and _agent_selector_project_matches(window, project_id, deadline=deadline)
            ):
                raise RuntimeError(
                    "JetBrains ACP agent selector geometry changed after confirmation"
                )
        except x11.X11WindowDisappeared:
            continue
    return tuple(matches)


def visible_agent_selector_popups(
    project: str,
    *,
    deadline: float,
    geometry_phase: _SelectorGeometryPhase = _SelectorGeometryPhase.UNFILTERED,
) -> tuple[str, ...]:
    """Return visible selectors owned by one project frame.

    Parameters
    ----------
    project:
        Pinned project-frame X11 identifier.
    deadline:
        Absolute monotonic deadline for snapshot and ownership queries.
    geometry_phase:
        Lifecycle-specific selector geometry contract.

    Returns
    -------
    tuple[str, ...]
        Distinct matching selector window identifiers, or an empty tuple when
        the project identifier is not numeric.
    """
    try:
        project_id = int(project)
    except ValueError:
        return ()
    rectangles = visible_jetbrains_window_rectangles(deadline=deadline)
    return owned_agent_selector_popups(
        rectangles,
        project_id,
        deadline=deadline,
        geometry_phase=geometry_phase,
    )
