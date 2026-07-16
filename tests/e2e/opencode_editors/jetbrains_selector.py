# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — fail-closed JetBrains ACP selector lifecycle
"""Own the bounded X11 lifecycle of the pinned JetBrains ACP selector."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from typing import Protocol

from e2e.opencode_editors import jetbrains_x11_driver as x11
from e2e.opencode_editors.jetbrains_x11_geometry import (
    X11WindowRectangle,
    parse_window_rectangles,
)

AGENT_NAME = "SYNAPSE OpenCode E2E"

_AGENT_SELECTOR_OPEN_RETRY_SECONDS = 5.0
_AGENT_SELECTOR_CLOSED_SNAPSHOTS = 2
_X11_SNAPSHOT_ATTEMPTS = 3
_X11_BAD_WINDOW_LINE = "X Error of failed request:  BadWindow (invalid Window parameter)"
_X11_GET_WINDOW_ATTRIBUTES_LINE = "  Major opcode of failed request:  3 (X_GetWindowAttributes)"
_X11_BAD_WINDOW_METADATA = (
    re.compile(r"  Minor opcode of failed request:  [0-9]+"),
    re.compile(r"  Resource id in failed request:  0x[0-9a-f]+"),
    re.compile(r"  Serial number of failed request:  [0-9]+"),
    re.compile(r"  Current serial number in output stream:  [0-9]+"),
)
_AGENT_SELECTOR_TITLE = "win0"
_AGENT_SELECTOR_GEOMETRY = (310, 407)


class X11QueryResult(Protocol):
    """Expose the completed-process fields consumed by snapshot parsing."""

    returncode: int
    stdout: str
    stderr: str


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
    return x11._window_geometry(
        window, deadline=deadline
    ) == _AGENT_SELECTOR_GEOMETRY and _agent_selector_owner_matches(
        window, project_id, deadline=deadline
    )


def _agent_selector_owner_matches(
    window: str,
    project_id: int,
    *,
    deadline: float | None = None,
) -> bool:
    """Validate one selector candidate, rejecting unclassifiable X11 state."""
    return (
        x11._required_window_name(window, deadline=deadline) == _AGENT_SELECTOR_TITLE
        and x11._required_window_is_root_child(window, deadline=deadline)
        and x11._required_window_transient_for(window, deadline=deadline) == project_id
    )


def _is_disappearing_window_snapshot(result: X11QueryResult) -> bool:
    """Return whether a batched X11 query lost a window during classification."""
    if result.returncode != 1 or result.stdout.strip():
        return False
    lines = result.stderr.splitlines()
    if lines[:2] != [_X11_BAD_WINDOW_LINE, _X11_GET_WINDOW_ATTRIBUTES_LINE]:
        return False
    matched_metadata: set[int] = set()
    for line in lines[2:]:
        matches = {
            index
            for index, pattern in enumerate(_X11_BAD_WINDOW_METADATA)
            if pattern.fullmatch(line)
        }
        if len(matches) != 1 or not matched_metadata.isdisjoint(matches):
            return False
        matched_metadata.update(matches)
    return True


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
        if not _is_disappearing_window_snapshot(result):
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
) -> tuple[str, ...]:
    """Return exact selector candidates owned by one project frame.

    Parameters
    ----------
    rectangles:
        Validated visible JetBrains-window snapshot.
    project_id:
        Numeric X11 identifier of the pinned project frame.
    deadline:
        Absolute monotonic deadline for ownership queries.

    Returns
    -------
    tuple[str, ...]
        Distinct matching selector window identifiers.

    Raises
    ------
    RuntimeError
        If an exact selector title has selector geometry but belongs outside
        the pinned project frame.
    """
    matches: list[str] = []
    for rectangle in reversed(rectangles):
        window = rectangle.window
        if (
            rectangle.geometry == _AGENT_SELECTOR_GEOMETRY
            and window not in matches
            and _agent_selector_owner_matches(window, project_id, deadline=deadline)
        ):
            matches.append(window)
        elif (
            rectangle.geometry == _AGENT_SELECTOR_GEOMETRY
            and window not in matches
            and x11._required_window_name(window, deadline=deadline) == _AGENT_SELECTOR_TITLE
        ):
            raise RuntimeError(
                "refusing a JetBrains ACP agent selector outside the pinned project frame"
            )
    return tuple(matches)


def visible_agent_selector_popups(
    project: str,
    *,
    deadline: float,
) -> tuple[str, ...]:
    """Return visible selectors owned by one project frame.

    Parameters
    ----------
    project:
        Pinned project-frame X11 identifier.
    deadline:
        Absolute monotonic deadline for snapshot and ownership queries.

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
    return owned_agent_selector_popups(rectangles, project_id, deadline=deadline)


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
        matches = visible_agent_selector_popups(project, deadline=deadline)
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
        matches = visible_agent_selector_popups(project, deadline=deadline)
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
        matches = owned_agent_selector_popups(
            rectangles,
            int(project),
            deadline=deadline,
        )
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
