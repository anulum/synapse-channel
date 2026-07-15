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
import shutil
import subprocess  # nosec B404
import sys
import time
from pathlib import Path

_STARTUP_TIMEOUT_SECONDS = 60.0
_ACP_SESSION_TIMEOUT_SECONDS = 60.0
_ACP_PROMPT_TIMEOUT_SECONDS = 60.0
_GUI_COMMAND_TIMEOUT_SECONDS = 10.0
_OPEN_AGENT_BINDING = "ctrl-alt-shift-f12"
_OPEN_AGENT_ACCELERATOR = "ctrl+alt+shift+F12"


def _required_env(name: str) -> str:
    """Return one required non-empty environment value."""
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _required_executable(name: str) -> str:
    """Resolve one required tool to an absolute executable path."""
    executable = shutil.which(name)
    if executable is None or not os.path.isabs(executable):
        raise RuntimeError(f"required executable is unavailable: {name}")
    return executable


def _run_xdotool(*args: str) -> subprocess.CompletedProcess[str]:
    """Run one bounded X11 command and normalise a transport timeout."""
    command = [_required_executable("xdotool"), *args]
    try:
        return subprocess.run(  # nosec B603
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=_GUI_COMMAND_TIMEOUT_SECONDS,
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


def _checked_xdotool(action: str, *args: str) -> None:
    """Run one GUI action and fail with its diagnostic output."""
    completed = _run_xdotool(*args)
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "no diagnostic"
        raise RuntimeError(f"xdotool could not {action}: {detail}")


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


def _find_window(deadline: float) -> str:
    """Return the one visible Zed frame before the startup deadline."""
    while time.monotonic() < deadline:
        windows: list[str] = []
        for selector in (("--class", "zed"), ("--classname", "zed"), ("--name", "Zed")):
            result = _run_xdotool("search", "--onlyvisible", *selector)
            for window in _window_ids(result, selector=selector):
                if window not in windows:
                    windows.append(window)
        if len(windows) > 1:
            raise RuntimeError(f"Zed exposed multiple visible candidate windows: {windows!r}")
        if windows:
            return windows[0]
        time.sleep(0.25)
    raise RuntimeError("Zed did not expose a visible window")


def _trace_has(trace: Path, marker: str) -> bool:
    """Return whether the bounded ACP trace contains one semantic marker."""
    if not trace.is_file():
        return False
    return marker in trace.read_text(encoding="utf-8")


def _wait_for_trace(
    trace: Path,
    marker: str,
    deadline: float,
    process: subprocess.Popen[str],
    stage: str,
) -> None:
    """Wait for one semantic ACP milestone or a terminal process failure."""
    while time.monotonic() < deadline:
        if _trace_has(trace, marker):
            return
        if process.poll() is not None:
            raise RuntimeError(f"Zed exited before {stage}: exit status {process.returncode}")
        time.sleep(0.25)
    raise RuntimeError(f"Zed never reached {stage} before timeout")


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
        completed = subprocess.run(  # nosec B603
            [_required_executable("import"), "-window", "root", str(path)],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0 and path.is_file() and path.stat().st_size > 0


def main() -> int:
    """Run the isolated pinned Zed-to-OpenCode ACP acceptance flow."""
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

    log_path = artifacts / "zed-process.log"
    screenshot = artifacts / "zed.png"
    process_env = dict(os.environ)
    # The pinned Linux build documents this opt-in for software-rendered CI.
    # Without it Zed presents a modal GPU warning before the trust prompt.
    process_env["ZED_ALLOW_EMULATED_GPU"] = "1"
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(  # nosec B603
            [str(zed), "--foreground", "--user-data-dir", str(data_root), str(project)],
            cwd=project,
            env=process_env,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            startup_deadline = time.monotonic() + _STARTUP_TIMEOUT_SECONDS
            window = _find_window(startup_deadline)
            # xvfb-run intentionally has no EWMH window manager.  Every action
            # targets the discovered X11 window directly, so activation is not
            # required and would fail on the headless runner.
            _checked_xdotool("focus the Zed window", "windowfocus", "--sync", window)
            # Each run has an isolated data directory, so Zed correctly asks
            # whether the synthetic repository is trusted.  The dialog binds
            # Enter to "Trust and Continue"; exercise that real first-run UI
            # before invoking the configured ACP action.
            time.sleep(1.0)
            _checked_xdotool(
                "trust the synthetic Zed project",
                "key",
                "--window",
                window,
                "Return",
            )
            time.sleep(1.0)
            _checked_xdotool(
                "open the configured ACP agent",
                "key",
                "--window",
                window,
                _OPEN_AGENT_ACCELERATOR,
            )
            session_deadline = time.monotonic() + _ACP_SESSION_TIMEOUT_SECONDS
            _wait_for_trace(
                trace,
                '"method":"session/new"',
                session_deadline,
                process,
                "ACP session creation",
            )
            _checked_xdotool(
                "type the Zed prompt",
                "type",
                "--window",
                window,
                "--delay",
                "1",
                "--",
                prompt,
            )
            _checked_xdotool("submit the Zed prompt", "key", "--window", window, "Return")
            prompt_deadline = time.monotonic() + _ACP_PROMPT_TIMEOUT_SECONDS
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
            return 0
        finally:
            if not screenshot.exists():
                _capture_screenshot(screenshot)
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            if process.returncode not in (0, -15):
                print(log_path.read_text(encoding="utf-8")[-8000:], file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
