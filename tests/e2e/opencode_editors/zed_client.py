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
from pathlib import Path

_TIMEOUT_SECONDS = 90.0


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _run_xdotool(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # nosec B603
        ["xdotool", *args], capture_output=True, text=True, check=False, timeout=10
    )


def _checked_xdotool(action: str, *args: str) -> None:
    """Run one GUI action and fail with its diagnostic output."""
    completed = _run_xdotool(*args)
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "no diagnostic"
        raise RuntimeError(f"xdotool could not {action}: {detail}")


def _find_window(deadline: float) -> str:
    while time.monotonic() < deadline:
        for selector in (("--class", "zed"), ("--classname", "zed"), ("--name", "Zed")):
            result = _run_xdotool("search", "--onlyvisible", *selector)
            if result.returncode == 0 and result.stdout.splitlines():
                return result.stdout.splitlines()[-1]
        time.sleep(0.25)
    raise RuntimeError("Zed did not expose a visible window")


def _trace_has(trace: Path, marker: str) -> bool:
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


def main() -> int:
    zed = Path(_required_env("SYNAPSE_ZED_BIN"))
    project = Path(_required_env("SYNAPSE_EDITOR_E2E_PROJECT"))
    trace = Path(_required_env("SYNAPSE_ACP_TRACE"))
    prompt = _required_env("SYNAPSE_EDITOR_E2E_PROMPT")
    proxy_argv = json.loads(_required_env("SYNAPSE_ACP_PROXY_ARGV_JSON"))
    if not isinstance(proxy_argv, list) or not all(isinstance(arg, str) for arg in proxy_argv):
        raise RuntimeError("SYNAPSE_ACP_PROXY_ARGV_JSON must contain string arguments")

    config_root = Path(_required_env("XDG_CONFIG_HOME")) / "zed"
    data_root = Path(_required_env("XDG_DATA_HOME")) / "zed-e2e"
    artifacts = Path(_required_env("SYNAPSE_EDITOR_E2E_ARTIFACT_DIR"))
    config_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    data_root.mkdir(mode=0o700, parents=True, exist_ok=True)
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
                "ctrl-alt-shift-o": [
                    "agent::NewExternalAgentThread",
                    {"agent": "synapse-opencode"},
                ]
            }
        }
    ]
    (config_root / "settings.json").write_text(json.dumps(settings) + "\n", encoding="utf-8")
    (config_root / "keymap.json").write_text(json.dumps(keymap) + "\n", encoding="utf-8")

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
            deadline = time.monotonic() + _TIMEOUT_SECONDS
            window = _find_window(deadline)
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
                "ctrl+alt+shift+o",
            )
            _wait_for_trace(
                trace,
                '"method":"session/new"',
                deadline,
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
            _wait_for_trace(
                trace,
                '"method":"session/prompt"',
                deadline,
                process,
                "ACP prompt delivery",
            )
            _wait_for_trace(
                trace,
                '"response_to":"session/prompt"',
                deadline,
                process,
                "ACP prompt response",
            )
            subprocess.run(  # nosec B603
                ["import", "-window", "root", str(screenshot)],
                check=False,
                timeout=15,
            )
            return 0
        finally:
            if not screenshot.exists():
                subprocess.run(  # nosec B603
                    ["import", "-window", "root", str(screenshot)],
                    check=False,
                    timeout=15,
                )
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
