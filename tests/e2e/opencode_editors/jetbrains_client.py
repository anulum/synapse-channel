# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real JetBrains AI Assistant ACP acceptance driver
"""Orchestrate pinned IntelliJ IDEA and AI Assistant through their public ACP UI."""

from __future__ import annotations

import json
import os
import subprocess  # nosec B404
import sys
import time
from collections.abc import Callable
from pathlib import Path

from e2e.opencode_editors import jetbrains_x11_driver as x11
from e2e.opencode_editors.jetbrains_cleanup import capture_evidence_and_terminate
from e2e.opencode_editors.jetbrains_evidence import (
    capture_screenshot,
    wait_for_idea_log,
    wait_for_trace,
)
from e2e.opencode_editors.jetbrains_lifecycle import JetBrainsLifecycleGuard
from e2e.opencode_editors.jetbrains_readiness import prerequisite_then_all
from e2e.opencode_editors.jetbrains_setup import (
    complete_first_run_agreements,
    find_project_window,
    idea_command,
    skip_islands_onboarding,
    write_acp_config,
    write_idea_profile,
)
from e2e.opencode_editors.jetbrains_timing import DEFAULT_JETBRAINS_TIMING
from e2e.opencode_editors.jetbrains_x11_geometry import (
    X11WindowRectangle,
    parse_window_rectangles,
)

_AGENT_NAME = "SYNAPSE OpenCode E2E"
_AGENT_ID = "acp.synapse-opencode-e2e"
_STARTUP_TIMEOUT_SECONDS = DEFAULT_JETBRAINS_TIMING.startup_seconds
_CHAT_READY_TIMEOUT_SECONDS = DEFAULT_JETBRAINS_TIMING.chat_ready_seconds
_AGENT_SELECTION_TIMEOUT_SECONDS = DEFAULT_JETBRAINS_TIMING.agent_selection_seconds
_ACP_HANDSHAKE_TIMEOUT_SECONDS = DEFAULT_JETBRAINS_TIMING.acp_handshake_seconds
_ACP_PROMPT_TIMEOUT_SECONDS = DEFAULT_JETBRAINS_TIMING.acp_prompt_seconds
_AGENT_SELECTOR_OPEN_RETRY_SECONDS = 5.0
_X11_SNAPSHOT_ATTEMPTS = 3
_X11_BAD_WINDOW_MARKER = "X Error of failed request:  BadWindow (invalid Window parameter)"
_X11_GET_WINDOW_ATTRIBUTES_MARKER = "Major opcode of failed request:  3 (X_GetWindowAttributes)"
_AGENT_SELECTOR_TITLE = "win0"
_AGENT_SELECTOR_GEOMETRY = (310, 407)
_CHAT_READY_MARKERS = (f"No session managers found for agent '{_AGENT_NAME}'",)
_ACP_SESSION_PREREQUISITE = "Required plugins check passed"
_ACP_SESSION_COMPLETIONS = (
    "Starting ACP client session ",
    "Received notification: AvailableCommandsUpdate",
)


def _required_env(name: str) -> str:
    """Return one required non-empty environment value."""
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _show_ai_chat(window: str, *, deadline: float | None = None) -> None:
    """Focus the pinned IDEA frame and invoke its idempotent chat action."""
    x11._checked_xdotool(
        "focus the IntelliJ IDEA window",
        "windowfocus",
        "--sync",
        window,
        deadline=deadline,
    )
    x11._checked_xdotool(
        "open the AI Assistant tool window",
        "key",
        "--window",
        window,
        "ctrl+alt+shift+j",
        deadline=deadline,
    )


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


def _is_disappearing_window_snapshot(result: subprocess.CompletedProcess[str]) -> bool:
    """Return whether a batched X11 query lost a window during classification."""
    diagnostic = result.stderr
    return (
        result.returncode == 1
        and not result.stdout.strip()
        and _X11_BAD_WINDOW_MARKER in diagnostic
        and _X11_GET_WINDOW_ATTRIBUTES_MARKER in diagnostic
    )


