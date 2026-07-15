# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bounded X11 transport for the JetBrains ACP E2E
"""Provide fail-closed X11 discovery, focus, typing, and pointer primitives."""

from __future__ import annotations

import re
import shutil
import subprocess  # nosec B404
import time

from e2e.opencode_editors.jetbrains_timing import DEFAULT_JETBRAINS_TIMING
from e2e.opencode_editors.jetbrains_x11_focus import focus_belongs_to_project
from e2e.opencode_editors.jetbrains_x11_geometry import (
    parse_window_rectangle,
)

_GUI_COMMAND_TIMEOUT_SECONDS = DEFAULT_JETBRAINS_TIMING.command_timeout_seconds
_PROJECT_MINIMUM_GEOMETRY = (1000, 700)
_PROJECT_SELECTOR_GEOMETRY = (1400, 1000)
_CHAT_COMPOSER_RIGHT_INSET = 240
_CHAT_COMPOSER_BOTTOM_INSET = 130
_CHAT_SEND_RIGHT_INSET = 64
_CHAT_SEND_BOTTOM_INSET = 76
_CANONICAL_XID = re.compile(r"0x[0-9A-Fa-f]+\Z")


def _required_xid(token: str, *, diagnostic: str) -> int:
    """Parse one positive canonical hexadecimal X11 identifier."""
    if _CANONICAL_XID.fullmatch(token) is None:
        raise RuntimeError(diagnostic)
    xid = int(token[2:], 16)
    if xid == 0:
        raise RuntimeError(diagnostic)
    return xid


def _required_tool(name: str) -> str:
    """Resolve one required desktop tool to an absolute executable path."""
    executable = shutil.which(name)
    if executable is None:
        raise RuntimeError(f"required JetBrains E2E tool is unavailable: {name}")
    return executable


def _command_timeout(deadline: float | None) -> float:
    """Return one GUI command's ceiling within an optional absolute deadline."""
    if deadline is None:
        return _GUI_COMMAND_TIMEOUT_SECONDS
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise RuntimeError("JetBrains GUI phase deadline expired")
    return min(_GUI_COMMAND_TIMEOUT_SECONDS, remaining)


def _bounded_poll_sleep(deadline: float) -> None:
    """Sleep for at most one poll interval without crossing a phase deadline."""
    remaining = deadline - time.monotonic()
    if remaining > 0:
        time.sleep(min(0.25, remaining))


