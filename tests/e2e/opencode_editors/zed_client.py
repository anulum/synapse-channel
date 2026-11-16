# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real Zed GUI ACP acceptance driver
"""Drive a pinned Zed GUI through a deterministic action binding."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from e2e.opencode_editors.editor_process_runner import PROCESS_GROUP_SUPERVISION_ENV
from e2e.opencode_editors.zed_readiness import has_ready_session
from e2e.opencode_editors.zed_timing import DEFAULT_ZED_TIMING
from e2e.opencode_editors.zed_x11 import (
    bounded_sleep,
    checked_xdotool,
    find_owned_window,
    focus_window_for_input,
    required_executable,
)

_OPEN_AGENT_BINDING = "ctrl-alt-shift-f12"
_OPEN_AGENT_ACCELERATOR = "ctrl+alt+shift+F12"


def _required_env(name: str) -> str:
    """Return one required non-empty environment value."""
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _trace_has(trace: Path, marker: str) -> bool:
    """Return whether the bounded ACP trace contains one semantic marker."""
    if not trace.is_file():
        return False
    return marker in trace.read_text(encoding="utf-8")


def _wait_for_trace_state(
    trace: Path,
    ready: Callable[[Path], bool],
    deadline: float,
    process: subprocess.Popen[str],
    stage: str,
) -> None:
    """Wait for one semantic ACP milestone or a terminal process failure."""
    while time.monotonic() < deadline:
        if ready(trace):
            return
        if process.poll() is not None:
            raise RuntimeError(f"Zed exited before {stage}: exit status {process.returncode}")
        time.sleep(0.25)
    raise RuntimeError(f"Zed never reached {stage} before timeout")


def _wait_for_trace(
    trace: Path,
    marker: str,
    deadline: float,
    process: subprocess.Popen[str],
    stage: str,
) -> None:
    """Wait for one content marker in the bounded ACP trace."""
    _wait_for_trace_state(
        trace,
        lambda candidate: _trace_has(candidate, marker),
        deadline,
        process,
        stage,
    )


def _wait_for_ready_session(
    trace: Path,
    deadline: float,
    process: subprocess.Popen[str],
) -> None:
    """Wait until ordered trace evidence proves a usable ACP session."""
    _wait_for_trace_state(
        trace,
        has_ready_session,
        deadline,
        process,
        "ACP session readiness",
    )


def _write_profile(data_root: Path, proxy_argv: list[str]) -> None:
    """Write the isolated settings and keymap consumed by ``--user-data-dir``."""
    config_root = data_root / "config"
    config_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    settings = {
        "agent": {"use_modifier_to_send": False},
        "agent_servers": {
            "synapse-opencode": {
                "type": "custom",
                "command": proxy_argv[0],
                "args": proxy_argv[1:],
                "env": {},
            }
        },
        "telemetry": {"diagnostics": False, "metrics": False},
    }
    keymap = [
        {
            "bindings": {
                _OPEN_AGENT_BINDING: [
                    "agent::NewExternalAgentThread",
                    {"agent": "synapse-opencode"},
                ]
            }
        }
    ]
    for path, payload in (
        (config_root / "settings.json", settings),
        (config_root / "keymap.json", keymap),
    ):
        path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        path.chmod(0o600)


def _capture_screenshot(path: Path) -> bool:
    """Capture the root X11 surface and report whether evidence exists."""
    try:
        completed = subprocess.run(
            [required_executable("import"), "-window", "root", str(path)],
            capture_output=True,
            text=True,
            check=False,
            timeout=DEFAULT_ZED_TIMING.screenshot_seconds,
        )
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0 and path.is_file() and path.stat().st_size > 0


@dataclass(frozen=True)
class _ZedRun:
    """Paths and text one Zed acceptance run keeps after it reads the environment."""

    zed: Path
    project: Path
    trace: Path
    prompt: str
    data_root: Path
    log_path: Path
    screenshot: Path
    process_env: dict[str, str]


def _read_zed_run() -> _ZedRun:
    """Read the required environment and write the isolated Zed profile."""
    zed = Path(_required_env("SYNAPSE_ZED_BIN"))
    project = Path(_required_env("SYNAPSE_EDITOR_E2E_PROJECT"))
    trace = Path(_required_env("SYNAPSE_ACP_TRACE"))
    prompt = _required_env("SYNAPSE_EDITOR_E2E_PROMPT")
    proxy_argv = json.loads(_required_env("SYNAPSE_ACP_PROXY_ARGV_JSON"))
    if (
        not isinstance(proxy_argv, list)
        or not proxy_argv
        or not all(isinstance(arg, str) and arg for arg in proxy_argv)
    ):
        raise RuntimeError("SYNAPSE_ACP_PROXY_ARGV_JSON must contain non-empty string arguments")
    data_root = Path(_required_env("XDG_DATA_HOME")) / "zed-e2e"
    artifacts = Path(_required_env("SYNAPSE_EDITOR_E2E_ARTIFACT_DIR"))
    data_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    artifacts.mkdir(mode=0o700, parents=True, exist_ok=True)
    _write_profile(data_root, proxy_argv)
    process_env = dict(os.environ)
    # The pinned Linux build documents this opt-in for software-rendered CI.
    # Without it Zed presents a modal GPU warning before the trust prompt.
    process_env["ZED_ALLOW_EMULATED_GPU"] = "1"
    return _ZedRun(
        zed=zed,
        project=project,
        trace=trace,
        prompt=prompt,
        data_root=data_root,
        log_path=artifacts / "zed-process.log",
        screenshot=artifacts / "zed.png",
        process_env=process_env,
    )


def _trust_the_project_and_open_the_agent(
    window: str,
    startup_deadline: float,
    trace: Path,
    process: subprocess.Popen[str],
) -> None:
    """Trust the synthetic project, open the agent, and wait for its session."""
    # xvfb-run intentionally has no EWMH window manager.  Every action
    # targets the discovered X11 window directly, so activation is not
    # required and would fail on the headless runner.
    checked_xdotool(
        "focus the Zed window",
        "windowfocus",
        "--sync",
        window,
        deadline=startup_deadline,
    )
    # Each run has an isolated data directory, so Zed correctly asks
    # whether the synthetic repository is trusted.  The dialog binds
    # Enter to "Trust and Continue"; exercise that real first-run UI
    # before invoking the configured ACP action.
    bounded_sleep(startup_deadline, 1.0)
    checked_xdotool(
        "trust the synthetic Zed project",
        "key",
        "--window",
        window,
        "Return",
        deadline=startup_deadline,
    )
    bounded_sleep(startup_deadline, 1.0)
    checked_xdotool(
        "open the configured ACP agent",
        "key",
        "--window",
        window,
        _OPEN_AGENT_ACCELERATOR,
        deadline=startup_deadline,
    )
    session_deadline = time.monotonic() + DEFAULT_ZED_TIMING.acp_session_seconds
    _wait_for_ready_session(trace, session_deadline, process)
    bounded_sleep(session_deadline, 0.5)


def _type_and_submit_the_zed_prompt(
    window: str,
    prompt: str,
    trace: Path,
    process: subprocess.Popen[str],
    screenshot: Path,
) -> None:
    """Type the prompt into the ready session and require screenshot evidence."""
    prompt_deadline = time.monotonic() + DEFAULT_ZED_TIMING.acp_prompt_seconds
    focus_window_for_input(window, deadline=prompt_deadline)
    checked_xdotool(
        "type the Zed prompt",
        "type",
        "--clearmodifiers",
        "--delay",
        "12",
        "--",
        prompt,
        deadline=prompt_deadline,
    )
    focus_window_for_input(window, deadline=prompt_deadline)
    checked_xdotool(
        "submit the Zed prompt",
        "key",
        "--clearmodifiers",
        "Return",
        deadline=prompt_deadline,
    )
    _wait_for_trace(
        trace,
        '"method":"session/prompt"',
        prompt_deadline,
        process,
        "ACP prompt delivery",
    )
    _wait_for_trace(
        trace,
        '"response_to":"session/prompt"',
        prompt_deadline,
        process,
        "ACP prompt response",
    )
    if not _capture_screenshot(screenshot):
        raise RuntimeError("Zed completed the ACP turn without screenshot evidence")


def _stop_the_zed_process(
    process: subprocess.Popen[str],
    screenshot: Path,
    log_path: Path,
) -> None:
    """Capture a missing screenshot and stop the Zed process group leader."""
    if not screenshot.exists():
        _capture_screenshot(screenshot)
    process.terminate()
    try:
        process.wait(timeout=DEFAULT_ZED_TIMING.leader_term_seconds)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=DEFAULT_ZED_TIMING.leader_kill_seconds)
    if process.returncode not in (0, -15):
        print(log_path.read_text(encoding="utf-8")[-8000:], file=sys.stderr)


def main() -> int:
    """Run the isolated pinned Zed-to-OpenCode ACP acceptance flow."""
    if os.environ.get(PROCESS_GROUP_SUPERVISION_ENV, "").strip() != "1":
        raise RuntimeError("Zed acceptance requires isolated process-group supervision")
    run = _read_zed_run()
    with run.log_path.open("w", encoding="utf-8") as log:
        startup_deadline = time.monotonic() + DEFAULT_ZED_TIMING.startup_seconds
        process = subprocess.Popen(
            [
                str(run.zed),
                "--foreground",
                "--user-data-dir",
                str(run.data_root),
                str(run.project),
            ],
            cwd=run.project,
            env=run.process_env,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            window = find_owned_window(
                startup_deadline,
                process_group=os.getpgrp(),
                project_name=run.project.name,
            )
            _trust_the_project_and_open_the_agent(window, startup_deadline, run.trace, process)
            _type_and_submit_the_zed_prompt(window, run.prompt, run.trace, process, run.screenshot)
            return 0
        finally:
            _stop_the_zed_process(process, run.screenshot, run.log_path)


if __name__ == "__main__":
    raise SystemExit(main())
