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
import stat
import subprocess  # nosec B404
import sys
import time
from pathlib import Path

from e2e.opencode_editors import jetbrains_x11_driver as x11
from e2e.opencode_editors.jetbrains_cleanup import capture_evidence_and_terminate
from e2e.opencode_editors.jetbrains_evidence import (
    capture_screenshot,
    trace_has,
    wait_for_idea_log,
    wait_for_trace,
)
from e2e.opencode_editors.jetbrains_lifecycle import JetBrainsLifecycleGuard
from e2e.opencode_editors.jetbrains_readiness import all_then_completion, prerequisite_then_all
from e2e.opencode_editors.jetbrains_selector import (
    AGENT_NAME as _AGENT_NAME,
)
from e2e.opencode_editors.jetbrains_setup import (
    complete_first_run_agreements,
    find_project_window,
    idea_command,
    render_acp_config,
    skip_islands_onboarding,
    write_idea_profile,
    write_inactive_acp_config,
)
from e2e.opencode_editors.jetbrains_timing import DEFAULT_JETBRAINS_TIMING

_AGENT_ID = "acp.synapse-opencode-e2e"
_STARTUP_TIMEOUT_SECONDS = DEFAULT_JETBRAINS_TIMING.startup_seconds
_PROJECT_READY_TIMEOUT_SECONDS = DEFAULT_JETBRAINS_TIMING.chat_ready_seconds
_ACP_HANDSHAKE_TIMEOUT_SECONDS = DEFAULT_JETBRAINS_TIMING.acp_handshake_seconds
_ACP_PROMPT_TIMEOUT_SECONDS = DEFAULT_JETBRAINS_TIMING.acp_prompt_seconds
_PROJECT_READY_MARKERS = (
    "fileOpened README.md",
    "exit dumb mode [project]",
)
_PROJECT_READY_PREREQUISITES = _PROJECT_READY_MARKERS[:1]
_PROJECT_READY_COMPLETION = _PROJECT_READY_MARKERS[1]
_ACP_SESSION_PREREQUISITE = "Required plugins check passed"
_ACP_CHAT_CREATED_MARKER = (
    "Created ACP session replay controller for agent=acp.synapse-opencode-e2e"
)
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
    agent_config = render_acp_config(proxy_argv, agent_name=_AGENT_NAME)

    home = Path(_required_env("HOME"))
    artifacts = Path(_required_env("SYNAPSE_EDITOR_E2E_ARTIFACT_DIR"))
    runtime_root = Path(_required_env("XDG_DATA_HOME")) / "intellij-e2e"
    config_root = runtime_root / "config"
    system_root = runtime_root / "system"
    log_root = runtime_root / "log"
    for directory in (config_root, system_root, log_root):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    write_inactive_acp_config(home)
    write_idea_profile(config_root)

    output = artifacts / "intellij-process.log"
    screenshot = artifacts / "intellij.png"
    hub_screenshot = artifacts / "intellij-ai-chat-hub.png"
    chat_screenshot = artifacts / "intellij-chat-open.png"
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
        lifecycle = JetBrainsLifecycleGuard.capture(
            log_root,
            trace,
            agent_id=_AGENT_ID,
            agent_name=_AGENT_NAME,
        )
        try:
            startup_deadline = time.monotonic() + _STARTUP_TIMEOUT_SECONDS
            complete_first_run_agreements(startup_deadline)
            window = find_project_window(startup_deadline)
            skip_islands_onboarding(startup_deadline, window)
            window = find_project_window(startup_deadline)
            project_deadline = time.monotonic() + _PROJECT_READY_TIMEOUT_SECONDS
            wait_for_idea_log(
                log_root,
                _PROJECT_READY_MARKERS,
                project_deadline,
                process.poll,
                matcher=lambda contents: all_then_completion(
                    contents,
                    _PROJECT_READY_PREREQUISITES,
                    _PROJECT_READY_COMPLETION,
                ),
            )
            lifecycle.require_none()
            activation_deadline = time.monotonic() + _ACP_HANDSHAKE_TIMEOUT_SECONDS
            x11._focus_window_for_input(window, deadline=activation_deadline)
            x11._checked_xdotool(
                "open the isolated JetBrains ACP configuration",
                "key",
                "ctrl+alt+shift+a",
                deadline=activation_deadline,
            )
            wait_for_idea_log(
                log_root,
                "fileOpened acp.json",
                activation_deadline,
                process.poll,
                guard=lifecycle.assert_at_most_one,
            )
            x11._focus_window_for_input(window, deadline=activation_deadline)
            x11._pointer_click(
                window,
                900,
                300,
                "focus the opened JetBrains ACP configuration editor",
                deadline=activation_deadline,
            )
            x11._checked_xdotool(
                "select the isolated JetBrains ACP configuration",
                "key",
                "ctrl+a",
                deadline=activation_deadline,
            )
            x11._checked_xdotool(
                "enter the exact JetBrains ACP configuration",
                "type",
                "--clearmodifiers",
                "--delay",
                "1",
                agent_config,
                deadline=activation_deadline,
            )
            x11._checked_xdotool(
                "save the isolated JetBrains ACP configuration",
                "key",
                "ctrl+s",
                deadline=activation_deadline,
            )
            wait_for_idea_log(
                log_root,
                "Local ACP agents reloaded: 1 active",
                activation_deadline,
                process.poll,
                guard=lifecycle.assert_at_most_one,
            )
            config_path = home / ".jetbrains" / "acp.json"
            config_stat = config_path.lstat()
            if (
                not stat.S_ISREG(config_stat.st_mode)
                or config_stat.st_uid != os.getuid()
                or config_stat.st_mode & 0o777 != 0o600
                or json.loads(config_path.read_text(encoding="utf-8")) != json.loads(agent_config)
            ):
                raise RuntimeError("IntelliJ IDEA did not save the exact ACP configuration")
            lifecycle.require_configured_state()
            selection_deadline = time.monotonic() + _ACP_HANDSHAKE_TIMEOUT_SECONDS
            x11._pointer_click(
                window,
                1382,
                83,
                "open the JetBrains AI Chat tool window",
                deadline=selection_deadline,
            )
            x11._checked_xdotool(
                "wait for the JetBrains AI Chat hub",
                "sleep",
                "2",
                deadline=selection_deadline,
            )
            lifecycle.assert_at_most_one()
            capture_screenshot(hub_screenshot, deadline=selection_deadline)
            for attempt in range(10):
                if trace_has(trace, '"method":"initialize"'):
                    break
                x11._pointer_click(
                    window,
                    1130,
                    613,
                    "continue to JetBrains AI Chat without activating JetBrains AI "
                    f"(attempt {attempt + 1})",
                    deadline=selection_deadline,
                )
                x11._bounded_poll_sleep(selection_deadline)
            x11._checked_xdotool(
                "wait for the provider-neutral JetBrains AI Chat",
                "sleep",
                "2",
                deadline=selection_deadline,
            )
            handshake_deadline = time.monotonic() + _ACP_HANDSHAKE_TIMEOUT_SECONDS
            wait_for_trace(
                trace,
                '"method":"initialize"',
                handshake_deadline,
                process,
                guard=lifecycle.assert_at_most_one,
            )
            lifecycle.require_initialized_state()
            capture_screenshot(chat_screenshot, deadline=handshake_deadline)
            prompt_deadline = time.monotonic() + _ACP_PROMPT_TIMEOUT_SECONDS
            x11._submit_chat_prompt(window, prompt, deadline=prompt_deadline)
            wait_for_idea_log(
                log_root,
                _ACP_CHAT_CREATED_MARKER,
                prompt_deadline,
                process.poll,
                guard=lifecycle.assert_at_most_one,
            )
            wait_for_trace(
                trace,
                '"method":"session/new"',
                prompt_deadline,
                process,
                guard=lifecycle.assert_at_most_one,
            )
            lifecycle.require_exactly_one()
            wait_for_idea_log(
                log_root,
                (_ACP_SESSION_PREREQUISITE, *_ACP_SESSION_COMPLETIONS),
                prompt_deadline,
                process.poll,
                guard=lifecycle.require_exactly_one,
                matcher=lambda contents: prerequisite_then_all(
                    contents,
                    _ACP_SESSION_PREREQUISITE,
                    _ACP_SESSION_COMPLETIONS,
                ),
                contents_reader=lifecycle.idea_contents,
            )
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
