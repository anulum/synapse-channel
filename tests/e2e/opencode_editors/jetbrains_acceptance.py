# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — JetBrains ACP acceptance phases
"""Phases of the pinned IntelliJ IDEA ACP acceptance flow.

Each phase calls back through the client module, so a test that replaces one of
that module's steps still sees the same call.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from e2e.opencode_editors import jetbrains_x11_driver as x11
from e2e.opencode_editors.jetbrains_lifecycle import JetBrainsLifecycleGuard
from e2e.opencode_editors.jetbrains_readiness import all_then_completion


@dataclass(frozen=True)
class JetBrainsAcceptance:
    """The paths and text one acceptance run keeps for every later phase."""

    project: Path
    trace: Path
    prompt: str
    agent_config: str
    home: Path
    artifacts: Path
    log_root: Path
    command: list[str]


def read_jetbrains_acceptance(driver: Any) -> JetBrainsAcceptance:
    """Read the required environment and prepare the isolated IDE profile."""
    binary = Path(driver._required_env("SYNAPSE_JETBRAINS_BIN"))
    plugins = Path(driver._required_env("SYNAPSE_JETBRAINS_PLUGINS"))
    project = Path(driver._required_env("SYNAPSE_EDITOR_E2E_PROJECT"))
    trace = Path(driver._required_env("SYNAPSE_ACP_TRACE"))
    prompt = driver._required_env("SYNAPSE_EDITOR_E2E_PROMPT")
    proxy_argv = json.loads(driver._required_env("SYNAPSE_ACP_PROXY_ARGV_JSON"))
    if (
        not isinstance(proxy_argv, list)
        or not proxy_argv
        or not all(isinstance(arg, str) for arg in proxy_argv)
    ):
        raise RuntimeError("SYNAPSE_ACP_PROXY_ARGV_JSON must contain non-empty string arguments")
    agent_config = driver.render_acp_config(proxy_argv, agent_name=driver._AGENT_NAME)
    home = Path(driver._required_env("HOME"))
    artifacts = Path(driver._required_env("SYNAPSE_EDITOR_E2E_ARTIFACT_DIR"))
    runtime_root = Path(driver._required_env("XDG_DATA_HOME")) / "intellij-e2e"
    config_root = runtime_root / "config"
    system_root = runtime_root / "system"
    log_root = runtime_root / "log"
    for directory in (config_root, system_root, log_root):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    driver.write_inactive_acp_config(home)
    driver.write_idea_profile(config_root)
    command = driver.idea_command(
        binary,
        home=home,
        config_root=config_root,
        system_root=system_root,
        plugins=plugins,
        log_root=log_root,
        project=project,
    )
    return JetBrainsAcceptance(
        project=project,
        trace=trace,
        prompt=prompt,
        agent_config=agent_config,
        home=home,
        artifacts=artifacts,
        log_root=log_root,
        command=command,
    )


def open_the_prepared_project(
    driver: Any,
    log_root: Path,
    process: subprocess.Popen[str],
    lifecycle: JetBrainsLifecycleGuard,
) -> str:
    """Accept the first-run agreements and wait until the project is ready."""
    startup_deadline = time.monotonic() + driver._STARTUP_TIMEOUT_SECONDS
    driver.complete_first_run_agreements(startup_deadline)
    window = cast(str, driver.find_project_window(startup_deadline))
    driver.skip_islands_onboarding(startup_deadline, window)
    window = cast(str, driver.find_project_window(startup_deadline))
    project_deadline = time.monotonic() + driver._PROJECT_READY_TIMEOUT_SECONDS
    driver.wait_for_idea_log(
        log_root,
        driver._PROJECT_READY_MARKERS,
        project_deadline,
        process.poll,
        matcher=lambda contents: all_then_completion(
            contents,
            driver._PROJECT_READY_PREREQUISITES,
            driver._PROJECT_READY_COMPLETION,
        ),
    )
    lifecycle.require_none()
    return window


def open_the_acp_editor(
    driver: Any,
    log_root: Path,
    window: str,
    process: subprocess.Popen[str],
    lifecycle: JetBrainsLifecycleGuard,
) -> float:
    """Open acp.json in the prepared IDE and return the phase deadline."""
    activation_deadline = time.monotonic() + cast(float, driver._ACP_HANDSHAKE_TIMEOUT_SECONDS)
    x11._focus_window_for_input(window, deadline=activation_deadline)
    x11._checked_xdotool(
        "open the isolated JetBrains ACP configuration",
        "key",
        "ctrl+alt+shift+a",
        deadline=activation_deadline,
    )
    driver.wait_for_idea_log(
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
    return activation_deadline


def install_the_saved_acp_configuration(
    driver: Any,
    *,
    home: Path,
    window: str,
    agent_config: str,
    log_root: Path,
    process: subprocess.Popen[str],
    lifecycle: JetBrainsLifecycleGuard,
    activation_deadline: float,
) -> None:
    """Replace acp.json with the exact agent and require IDEA to save it."""
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
    driver.wait_for_idea_log(
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


def open_chat_until_the_agent_is_ready(
    driver: Any,
    *,
    trace: Path,
    window: str,
    log_root: Path,
    process: subprocess.Popen[str],
    lifecycle: JetBrainsLifecycleGuard,
    hub_screenshot: Path,
    chat_screenshot: Path,
) -> None:
    """Open AI Chat, wait for initialize, and wait for the IDE's own session."""
    selection_deadline = time.monotonic() + driver._ACP_HANDSHAKE_TIMEOUT_SECONDS
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
    driver.capture_screenshot(hub_screenshot, deadline=selection_deadline)
    for attempt in range(10):
        if driver.trace_has(trace, '"method":"initialize"'):
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
    handshake_deadline = time.monotonic() + driver._ACP_HANDSHAKE_TIMEOUT_SECONDS
    driver.wait_for_trace(
        trace,
        '"method":"initialize"',
        handshake_deadline,
        process,
        guard=lifecycle.assert_at_most_one,
    )
    lifecycle.require_initialized_state()
    driver._wait_for_agent_session(trace, log_root, handshake_deadline, process, lifecycle)
    driver.capture_screenshot(chat_screenshot, deadline=handshake_deadline)


