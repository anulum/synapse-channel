# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real JetBrains AI Assistant ACP acceptance driver
"""Drive a pinned IntelliJ IDEA and AI Assistant through its public ACP UI."""

from __future__ import annotations

import json
import os
import shutil
import subprocess  # nosec B404
import sys
import time
from collections.abc import Callable
from pathlib import Path

from e2e.opencode_editors.jetbrains_cleanup import capture_evidence_and_terminate
from e2e.opencode_editors.jetbrains_lifecycle import JetBrainsLifecycleGuard
from e2e.opencode_editors.jetbrains_readiness import prerequisite_then_all
from e2e.opencode_editors.jetbrains_timing import DEFAULT_JETBRAINS_TIMING
from e2e.opencode_editors.jetbrains_x11_focus import focus_belongs_to_project
from e2e.opencode_editors.jetbrains_x11_geometry import (
    parse_window_rectangle,
    parse_window_rectangles,
)

_AGENT_NAME = "SYNAPSE OpenCode E2E"
_AGENT_ID = "acp.synapse-opencode-e2e"
_STARTUP_TIMEOUT_SECONDS = DEFAULT_JETBRAINS_TIMING.startup_seconds
_CHAT_READY_TIMEOUT_SECONDS = DEFAULT_JETBRAINS_TIMING.chat_ready_seconds
_AGENT_SELECTION_TIMEOUT_SECONDS = DEFAULT_JETBRAINS_TIMING.agent_selection_seconds
_ACP_HANDSHAKE_TIMEOUT_SECONDS = DEFAULT_JETBRAINS_TIMING.acp_handshake_seconds
_ACP_PROMPT_TIMEOUT_SECONDS = DEFAULT_JETBRAINS_TIMING.acp_prompt_seconds
_GUI_COMMAND_TIMEOUT_SECONDS = DEFAULT_JETBRAINS_TIMING.command_timeout_seconds
_SCREENSHOT_TIMEOUT_SECONDS = DEFAULT_JETBRAINS_TIMING.screenshot_seconds
_CHAT_OPEN_RETRY_SECONDS = 5.0
_AGENT_SELECTOR_OPEN_RETRY_SECONDS = 5.0
_USER_AGREEMENT_TITLE = "IntelliJ IDEA User Agreement"
_USER_AGREEMENT_VERSION = "2.0"
_USER_AGREEMENT_ENV = "SYNAPSE_JETBRAINS_EULA_ACCEPTED_VERSION"
_DATA_SHARING_TITLE = "Data Sharing"
_AGENT_SELECTOR_REGISTRY_KEY = "llm.chat.new.chat.and.agent.selector.enabled"
_AGENT_SELECTOR_GEOMETRY = (310, 407)
_CHAT_READY_MARKERS = (f"No session managers found for agent '{_AGENT_NAME}'",)
_ACP_SESSION_PREREQUISITE = "Required plugins check passed"
_ACP_SESSION_COMPLETIONS = (
    "Starting ACP client session ",
    "Received notification: AvailableCommandsUpdate",
)
_PROJECT_MINIMUM_GEOMETRY = (1000, 700)
_PROJECT_SELECTOR_GEOMETRY = (1400, 1000)
_AGENT_SELECTOR_AGENT_POINT = (155, 185)
_CHAT_COMPOSER_RIGHT_INSET = 240
_CHAT_COMPOSER_BOTTOM_INSET = 130
_CHAT_SEND_RIGHT_INSET = 64
_CHAT_SEND_BOTTOM_INSET = 76


def _required_tool(name: str) -> str:
    """Resolve one required desktop tool to an absolute executable path."""
    executable = shutil.which(name)
    if executable is None:
        raise RuntimeError(f"required JetBrains E2E tool is unavailable: {name}")
    return executable


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


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


def _show_ai_chat(window: str, *, deadline: float | None = None) -> None:
    """Focus the pinned IDEA frame and invoke its idempotent chat action."""
    _checked_xdotool(
        "focus the IntelliJ IDEA window",
        "windowfocus",
        "--sync",
        window,
        deadline=deadline,
    )
    _checked_xdotool(
        "open the AI Assistant tool window",
        "key",
        "--window",
        window,
        "ctrl+alt+shift+j",
        deadline=deadline,
    )


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


