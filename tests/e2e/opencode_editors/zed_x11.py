# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — pinned Zed X11 identity and transport
"""Select only the launched pinned Zed window within absolute phase bounds."""

from __future__ import annotations

import os
import re
import shutil
import subprocess  # nosec B404
import time

from e2e.opencode_editors.zed_timing import DEFAULT_ZED_TIMING

_PINNED_ZED_APP_ID = "dev.zed.Zed"
_PINNED_ZED_APP_ID_REGEX = f"^{re.escape(_PINNED_ZED_APP_ID)}$"
_CLASS_SELECTOR = ("--class", _PINNED_ZED_APP_ID_REGEX)
_INSTANCE_SELECTOR = ("--classname", _PINNED_ZED_APP_ID_REGEX)
_POLL_INTERVAL_SECONDS = 0.25


def required_executable(name: str) -> str:
    """Resolve one required tool to an absolute executable path."""
    executable = shutil.which(name)
    if executable is None or not os.path.isabs(executable):
        raise RuntimeError(f"required executable is unavailable: {name}")
    return executable


def _remaining_timeout(deadline: float) -> float:
    """Return one X11 command timeout bounded by an absolute phase deadline."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise RuntimeError("Zed X11 phase deadline expired")
    return min(DEFAULT_ZED_TIMING.command_timeout_seconds, remaining)


def _run_xdotool(
    *args: str,
    deadline: float,
) -> subprocess.CompletedProcess[str]:
    """Run one X11 command without extending its absolute phase deadline."""
    command = [required_executable("xdotool"), *args]
    try:
        return subprocess.run(  # nosec B603
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=_remaining_timeout(deadline),
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


def checked_xdotool(
    action: str,
    *args: str,
    deadline: float,
) -> None:
    """Run one bounded GUI action and fail with its diagnostic output."""
    completed = _run_xdotool(*args, deadline=deadline)
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "no diagnostic"
        raise RuntimeError(f"xdotool could not {action}: {detail}")


def focus_window_for_input(window: str, *, deadline: float) -> None:
    """Focus one owned frame and prove X11 reports that exact input target."""
    checked_xdotool(
        "focus the Zed input target",
        "windowfocus",
        "--sync",
        window,
        deadline=deadline,
    )
    result = _run_xdotool("getwindowfocus", "-f", deadline=deadline)
    token = result.stdout.strip()
    if (
        result.returncode != 0
        or result.stderr.strip()
        or not token.isdecimal()
        or int(token) <= 0
        or token != str(int(token))
        or token != window
    ):
        detail = result.stderr.strip() or result.stdout.strip() or "no focused window"
        raise RuntimeError(f"xdotool could not prove Zed input focus: {detail}")


def _window_ids(
    result: subprocess.CompletedProcess[str],
    *,
    selector: tuple[str, str],
) -> tuple[str, ...]:
    """Parse one exact visible-window search without hiding X11 failures."""
    if result.returncode == 1 and not result.stdout.strip() and not result.stderr.strip():
        return ()
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "no diagnostic"
        raise RuntimeError(f"xdotool could not search for Zed by {selector[0]}: {detail}")
    if result.stderr.strip() or not result.stdout.strip():
        detail = result.stderr.strip() or "empty window identifier output"
        raise RuntimeError(f"xdotool returned an unclassifiable Zed search: {detail}")
    windows: list[str] = []
    for raw_window in result.stdout.splitlines():
        window = raw_window.strip()
        if not window.isdecimal() or int(window) <= 0:
            raise RuntimeError("xdotool returned a malformed Zed window identifier")
        if window not in windows:
            windows.append(window)
    return tuple(windows)


def _search_window_ids(
    selector: tuple[str, str],
    *,
    deadline: float,
) -> tuple[str, ...]:
    """Return exact visible IDs for one pinned WM_CLASS selector."""
    result = _run_xdotool("search", "--onlyvisible", *selector, deadline=deadline)
    return _window_ids(result, selector=selector)


def _required_window_title(window: str, *, deadline: float) -> str:
    """Return one non-empty single-line title or reject the X11 response."""
    result = _run_xdotool("getwindowname", window, deadline=deadline)
    lines = result.stdout.splitlines()
    if result.returncode != 0 or result.stderr.strip() or len(lines) != 1 or not lines[0]:
        detail = result.stderr.strip() or result.stdout.strip() or "no title"
        raise RuntimeError(f"xdotool could not classify the Zed window title: {detail}")
    return lines[0]


def _required_window_pid(window: str, *, deadline: float) -> int:
    """Return one canonical positive `_NET_WM_PID` for the candidate."""
    result = _run_xdotool("getwindowpid", window, deadline=deadline)
    token = result.stdout.strip()
    if (
        result.returncode != 0
        or result.stderr.strip()
        or not token.isdecimal()
        or int(token) <= 1
        or token != str(int(token))
    ):
        detail = result.stderr.strip() or result.stdout.strip() or "no process id"
        raise RuntimeError(f"xdotool could not classify the Zed window process: {detail}")
    return int(token)


def _title_matches_project(title: str, project_name: str) -> bool:
    """Match pinned Zed's root title with its optional active-item suffix."""
    return title == project_name or title.startswith(f"{project_name} — ")


def _required_process_group(pid: int) -> int:
    """Return the live process group for one candidate owner PID."""
    try:
        return os.getpgid(pid)
    except ProcessLookupError as exc:
        raise RuntimeError("Zed window owner exited during classification") from exc
    except OSError as exc:
        raise RuntimeError("Zed window owner process group could not be read") from exc


def bounded_sleep(deadline: float, seconds: float) -> None:
    """Sleep only when the complete delay fits inside the phase deadline."""
    remaining = deadline - time.monotonic()
    if seconds < 0 or remaining < seconds:
        raise RuntimeError("Zed X11 phase deadline cannot accommodate the required delay")
    time.sleep(seconds)


def find_owned_window(
    deadline: float,
    *,
    process_group: int,
    project_name: str,
) -> str:
    """Return the sole strong Zed frame owned by the launched process group."""
    if process_group <= 1 or not project_name:
        raise ValueError("Zed window ownership requires a valid process group and project name")
    while time.monotonic() < deadline:
        class_windows = _search_window_ids(_CLASS_SELECTOR, deadline=deadline)
        instance_windows = _search_window_ids(_INSTANCE_SELECTOR, deadline=deadline)
        if set(class_windows) != set(instance_windows):
            raise RuntimeError("Zed class and instance selectors disagreed")
        if len(class_windows) > 1:
            raise RuntimeError(f"Zed exposed multiple strong window candidates: {class_windows!r}")
        if class_windows:
            window = class_windows[0]
            title = _required_window_title(window, deadline=deadline)
            if not _title_matches_project(title, project_name):
                raise RuntimeError(f"Zed window title did not match project {project_name!r}")
            pid = _required_window_pid(window, deadline=deadline)
            if _required_process_group(pid) != process_group:
                raise RuntimeError("Zed window was not owned by the launched process group")
            return window
        bounded_sleep(deadline, _POLL_INTERVAL_SECONDS)
    raise RuntimeError("Zed did not expose an owned visible window")