def _visible_jetbrains_window_rectangles(*, deadline: float) -> tuple[X11WindowRectangle, ...]:
    """Return one validated batched snapshot of visible JetBrains windows."""
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


def _owned_agent_selector_popups(
    rectangles: tuple[X11WindowRectangle, ...],
    project_id: int,
    *,
    deadline: float,
) -> tuple[str, ...]:
    """Return distinct exact selector candidates owned by one project frame."""
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


def _visible_agent_selector_popups(
    project: str,
    *,
    deadline: float,
) -> tuple[str, ...]:
    """Return the distinct visible selectors owned by one project frame."""
    try:
        project_id = int(project)
    except ValueError:
        return ()
    rectangles = _visible_jetbrains_window_rectangles(deadline=deadline)
    return _owned_agent_selector_popups(rectangles, project_id, deadline=deadline)


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
        x11._bounded_poll_sleep(deadline)
    raise RuntimeError("IntelliJ IDEA did not expose the pinned ACP agent selector popup")


def _open_agent_selector(
    window: str,
    *,
    deadline: float,
    guard: Callable[[], object] | None = None,
) -> str:
    """Invoke the pinned selector action, retrying only before lifecycle start."""

    def click_selector() -> None:
        geometry = x11._window_geometry(window, deadline=deadline)
        root_child = x11._window_is_root_child(window, deadline=deadline)
        if geometry != x11._PROJECT_SELECTOR_GEOMETRY or not root_child:
            rendered = "?x?" if geometry is None else f"{geometry[0]}x{geometry[1]}"
            raise RuntimeError(
                "refusing JetBrains selector input outside the pinned project frame: "
                f"geometry={rendered}, root_child={root_child}"
            )
        x11._checked_xdotool(
            "focus the IntelliJ IDEA window",
            "windowfocus",
            "--sync",
            window,
            deadline=deadline,
        )
        x11._checked_xdotool(
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
    capture_filtered_selector: Callable[[], None] | None = None,
) -> None:
    """Filter, capture, confirm, and prove closure of one pinned agent selector."""
    if not _is_agent_selector_popup(selector, project, deadline=deadline):
        raise RuntimeError("refusing input outside the pinned ACP agent selector popup")
    matches = _visible_agent_selector_popups(project, deadline=deadline)
    if matches != (selector,):
        raise RuntimeError(f"refusing ambiguous JetBrains ACP agent selection: matches={matches!r}")
    x11._checked_xdotool(
        "focus the pinned JetBrains ACP agent selector",
        "windowfocus",
        "--sync",
        selector,
        deadline=deadline,
    )
    x11._checked_xdotool(
        "clear the JetBrains ACP agent filter",
        "key",
        "--window",
        selector,
        "ctrl+a",
        deadline=deadline,
    )
    x11._checked_xdotool(
        "filter the exact SYNAPSE OpenCode ACP agent",
        "type",
        "--window",
        selector,
        "--delay",
        "1",
        "--",
        _AGENT_NAME,
        deadline=deadline,
    )
    x11._bounded_poll_sleep(deadline)
    if guard is not None:
        guard()
    matches = _visible_agent_selector_popups(project, deadline=deadline)
    if matches != (selector,):
        raise RuntimeError(
            "JetBrains ACP agent selector changed while filtering the pinned agent: "
            f"matches={matches!r}"
        )
    if capture_filtered_selector is not None:
        capture_filtered_selector()
    if guard is not None:
        guard()
    x11._checked_xdotool(
        "confirm the exact SYNAPSE OpenCode ACP agent",
        "key",
        "--window",
        selector,
        "Return",
        deadline=deadline,
    )
    while time.monotonic() < deadline:
        if guard is not None:
            guard()
        rectangles = _visible_jetbrains_window_rectangles(deadline=deadline)
        matches = _owned_agent_selector_popups(
            rectangles,
            int(project),
            deadline=deadline,
        )
        visible_windows = {rectangle.window for rectangle in rectangles}
        if selector not in visible_windows and not matches:
            return
        if matches != (selector,):
            raise RuntimeError(
                "JetBrains ACP agent selector cardinality changed after confirmation: "
                f"matches={matches!r}"
            )
        x11._bounded_poll_sleep(deadline)
    raise RuntimeError("JetBrains ACP agent selector remained open after confirmation")