def _focused_window_id(*, deadline: float | None = None) -> int | None:
    """Return the XID that owns keyboard focus, if it can be proven."""
    completed = _xdotool("getwindowfocus", deadline=deadline)
    if completed.returncode != 0:
        return None
    try:
        return int(completed.stdout.strip(), 0)
    except ValueError:
        return None


def _find_first_run_dialog(deadline: float) -> tuple[str, str]:
    """Wait for one recognised top-level page of the pinned first-run UI."""
    while time.monotonic() < deadline:
        for title in (_USER_AGREEMENT_TITLE, _DATA_SHARING_TITLE):
            result = _xdotool(
                "search",
                "--onlyvisible",
                "--name",
                f"^{title}$",
                deadline=deadline,
            )
            if result.returncode == 0:
                for window in reversed(result.stdout.splitlines()):
                    if (
                        _window_name(window, deadline=deadline) == title
                        and _window_geometry(window, deadline=deadline) == (600, 460)
                        and _window_is_root_child(window, deadline=deadline)
                    ):
                        return window, title
        _bounded_poll_sleep(deadline)
    raise RuntimeError("IntelliJ IDEA did not expose a recognised pinned first-run dialog")


def _find_project_window(deadline: float) -> str:
    """Wait past fixed-size first-run windows for the real project frame."""
    while time.monotonic() < deadline:
        result = _xdotool(
            "search",
            "--onlyvisible",
            "--class",
            "jetbrains-.*",
            deadline=deadline,
        )
        if result.returncode == 0:
            for window in reversed(result.stdout.splitlines()):
                geometry = _window_geometry(window, deadline=deadline)
                if (
                    geometry is not None
                    and geometry[0] > 640
                    and geometry[1] > 460
                    and _window_is_root_child(window, deadline=deadline)
                ):
                    return window
        _bounded_poll_sleep(deadline)
    raise RuntimeError("IntelliJ IDEA did not expose a visible project window")


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


def _require_agreement_window(
    window: str,
    title: str,
    *,
    deadline: float | None = None,
) -> None:
    """Refuse pointer input unless the exact pinned agreement page is present."""
    geometry = _window_geometry(window, deadline=deadline)
    actual_title = _window_name(window, deadline=deadline)
    root_child = _window_is_root_child(window, deadline=deadline)
    if geometry != (600, 460) or actual_title != title or not root_child:
        rendered = "?x?" if geometry is None else f"{geometry[0]}x{geometry[1]}"
        raise RuntimeError(
            "refusing JetBrains agreement input outside the pinned semantic UI: "
            f"title={actual_title!r}, geometry={rendered}, root_child={root_child}"
        )


def _complete_first_run_agreements(deadline: float) -> None:
    """Explicitly decline telemetry in the pinned first-run data-sharing UI."""
    window, title = _find_first_run_dialog(deadline)
    if title == _USER_AGREEMENT_TITLE:
        _accept_user_agreement(window, title, deadline=deadline)
        while time.monotonic() < deadline:
            window, title = _find_first_run_dialog(deadline)
            if title == _DATA_SHARING_TITLE:
                break
            _bounded_poll_sleep(deadline)
        else:
            raise RuntimeError("IntelliJ IDEA did not advance to Data Sharing")
    # The nested "Content window" has the same class and geometry, so the
    # semantic title and root-parent invariant are both mandatory.
    _require_agreement_window(window, title, deadline=deadline)
    _pointer_click(
        window,
        326,
        432,
        "decline JetBrains usage-statistics sharing",
        deadline=deadline,
    )


def _require_user_agreement_authorization() -> None:
    """Require the repository owner's exact version-bound legal attestation."""
    accepted = os.environ.get(_USER_AGREEMENT_ENV, "").strip()
    if accepted != _USER_AGREEMENT_VERSION:
        raise RuntimeError(
            f"JetBrains User Agreement v{_USER_AGREEMENT_VERSION} requires "
            f"{_USER_AGREEMENT_ENV}={_USER_AGREEMENT_VERSION}; refusing "
            f"owner attestation {accepted!r}"
        )


def _accept_user_agreement(window: str, title: str, *, deadline: float) -> None:
    """Accept only the exact agreement version attested by the owner."""
    _require_user_agreement_authorization()
    _require_agreement_window(window, title, deadline=deadline)
    _pointer_click(
        window,
        44,
        392,
        "confirm the JetBrains User Agreement checkbox",
        deadline=deadline,
    )
    _bounded_poll_sleep(deadline)
    _require_agreement_window(window, title, deadline=deadline)
    _pointer_click(
        window,
        542,
        432,
        "accept the JetBrains User Agreement",
        deadline=deadline,
    )