def _xdotool(
    *args: str,
    deadline: float | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run one bounded ``xdotool`` command and normalize timeout failure."""
    command = [_required_tool("xdotool"), *args]
    try:
        return subprocess.run(  # nosec B603
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=_command_timeout(deadline),
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        return subprocess.CompletedProcess(
            command,
            124,
            stdout,
            stderr or "xdotool command timed out",
        )


def _checked_xdotool(
    action: str,
    *args: str,
    deadline: float | None = None,
) -> None:
    """Run one GUI action and fail with its diagnostic output."""
    completed = _xdotool(*args, deadline=deadline)
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "no diagnostic"
        raise RuntimeError(f"xdotool could not {action}: {detail}")


def _window_rectangle(
    window: str,
    *,
    deadline: float | None = None,
) -> tuple[int, int, int, int, int] | None:
    """Return ``(screen, x, y, width, height)`` for one X11 window."""
    completed = _xdotool(
        "getwindowgeometry",
        "--shell",
        window,
        deadline=deadline,
    )
    if completed.returncode != 0:
        return None
    return parse_window_rectangle(completed.stdout)


def _window_geometry(
    window: str,
    *,
    deadline: float | None = None,
) -> tuple[int, int] | None:
    """Return one X11 window's dimensions, or ``None`` if it vanished."""
    rectangle = _window_rectangle(window, deadline=deadline)
    return None if rectangle is None else rectangle[3:]


def _window_name(window: str, *, deadline: float | None = None) -> str | None:
    """Return one visible X11 window's semantic title, if it still exists."""
    completed = _xdotool("getwindowname", window, deadline=deadline)
    if completed.returncode != 0:
        return None
    return completed.stdout.rstrip("\r\n")


def _required_window_name(window: str, *, deadline: float | None = None) -> str:
    """Return one title, rejecting an X11 query that cannot classify the window."""
    completed = _xdotool("getwindowname", window, deadline=deadline)
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "no diagnostic"
        raise RuntimeError(f"xdotool could not classify X11 window {window}: {detail}")
    return completed.stdout.rstrip("\r\n")


def _window_parentage(tree: str) -> tuple[str | None, str | None]:
    """Parse the root and parent XIDs from one ``xwininfo -tree`` result."""
    root: str | None = None
    parent: str | None = None
    for raw_line in tree.splitlines():
        line = raw_line.strip()
        if line.startswith("Root window id:"):
            fields = line.removeprefix("Root window id:").split()
            root = fields[0] if fields else None
        elif line.startswith("Parent window id:"):
            fields = line.removeprefix("Parent window id:").split()
            parent = fields[0] if fields else None
    return root, parent


def _window_is_root_child(window: str, *, deadline: float | None = None) -> bool:
    """Return whether a visible window is a top-level child of the X11 root."""
    completed = subprocess.run(  # nosec B603
        [_required_tool("xwininfo"), "-id", window, "-tree"],
        capture_output=True,
        text=True,
        check=False,
        timeout=_command_timeout(deadline),
    )
    if completed.returncode != 0:
        return False
    root, parent = _window_parentage(completed.stdout)
    return root is not None and parent == root


def _required_window_is_root_child(
    window: str,
    *,
    deadline: float | None = None,
) -> bool:
    """Return root ownership, rejecting failed or malformed X11 parentage queries."""
    completed = subprocess.run(  # nosec B603
        [_required_tool("xwininfo"), "-id", window, "-tree"],
        capture_output=True,
        text=True,
        check=False,
        timeout=_command_timeout(deadline),
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "no diagnostic"
        raise RuntimeError(f"xwininfo could not classify X11 window {window}: {detail}")
    root, parent = _window_parentage(completed.stdout)
    if root is None or parent is None:
        raise RuntimeError(f"xwininfo returned malformed parentage for X11 window {window}")
    diagnostic = f"xwininfo returned malformed parentage for X11 window {window}"
    root_id = _required_xid(root, diagnostic=diagnostic)
    parent_id = _required_xid(parent, diagnostic=diagnostic)
    return parent_id == root_id


def _window_parent_ids(
    window: int,
    *,
    deadline: float | None = None,
) -> tuple[int | None, int | None]:
    """Return parsed root and parent XIDs for one candidate focus owner."""
    completed = subprocess.run(  # nosec B603
        [_required_tool("xwininfo"), "-id", str(window), "-tree"],
        capture_output=True,
        text=True,
        check=False,
        timeout=_command_timeout(deadline),
    )
    if completed.returncode != 0:
        return None, None
    root, parent = _window_parentage(completed.stdout)
    try:
        return (
            int(root, 0) if root is not None else None,
            int(parent, 0) if parent is not None else None,
        )
    except ValueError:
        return None, None


def _xprop_window_id(output: str) -> int | None:
    """Parse one X11 window id from an ``xprop`` property result."""
    marker = "window id #"
    for raw_line in output.splitlines():
        if marker not in raw_line:
            continue
        fields = raw_line.split(marker, 1)[1].split()
        if not fields:
            return None
        try:
            return int(fields[0], 0)
        except ValueError:
            return None
    return None


def _window_transient_for(window: str, *, deadline: float | None = None) -> int | None:
    """Return the XID that owns one transient top-level window."""
    completed = subprocess.run(  # nosec B603
        [_required_tool("xprop"), "-id", window, "WM_TRANSIENT_FOR"],
        capture_output=True,
        text=True,
        check=False,
        timeout=_command_timeout(deadline),
    )
    if completed.returncode != 0:
        return None
    return _xprop_window_id(completed.stdout)


def _required_window_transient_for(
    window: str,
    *,
    deadline: float | None = None,
) -> int | None:
    """Return transient ownership, rejecting an X11 property query failure."""
    completed = subprocess.run(  # nosec B603
        [_required_tool("xprop"), "-id", window, "WM_TRANSIENT_FOR"],
        capture_output=True,
        text=True,
        check=False,
        timeout=_command_timeout(deadline),
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "no diagnostic"
        raise RuntimeError(f"xprop could not classify X11 window {window}: {detail}")
    normalized = " ".join(completed.stdout.split())
    if normalized == "WM_TRANSIENT_FOR: not found.":
        return None
    ownership_prefix = "WM_TRANSIENT_FOR(WINDOW): window id # "
    if not normalized.startswith(ownership_prefix):
        raise RuntimeError(f"xprop returned malformed transient ownership for X11 window {window}")
    raw_owner = normalized.removeprefix(ownership_prefix)
    diagnostic = f"xprop returned malformed transient ownership for X11 window {window}"
    return _required_xid(raw_owner, diagnostic=diagnostic)


def _focused_window_id(*, deadline: float | None = None) -> int | None:
    """Return the XID that owns keyboard focus, if it can be proven."""
    completed = _xdotool("getwindowfocus", deadline=deadline)
    if completed.returncode != 0:
        return None
    try:
        return int(completed.stdout.strip(), 0)
    except ValueError:
        return None


def _focus_window_for_input(window: str, *, deadline: float | None = None) -> None:
    """Focus one X11 frame and prove its window tree owns keyboard input."""
    try:
        window_id = int(window, 0)
    except ValueError as exc:
        raise RuntimeError("validated JetBrains input window has an invalid XID") from exc
    if window_id <= 0:
        raise RuntimeError("validated JetBrains input window has an invalid XID")
    _checked_xdotool(
        "focus the JetBrains input window",
        "windowfocus",
        "--sync",
        window,
        deadline=deadline,
    )
    focused_window_id = _focused_window_id(deadline=deadline)
    owns_focus = focused_window_id is not None and focus_belongs_to_project(
        window_id,
        focused_window_id,
        lambda candidate: _window_parent_ids(candidate, deadline=deadline),
    )
    if not owns_focus:
        raise RuntimeError(
            "refusing JetBrains input without target-window keyboard focus: "
            f"expected={window_id}, focused={focused_window_id}"
        )


def _focus_chat_composer(window: str, *, deadline: float | None = None) -> None:
    """Focus the chat composer inside one validated IDEA project frame."""
    geometry = _window_geometry(window, deadline=deadline)
    root_child = _window_is_root_child(window, deadline=deadline)
    if (
        geometry is None
        or geometry[0] < _PROJECT_MINIMUM_GEOMETRY[0]
        or geometry[1] < _PROJECT_MINIMUM_GEOMETRY[1]
        or not root_child
    ):
        rendered = "?x?" if geometry is None else f"{geometry[0]}x{geometry[1]}"
        raise RuntimeError(
            "refusing JetBrains composer input outside a validated project frame: "
            f"geometry={rendered}, root_child={root_child}"
        )
    _checked_xdotool(
        "focus the IntelliJ IDEA window",
        "windowfocus",
        "--sync",
        window,
        deadline=deadline,
    )
    _pointer_click(
        window,
        geometry[0] - _CHAT_COMPOSER_RIGHT_INSET,
        geometry[1] - _CHAT_COMPOSER_BOTTOM_INSET,
        "focus the JetBrains AI Chat composer",
        deadline=deadline,
    )
    try:
        project_window_id = int(window, 0)
    except ValueError as exc:
        raise RuntimeError("validated JetBrains project window has an invalid XID") from exc
    focused_window_id = _focused_window_id(deadline=deadline)
    owns_focus = focused_window_id is not None and focus_belongs_to_project(
        project_window_id,
        focused_window_id,
        lambda candidate: _window_parent_ids(candidate, deadline=deadline),
    )
    if not owns_focus:
        raise RuntimeError(
            "refusing JetBrains prompt input without project-frame keyboard focus: "
            f"expected={project_window_id}, focused={focused_window_id}"
        )


def _submit_chat_prompt(
    window: str,
    prompt: str,
    *,
    deadline: float | None = None,
) -> None:
    """Enter and submit a prompt through the focused Swing composer widget."""
    _focus_chat_composer(window, deadline=deadline)
    _checked_xdotool(
        "clear the ACP prompt composer",
        "key",
        "ctrl+a",
        deadline=deadline,
    )
    _checked_xdotool(
        "type the ACP prompt",
        "type",
        "--delay",
        "1",
        "--",
        prompt,
        deadline=deadline,
    )
    geometry = _window_geometry(window, deadline=deadline)
    root_child = _window_is_root_child(window, deadline=deadline)
    if geometry != _PROJECT_SELECTOR_GEOMETRY or not root_child:
        rendered = "?x?" if geometry is None else f"{geometry[0]}x{geometry[1]}"
        raise RuntimeError(
            "refusing JetBrains prompt submission outside the pinned project frame: "
            f"geometry={rendered}, root_child={root_child}"
        )
    _pointer_click(
        window,
        geometry[0] - _CHAT_SEND_RIGHT_INSET,
        geometry[1] - _CHAT_SEND_BOTTOM_INSET,
        "submit the JetBrains ACP prompt",
        deadline=deadline,
    )


def _pointer_click(
    window: str,
    x: int,
    y: int,
    action: str,
    *,
    deadline: float | None = None,
) -> None:
    """Atomically click one deterministic point in a validated window."""
    rectangle = _window_rectangle(window, deadline=deadline)
    if rectangle is None:
        raise RuntimeError(f"refusing {action} in a vanished X11 window")
    screen, left, top, width, height = rectangle
    if x < 0 or y < 0 or x >= width or y >= height:
        raise RuntimeError(
            f"refusing {action} outside its X11 window: point=({x},{y}), geometry={width}x{height}"
        )
    _checked_xdotool(
        action,
        "mousemove",
        "--screen",
        str(screen),
        str(left + x),
        str(top + y),
        "sleep",
        "0.25",
        "click",
        "1",
        deadline=deadline,
    )