def run_jetbrains_acceptance(
    driver: Any,
    acceptance: JetBrainsAcceptance,
    process: subprocess.Popen[str],
    lifecycle: JetBrainsLifecycleGuard,
    *,
    hub_screenshot: Path,
    chat_screenshot: Path,
    screenshot: Path,
) -> None:
    """Drive the IDE from a ready project through the governed prompt."""
    window = open_the_prepared_project(driver, acceptance.log_root, process, lifecycle)
    activation_deadline = open_the_acp_editor(
        driver, acceptance.log_root, window, process, lifecycle
    )
    install_the_saved_acp_configuration(
        driver,
        home=acceptance.home,
        window=window,
        agent_config=acceptance.agent_config,
        log_root=acceptance.log_root,
        process=process,
        lifecycle=lifecycle,
        activation_deadline=activation_deadline,
    )
    open_chat_until_the_agent_is_ready(
        driver,
        trace=acceptance.trace,
        window=window,
        log_root=acceptance.log_root,
        process=process,
        lifecycle=lifecycle,
        hub_screenshot=hub_screenshot,
        chat_screenshot=chat_screenshot,
    )
    submit_the_governed_prompt(
        driver,
        trace=acceptance.trace,
        prompt=acceptance.prompt,
        window=window,
        process=process,
        lifecycle=lifecycle,
        screenshot=screenshot,
    )


def submit_the_governed_prompt(
    driver: Any,
    *,
    trace: Path,
    prompt: str,
    window: str,
    process: subprocess.Popen[str],
    lifecycle: JetBrainsLifecycleGuard,
    screenshot: Path,
) -> None:
    """Type the prompt only after the session exists, then keep the screenshot."""
    prompt_deadline = time.monotonic() + driver._ACP_PROMPT_TIMEOUT_SECONDS
    x11._submit_chat_prompt(window, prompt, deadline=prompt_deadline)
    driver.wait_for_trace(
        trace,
        '"method":"session/prompt"',
        prompt_deadline,
        process,
        guard=lifecycle.require_exactly_one,
    )
    driver.wait_for_trace(
        trace,
        '"response_to":"session/prompt"',
        prompt_deadline,
        process,
        guard=lifecycle.require_exactly_one,
    )
    lifecycle.require_exactly_one()
    driver.capture_screenshot(screenshot)
    lifecycle.require_exactly_one()


def retain_jetbrains_failure_evidence(
    driver: Any,
    *,
    process: subprocess.Popen[str],
    screenshot: Path,
    output: Path,
    artifacts: Path,
    log_root: Path,
) -> None:
    """Keep the same failure evidence the acceptance run always captured."""
    driver.capture_evidence_and_terminate(
        process,
        screenshot=screenshot,
        capture_screenshot=driver.capture_screenshot,
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