def _is_islands_popup(
    window: str,
    project: str,
    *,
    deadline: float | None = None,
) -> bool:
    """Match only the pinned onboarding transient owned by the project frame."""
    title = _window_name(window, deadline=deadline)
    try:
        project_id = int(project)
    except ValueError:
        return False
    return (
        title is not None
        and not title.strip()
        and _window_geometry(window, deadline=deadline) == (386, 486)
        and _window_is_root_child(window, deadline=deadline)
        and _window_transient_for(window, deadline=deadline) == project_id
    )


def _find_islands_popup(deadline: float, project: str) -> str:
    """Wait for the exact late first-run onboarding transient."""
    while time.monotonic() < deadline:
        result = _xdotool(
            "search",
            "--onlyvisible",
            "--class",
            "jetbrains-.*",
            deadline=deadline,
        )
        if result.returncode == 0:
            for window in reversed(result.stdout.splitlines()):
                if _is_islands_popup(window, project, deadline=deadline):
                    return window
        _bounded_poll_sleep(deadline)
    raise RuntimeError("IntelliJ IDEA did not expose the pinned Islands onboarding popup")


def _is_agent_selector_popup(
    window: str,
    project: str,
    *,
    deadline: float | None = None,
) -> bool:
    """Match only the pinned agent selector transient owned by the project frame."""
    try:
        project_id = int(project)
    except ValueError:
        return False
    return _window_geometry(
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
    """Validate the expensive X11 ownership invariants for one selector candidate."""
    return _window_is_root_child(window, deadline=deadline) and (
        _window_transient_for(window, deadline=deadline) == project_id
    )


def _visible_agent_selector_popups(
    project: str,
    *,
    deadline: float,
) -> tuple[str, ...]:
    """Return the distinct visible selectors owned by one project frame."""
    result = _xdotool(
        "search",
        "--onlyvisible",
        "--class",
        "jetbrains-.*",
        "getwindowgeometry",
        "--shell",
        "%@",
        deadline=deadline,
    )
    if result.returncode != 0:
        return ()
    try:
        rectangles = parse_window_rectangles(result.stdout)
    except ValueError as exc:
        raise RuntimeError("xdotool returned malformed batched selector geometry") from exc
    try:
        project_id = int(project)
    except ValueError:
        return ()
    matches: list[str] = []
    for rectangle in reversed(rectangles):
        window = rectangle.window
        if (
            rectangle.geometry == _AGENT_SELECTOR_GEOMETRY
            and window not in matches
            and _agent_selector_owner_matches(window, project_id, deadline=deadline)
        ):
            matches.append(window)
    return tuple(matches)


def _find_agent_selector_popup(
    deadline: float,
    project: str,
    *,
    retry: Callable[[], None] | None = None,
    retry_interval_seconds: float = _AGENT_SELECTOR_OPEN_RETRY_SECONDS,
    guard: Callable[[], object] | None = None,
) -> str:
    """Wait for one selector while safely retrying its idempotent opener."""
    if retry_interval_seconds <= 0:
        raise ValueError("selector retry interval must be positive")
    next_retry = 0.0
    while time.monotonic() < deadline:
        if guard is not None:
            guard()
        matches = _visible_agent_selector_popups(project, deadline=deadline)
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
        _bounded_poll_sleep(deadline)
    raise RuntimeError("IntelliJ IDEA did not expose the pinned ACP agent selector popup")


def _open_agent_selector(
    window: str,
    *,
    deadline: float,
    guard: Callable[[], object] | None = None,
) -> str:
    """Invoke the pinned selector action, retrying only before lifecycle start."""

    def click_selector() -> None:
        geometry = _window_geometry(window, deadline=deadline)
        root_child = _window_is_root_child(window, deadline=deadline)
        if geometry != _PROJECT_SELECTOR_GEOMETRY or not root_child:
            rendered = "?x?" if geometry is None else f"{geometry[0]}x{geometry[1]}"
            raise RuntimeError(
                "refusing JetBrains selector input outside the pinned project frame: "
                f"geometry={rendered}, root_child={root_child}"
            )
        _checked_xdotool(
            "focus the IntelliJ IDEA window",
            "windowfocus",
            "--sync",
            window,
            deadline=deadline,
        )
        _checked_xdotool(
            "invoke the pinned JetBrains agent selector action",
            "key",
            "--window",
            window,
            "ctrl+alt+shift+k",
            deadline=deadline,
        )

    return _find_agent_selector_popup(
        deadline,
        window,
        retry=click_selector,
        guard=guard,
    )


def _select_pinned_agent(
    selector: str,
    project: str,
    *,
    deadline: float,
    guard: Callable[[], object] | None = None,
) -> None:
    """Click the pinned OpenCode row once and prove the selector closes."""
    if not _is_agent_selector_popup(selector, project, deadline=deadline):
        raise RuntimeError("refusing input outside the pinned ACP agent selector popup")
    matches = _visible_agent_selector_popups(project, deadline=deadline)
    if matches != (selector,):
        raise RuntimeError(f"refusing ambiguous JetBrains ACP agent selection: matches={matches!r}")
    _pointer_click(
        selector,
        *_AGENT_SELECTOR_AGENT_POINT,
        "select the pinned SYNAPSE OpenCode ACP agent",
        deadline=deadline,
    )
    while time.monotonic() < deadline:
        if guard is not None:
            guard()
        matches = _visible_agent_selector_popups(project, deadline=deadline)
        if not matches:
            return
        if matches != (selector,):
            raise RuntimeError(
                "JetBrains ACP agent selector cardinality changed after confirmation: "
                f"matches={matches!r}"
            )
        _bounded_poll_sleep(deadline)
    raise RuntimeError("JetBrains ACP agent selector remained open after confirmation")


def _skip_islands_onboarding(deadline: float, project: str) -> None:
    """Dismiss the pinned onboarding transient and prove it disappeared."""
    popup = _find_islands_popup(deadline, project)
    if not _is_islands_popup(popup, project, deadline=deadline):
        raise RuntimeError("refusing input outside the pinned Islands onboarding popup")
    _pointer_click(
        popup,
        191,
        444,
        "skip the JetBrains Islands quick tour",
        deadline=deadline,
    )
    while time.monotonic() < deadline:
        if _window_geometry(popup, deadline=deadline) is None:
            return
        _bounded_poll_sleep(deadline)
    raise RuntimeError("JetBrains Islands onboarding popup remained after Skip")


def _trace_has(trace: Path, marker: str) -> bool:
    return (
        trace.is_file() and not trace.is_symlink() and marker in trace.read_text(encoding="utf-8")
    )


def _wait_for_trace(
    trace: Path,
    marker: str,
    deadline: float,
    process: subprocess.Popen[str],
    *,
    guard: Callable[[], object] | None = None,
) -> None:
    while time.monotonic() < deadline:
        if guard is not None:
            guard()
        if _trace_has(trace, marker):
            return
        if process.poll() is not None:
            raise RuntimeError(f"IntelliJ IDEA exited before ACP evidence: {process.returncode}")
        _bounded_poll_sleep(deadline)
    raise RuntimeError(f"IntelliJ IDEA ACP trace never contained {marker}")


def _write_acp_config(home: Path, proxy_argv: list[str]) -> None:
    config_dir = home / ".jetbrains"
    config_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    config = {
        "default_mcp_settings": {"use_idea_mcp": False, "use_custom_mcp": False},
        "agent_servers": {
            _AGENT_NAME: {
                "command": proxy_argv[0],
                "args": proxy_argv[1:],
                "env": {},
            }
        },
    }
    config_path = config_dir / "acp.json"
    config_path.write_text(json.dumps(config) + "\n", encoding="utf-8")
    config_path.chmod(0o600)


def _write_idea_profile(config_root: Path) -> None:
    keymaps = config_root / "keymaps"
    options = config_root / "options"
    keymaps.mkdir(mode=0o700, parents=True, exist_ok=True)
    options.mkdir(mode=0o700, parents=True, exist_ok=True)
    (keymaps / "SynapseE2E.xml").write_text(
        """<keymap version="1" name="Synapse E2E" parent="$default">
  <action id="AIAssistant.ToolWindow.ShowOrFocus">
    <keyboard-shortcut first-keystroke="control alt shift J" />
  </action>
  <action id="NewChatAgentSelectorAction">
    <keyboard-shortcut first-keystroke="control alt shift K" />
  </action>
</keymap>
""",
        encoding="utf-8",
    )
    (options / "keymap.xml").write_text(
        """<application>
  <component name="KeymapManager">
    <active_keymap name="Synapse E2E" />
  </component>
</application>
""",
        encoding="utf-8",
    )
    (options / "ide.general.xml").write_text(
        f"""<application>
  <component name="Registry">
    <entry key="{_AGENT_SELECTOR_REGISTRY_KEY}" value="true" />
  </component>
</application>
""",
        encoding="utf-8",
    )


def _screenshot(path: Path) -> None:
    subprocess.run(  # nosec B603
        [_required_tool("import"), "-window", "root", str(path)],
        check=False,
        timeout=_SCREENSHOT_TIMEOUT_SECONDS,
    )


def _idea_command(
    binary: Path,
    *,
    home: Path,
    config_root: Path,
    system_root: Path,
    plugins: Path,
    log_root: Path,
    project: Path,
) -> list[str]:
    """Build the pinned IDEA command with an isolated JVM home."""
    return [
        str(binary),
        f"-Duser.home={home}",
        f"-Didea.config.path={config_root}",
        f"-Didea.system.path={system_root}",
        f"-Didea.plugins.path={plugins}",
        f"-Didea.log.path={log_root}",
        "-Didea.trust.all.projects=true",
        "-Dide.no.platform.update=true",
        "-Dide.browser.jcef.sandbox.enable=false",
        str(project),
    ]


def _wait_for_idea_log(
    log_root: Path,
    markers: str | tuple[str, ...],
    deadline: float,
    poll: Callable[[], int | None],
    *,
    retry: Callable[[], None] | None = None,
    retry_interval_seconds: float = _CHAT_OPEN_RETRY_SECONDS,
    guard: Callable[[], object] | None = None,
    matcher: Callable[[str], bool] | None = None,
    contents_reader: Callable[[], str] | None = None,
) -> None:
    """Wait for exact IDEA log evidence while proving the process remains live."""
    required = (markers,) if isinstance(markers, str) else markers
    if not required:
        raise ValueError("at least one IDEA log marker is required")
    if retry is not None and retry_interval_seconds <= 0:
        raise ValueError("IDEA log retry interval must be positive")
    idea_log = log_root / "idea.log"
    next_retry = 0.0
    while time.monotonic() < deadline:
        if guard is not None:
            guard()
        if contents_reader is not None or idea_log.is_file():
            contents = (
                contents_reader()
                if contents_reader is not None
                else idea_log.read_text(encoding="utf-8", errors="replace")
            )
            if matcher is not None and matcher(contents):
                return
            position = 0
            matched = True
            for marker in required:
                position = contents.find(marker, position)
                if position < 0:
                    matched = False
                    break
                position += len(marker)
            if matched:
                return
        if poll() is not None:
            raise RuntimeError(f"IntelliJ IDEA exited before log evidence {required!r}")
        now = time.monotonic()
        if retry is not None and now >= next_retry:
            retry()
            next_retry = now + retry_interval_seconds
        _bounded_poll_sleep(deadline)
    raise RuntimeError(f"IntelliJ IDEA log never contained ordered markers {required!r}")


def main() -> int:
    binary = Path(_required_env("SYNAPSE_JETBRAINS_BIN"))
    plugins = Path(_required_env("SYNAPSE_JETBRAINS_PLUGINS"))
    project = Path(_required_env("SYNAPSE_EDITOR_E2E_PROJECT"))
    trace = Path(_required_env("SYNAPSE_ACP_TRACE"))
    prompt = _required_env("SYNAPSE_EDITOR_E2E_PROMPT")
    proxy_argv = json.loads(_required_env("SYNAPSE_ACP_PROXY_ARGV_JSON"))
    if not isinstance(proxy_argv, list) or not all(isinstance(arg, str) for arg in proxy_argv):
        raise RuntimeError("SYNAPSE_ACP_PROXY_ARGV_JSON must contain string arguments")

    home = Path(_required_env("HOME"))
    artifacts = Path(_required_env("SYNAPSE_EDITOR_E2E_ARTIFACT_DIR"))
    runtime_root = Path(_required_env("XDG_DATA_HOME")) / "intellij-e2e"
    config_root = runtime_root / "config"
    system_root = runtime_root / "system"
    log_root = runtime_root / "log"
    for directory in (config_root, system_root, log_root):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    _write_acp_config(home, proxy_argv)
    _write_idea_profile(config_root)

    output = artifacts / "intellij-process.log"
    screenshot = artifacts / "intellij.png"
    selector_screenshot = artifacts / "intellij-agent-selector.png"
    command = _idea_command(
        binary,
        home=home,
        config_root=config_root,
        system_root=system_root,
        plugins=plugins,
        log_root=log_root,
        project=project,
    )
    with output.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(  # nosec B603
            command,
            cwd=project,
            env=dict(os.environ),
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        try:
            startup_deadline = time.monotonic() + _STARTUP_TIMEOUT_SECONDS
            _complete_first_run_agreements(startup_deadline)
            window = _find_project_window(startup_deadline)
            _skip_islands_onboarding(startup_deadline, window)
            window = _find_project_window(startup_deadline)
            _wait_for_idea_log(
                log_root,
                "Local ACP agents reloaded: 1 active",
                startup_deadline,
                process.poll,
            )
            chat_deadline = time.monotonic() + _CHAT_READY_TIMEOUT_SECONDS
            _wait_for_idea_log(
                log_root,
                _CHAT_READY_MARKERS,
                chat_deadline,
                process.poll,
                retry=lambda: _show_ai_chat(window, deadline=chat_deadline),
            )
            lifecycle = JetBrainsLifecycleGuard.capture(
                log_root,
                trace,
                agent_id=_AGENT_ID,
                agent_name=_AGENT_NAME,
            )
            lifecycle.assert_at_most_one()
            selection_deadline = time.monotonic() + _AGENT_SELECTION_TIMEOUT_SECONDS
            selector = _open_agent_selector(
                window,
                deadline=selection_deadline,
                guard=lifecycle.require_none,
            )
            lifecycle.require_none()
            _bounded_poll_sleep(selection_deadline)
            _screenshot(selector_screenshot)
            lifecycle.require_none()
            _select_pinned_agent(
                selector,
                window,
                deadline=selection_deadline,
                guard=lifecycle.assert_at_most_one,
            )
            _wait_for_idea_log(
                log_root,
                "Creating AcpSessionLifecycleManager for agent 'acp.synapse-opencode-e2e'",
                selection_deadline,
                process.poll,
                guard=lifecycle.assert_at_most_one,
            )
            handshake_deadline = time.monotonic() + _ACP_HANDSHAKE_TIMEOUT_SECONDS
            _wait_for_trace(
                trace,
                '"method":"initialize"',
                handshake_deadline,
                process,
                guard=lifecycle.assert_at_most_one,
            )
            _wait_for_trace(
                trace,
                '"method":"session/new"',
                handshake_deadline,
                process,
                guard=lifecycle.assert_at_most_one,
            )
            lifecycle.require_exactly_one()
            _wait_for_idea_log(
                log_root,
                (_ACP_SESSION_PREREQUISITE, *_ACP_SESSION_COMPLETIONS),
                handshake_deadline,
                process.poll,
                guard=lifecycle.require_exactly_one,
                matcher=lambda contents: prerequisite_then_all(
                    contents,
                    _ACP_SESSION_PREREQUISITE,
                    _ACP_SESSION_COMPLETIONS,
                ),
                contents_reader=lifecycle.idea_contents,
            )
            prompt_deadline = time.monotonic() + _ACP_PROMPT_TIMEOUT_SECONDS
            _submit_chat_prompt(window, prompt, deadline=prompt_deadline)
            _wait_for_trace(
                trace,
                '"method":"session/prompt"',
                prompt_deadline,
                process,
                guard=lifecycle.require_exactly_one,
            )
            _wait_for_trace(
                trace,
                '"response_to":"session/prompt"',
                prompt_deadline,
                process,
                guard=lifecycle.require_exactly_one,
            )
            lifecycle.require_exactly_one()
            _screenshot(screenshot)
            lifecycle.require_exactly_one()
            return 0
        finally:
            capture_evidence_and_terminate(
                process,
                screenshot=screenshot,
                capture_screenshot=_screenshot,
                active_error=sys.exc_info()[1],
            )
            if process.returncode not in (0, -15):
                print(output.read_text(encoding="utf-8")[-12000:], file=sys.stderr)
            idea_log = log_root / "idea.log"
            if idea_log.is_file():
                (artifacts / "intellij-idea-tail.log").write_text(
                    idea_log.read_text(encoding="utf-8", errors="replace")[-200_000:],
                    encoding="utf-8",
                )


if __name__ == "__main__":
    raise SystemExit(main())
