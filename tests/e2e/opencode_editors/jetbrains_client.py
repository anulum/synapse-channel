# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real JetBrains AI Assistant ACP acceptance driver
"""Orchestrate pinned IntelliJ IDEA and AI Assistant through their public ACP UI."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

from e2e.opencode_editors.jetbrains_acceptance import (
    read_jetbrains_acceptance,
    retain_jetbrains_failure_evidence,
    run_jetbrains_acceptance,
)
from e2e.opencode_editors.jetbrains_cleanup import capture_evidence_and_terminate
from e2e.opencode_editors.jetbrains_evidence import (
    capture_screenshot,
    trace_has,
    wait_for_idea_log,
    wait_for_trace,
)
from e2e.opencode_editors.jetbrains_lifecycle import JetBrainsLifecycleGuard
from e2e.opencode_editors.jetbrains_readiness import prerequisite_then_all
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


def _wait_for_agent_session(
    trace: Path,
    log_root: Path,
    deadline: float,
    process: subprocess.Popen[str],
    lifecycle: JetBrainsLifecycleGuard,
) -> None:
    """Wait, within the handshake phase, until IDEA's ACP chat can take a prompt.

    The chat's input exists only after the IDE has created the chat, the agent
    has answered ``session/new``, and the IDE has started its client session and
    received the agent's commands. A prompt typed earlier lands on the agent
    selection screen and is lost. A missing signal raises ``RuntimeError`` that
    names it.
    """
    wait_for_idea_log(
        log_root,
        _ACP_CHAT_CREATED_MARKER,
        deadline,
        process.poll,
        guard=lifecycle.assert_at_most_one,
    )
    for marker in ('"method":"session/new"', '"response_to":"session/new"'):
        wait_for_trace(trace, marker, deadline, process, guard=lifecycle.assert_at_most_one)
    lifecycle.require_exactly_one()
    wait_for_idea_log(
        log_root,
        (_ACP_SESSION_PREREQUISITE, *_ACP_SESSION_COMPLETIONS),
        deadline,
        process.poll,
        guard=lifecycle.require_exactly_one,
        matcher=lambda contents: prerequisite_then_all(
            contents,
            _ACP_SESSION_PREREQUISITE,
            _ACP_SESSION_COMPLETIONS,
        ),
        contents_reader=lifecycle.idea_contents,
    )


def _acceptance_steps() -> SimpleNamespace:
    """Return this module's steps, including any a test has replaced."""
    return SimpleNamespace(
        _required_env=_required_env,
        _AGENT_NAME=_AGENT_NAME,
        render_acp_config=render_acp_config,
        write_inactive_acp_config=write_inactive_acp_config,
        write_idea_profile=write_idea_profile,
        idea_command=idea_command,
        _STARTUP_TIMEOUT_SECONDS=_STARTUP_TIMEOUT_SECONDS,
        _PROJECT_READY_TIMEOUT_SECONDS=_PROJECT_READY_TIMEOUT_SECONDS,
        _ACP_HANDSHAKE_TIMEOUT_SECONDS=_ACP_HANDSHAKE_TIMEOUT_SECONDS,
        _ACP_PROMPT_TIMEOUT_SECONDS=_ACP_PROMPT_TIMEOUT_SECONDS,
        _PROJECT_READY_MARKERS=_PROJECT_READY_MARKERS,
        _PROJECT_READY_PREREQUISITES=_PROJECT_READY_PREREQUISITES,
        _PROJECT_READY_COMPLETION=_PROJECT_READY_COMPLETION,
        complete_first_run_agreements=complete_first_run_agreements,
        find_project_window=find_project_window,
        skip_islands_onboarding=skip_islands_onboarding,
        wait_for_idea_log=wait_for_idea_log,
        wait_for_trace=wait_for_trace,
        trace_has=trace_has,
        capture_screenshot=capture_screenshot,
        capture_evidence_and_terminate=capture_evidence_and_terminate,
        _wait_for_agent_session=_wait_for_agent_session,
    )


def main() -> int:
    """Run the isolated JetBrains/OpenCode ACP acceptance flow."""
    driver = _acceptance_steps()
    acceptance = read_jetbrains_acceptance(driver)
    output = acceptance.artifacts / "intellij-process.log"
    screenshot = acceptance.artifacts / "intellij.png"
    hub_screenshot = acceptance.artifacts / "intellij-ai-chat-hub.png"
    chat_screenshot = acceptance.artifacts / "intellij-chat-open.png"
    with output.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            acceptance.command,
            cwd=acceptance.project,
            env=dict(os.environ),
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        lifecycle = JetBrainsLifecycleGuard.capture(
            acceptance.log_root,
            acceptance.trace,
            agent_id=_AGENT_ID,
            agent_name=_AGENT_NAME,
        )
        try:
            run_jetbrains_acceptance(
                driver,
                acceptance,
                process,
                lifecycle,
                hub_screenshot=hub_screenshot,
                chat_screenshot=chat_screenshot,
                screenshot=screenshot,
            )
            return 0
        finally:
            retain_jetbrains_failure_evidence(
                driver,
                process=process,
                screenshot=screenshot,
                output=output,
                artifacts=acceptance.artifacts,
                log_root=acceptance.log_root,
            )


if __name__ == "__main__":
    raise SystemExit(main())