def main() -> int:
    """Run the isolated JetBrains/OpenCode ACP acceptance flow."""
    binary = Path(_required_env("SYNAPSE_JETBRAINS_BIN"))
    plugins = Path(_required_env("SYNAPSE_JETBRAINS_PLUGINS"))
    project = Path(_required_env("SYNAPSE_EDITOR_E2E_PROJECT"))
    trace = Path(_required_env("SYNAPSE_ACP_TRACE"))
    prompt = _required_env("SYNAPSE_EDITOR_E2E_PROMPT")
    proxy_argv = json.loads(_required_env("SYNAPSE_ACP_PROXY_ARGV_JSON"))
    if (
        not isinstance(proxy_argv, list)
        or not proxy_argv
        or not all(isinstance(arg, str) for arg in proxy_argv)
    ):
        raise RuntimeError("SYNAPSE_ACP_PROXY_ARGV_JSON must contain non-empty string arguments")

    home = Path(_required_env("HOME"))
    artifacts = Path(_required_env("SYNAPSE_EDITOR_E2E_ARTIFACT_DIR"))
    runtime_root = Path(_required_env("XDG_DATA_HOME")) / "intellij-e2e"
    config_root = runtime_root / "config"
    system_root = runtime_root / "system"
    log_root = runtime_root / "log"
    for directory in (config_root, system_root, log_root):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    write_acp_config(home, proxy_argv, agent_name=_AGENT_NAME)
    write_idea_profile(config_root)

    output = artifacts / "intellij-process.log"
    screenshot = artifacts / "intellij.png"
    selector_screenshot = artifacts / "intellij-agent-selector.png"
    command = idea_command(
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
            complete_first_run_agreements(startup_deadline)
            window = find_project_window(startup_deadline)
            skip_islands_onboarding(startup_deadline, window)
            window = find_project_window(startup_deadline)
            wait_for_idea_log(
                log_root,
                "Local ACP agents reloaded: 1 active",
                startup_deadline,
                process.poll,
            )
            chat_deadline = time.monotonic() + _CHAT_READY_TIMEOUT_SECONDS
            wait_for_idea_log(
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
            _select_pinned_agent(
                selector,
                window,
                deadline=selection_deadline,
                guard=lifecycle.assert_at_most_one,
                capture_filtered_selector=lambda: capture_screenshot(
                    selector_screenshot,
                    deadline=selection_deadline,
                ),
            )
            wait_for_idea_log(
                log_root,
                "Creating AcpSessionLifecycleManager for agent 'acp.synapse-opencode-e2e'",
                selection_deadline,
                process.poll,
                guard=lifecycle.assert_at_most_one,
            )
            handshake_deadline = time.monotonic() + _ACP_HANDSHAKE_TIMEOUT_SECONDS
            wait_for_trace(
                trace,
                '"method":"initialize"',
                handshake_deadline,
                process,
                guard=lifecycle.assert_at_most_one,
            )
            wait_for_trace(
                trace,
                '"method":"session/new"',
                handshake_deadline,
                process,
                guard=lifecycle.assert_at_most_one,
            )
            lifecycle.require_exactly_one()
            wait_for_idea_log(
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
            x11._submit_chat_prompt(window, prompt, deadline=prompt_deadline)
            wait_for_trace(
                trace,
                '"method":"session/prompt"',
                prompt_deadline,
                process,
                guard=lifecycle.require_exactly_one,
            )
            wait_for_trace(
                trace,
                '"response_to":"session/prompt"',
                prompt_deadline,
                process,
                guard=lifecycle.require_exactly_one,
            )
            lifecycle.require_exactly_one()
            capture_screenshot(screenshot)
            lifecycle.require_exactly_one()
            return 0
        finally:
            capture_evidence_and_terminate(
                process,
                screenshot=screenshot,
                capture_screenshot=capture_screenshot,
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
