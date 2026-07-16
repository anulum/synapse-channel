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
from e2e.opencode_editors.jetbrains_selector import (
    AGENT_NAME as _AGENT_NAME,
)
from e2e.opencode_editors.jetbrains_selector import (
    open_agent_selector as _open_agent_selector,
)
from e2e.opencode_editors.jetbrains_selector import (
    select_pinned_agent as _select_pinned_agent,
)
from e2e.opencode_editors.jetbrains_setup import (
    complete_first_run_agreements,
    find_project_window,
    idea_command,
    skip_islands_onboarding,
    write_acp_config,
    write_idea_profile,
)
from e2e.opencode_editors.jetbrains_timing import DEFAULT_JETBRAINS_TIMING

_AGENT_ID = "acp.synapse-opencode-e2e"
_STARTUP_TIMEOUT_SECONDS = DEFAULT_JETBRAINS_TIMING.startup_seconds
_CHAT_READY_TIMEOUT_SECONDS = DEFAULT_JETBRAINS_TIMING.chat_ready_seconds
_AGENT_SELECTION_TIMEOUT_SECONDS = DEFAULT_JETBRAINS_TIMING.agent_selection_seconds
_ACP_HANDSHAKE_TIMEOUT_SECONDS = DEFAULT_JETBRAINS_TIMING.acp_handshake_seconds
_ACP_PROMPT_TIMEOUT_SECONDS = DEFAULT_JETBRAINS_TIMING.acp_prompt_seconds
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
    x11._focus_window_for_input(window, deadline=deadline)
    x11._checked_xdotool(
        "open the AI Assistant tool window",
        "key",
        "ctrl+alt+shift+j",
        deadline=deadline,
    )


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
