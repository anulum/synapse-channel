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
import subprocess
import sys
import time
from pathlib import Path

_AGENT_NAME = "SYNAPSE OpenCode E2E"
_TIMEOUT_SECONDS = 150.0


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _xdotool(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # nosec B603
        ["xdotool", *args], capture_output=True, text=True, check=False, timeout=10
    )


def _checked_xdotool(action: str, *args: str) -> None:
    """Run one GUI action and fail with its diagnostic output."""
    completed = _xdotool(*args)
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "no diagnostic"
        raise RuntimeError(f"xdotool could not {action}: {detail}")


def _window_geometry(window: str) -> tuple[int, int] | None:
    """Return one visible X11 window's dimensions, or ``None`` if it vanished."""
    completed = _xdotool("getwindowgeometry", "--shell", window)
    if completed.returncode != 0:
        return None
    geometry = dict(line.split("=", 1) for line in completed.stdout.splitlines() if "=" in line)
    try:
        return int(geometry["WIDTH"]), int(geometry["HEIGHT"])
    except (KeyError, ValueError):
        return None


def _find_agreement_window(deadline: float) -> str:
    """Wait past the JetBrains splash for the pinned agreement dialog."""
    while time.monotonic() < deadline:
        result = _xdotool("search", "--onlyvisible", "--class", "jetbrains-.*")
        if result.returncode == 0:
            for window in reversed(result.stdout.splitlines()):
                if _window_geometry(window) == (600, 460):
                    return window
        time.sleep(0.25)
    raise RuntimeError("IntelliJ IDEA did not expose the pinned 600x460 agreement dialog")


def _find_project_window(deadline: float) -> str:
    """Wait past fixed-size first-run windows for the real project frame."""
    while time.monotonic() < deadline:
        result = _xdotool("search", "--onlyvisible", "--class", "jetbrains-.*")
        if result.returncode == 0:
            for window in reversed(result.stdout.splitlines()):
                geometry = _window_geometry(window)
                if geometry is not None and geometry[0] > 640 and geometry[1] > 460:
                    return window
        time.sleep(0.25)
    raise RuntimeError("IntelliJ IDEA did not expose a visible project window")


def _click(window: str, x: int, y: int, action: str) -> None:
    """Click one deterministic point in the pinned 600x460 agreement UI."""
    _checked_xdotool(
        f"move to {action}",
        "mousemove",
        "--sync",
        "--window",
        window,
        str(x),
        str(y),
    )
    _checked_xdotool(action, "click", "1")


def _require_agreement_geometry(window: str) -> None:
    """Refuse pointer input unless the pinned agreement dialog is present."""
    geometry = _window_geometry(window)
    if geometry != (600, 460):
        rendered = "?x?" if geometry is None else f"{geometry[0]}x{geometry[1]}"
        raise RuntimeError(
            f"refusing JetBrains agreement input outside the pinned 600x460 UI: {rendered}"
        )


def _complete_first_run_agreements(window: str) -> None:
    """Accept the IDE terms and explicitly decline usage-statistics sharing."""
    # AgreementUi is fixed at 600x460 in the pinned JetBrains platform.  Its
    # first page requires an explicit checkbox before Continue is enabled.
    time.sleep(1.0)
    _require_agreement_geometry(window)
    _click(window, 44, 392, "select the JetBrains terms checkbox")
    time.sleep(0.25)
    _click(window, 542, 432, "accept the JetBrains terms")
    # The same modal immediately switches to the data-sharing page.  Its left
    # action is the documented decline path; choosing it keeps CI telemetry off.
    time.sleep(1.0)
    _require_agreement_geometry(window)
    _click(window, 451, 432, "decline JetBrains usage-statistics sharing")
    time.sleep(1.0)


def _trace_has(trace: Path, marker: str) -> bool:
    if not trace.is_file():
        return False
    return marker in trace.read_text(encoding="utf-8")


def _wait_for_trace(
    trace: Path, marker: str, deadline: float, process: subprocess.Popen[str]
) -> None:
    while time.monotonic() < deadline:
        if _trace_has(trace, marker):
            return
        if process.poll() is not None:
            raise RuntimeError(f"IntelliJ IDEA exited before ACP evidence: {process.returncode}")
        time.sleep(0.25)
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
    (config_dir / "acp.json").write_text(json.dumps(config) + "\n", encoding="utf-8")


def _write_keymap(config_root: Path) -> None:
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


def _screenshot(path: Path) -> None:
    subprocess.run(  # nosec B603
        ["import", "-window", "root", str(path)], check=False, timeout=15
    )


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
    _write_keymap(config_root)

    output = artifacts / "intellij-process.log"
    screenshot = artifacts / "intellij.png"
    command = [
        str(binary),
        f"-Didea.config.path={config_root}",
        f"-Didea.system.path={system_root}",
        f"-Didea.plugins.path={plugins}",
        f"-Didea.log.path={log_root}",
        "-Didea.trust.all.projects=true",
        "-Dide.no.platform.update=true",
        "-Dide.browser.jcef.sandbox.enable=false",
        str(project),
    ]
    with output.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(  # nosec B603
            command,
            cwd=project,
            env=dict(os.environ),
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            deadline = time.monotonic() + _TIMEOUT_SECONDS
            window = _find_agreement_window(deadline)
            _complete_first_run_agreements(window)
            window = _find_project_window(deadline)
            # xvfb-run intentionally has no EWMH window manager.  Every action
            # targets the discovered X11 window directly, so activation is not
            # required and would fail on the headless runner.
            _checked_xdotool("focus the IntelliJ IDEA window", "windowfocus", "--sync", window)
            _checked_xdotool(
                "open the AI Assistant tool window",
                "key",
                "--window",
                window,
                "ctrl+alt+shift+j",
            )
            _checked_xdotool(
                "open the ACP agent selector",
                "key",
                "--window",
                window,
                "ctrl+alt+shift+k",
            )
            _checked_xdotool(
                "select the ACP agent",
                "type",
                "--window",
                window,
                "--delay",
                "1",
                "--",
                _AGENT_NAME,
            )
            _checked_xdotool("confirm the ACP agent", "key", "--window", window, "Return")
            _wait_for_trace(trace, '"method":"session/new"', deadline, process)
            _checked_xdotool(
                "type the ACP prompt",
                "type",
                "--window",
                window,
                "--delay",
                "1",
                "--",
                prompt,
            )
            _checked_xdotool("submit the ACP prompt", "key", "--window", window, "Return")
            _wait_for_trace(trace, '"response_to":"session/prompt"', deadline, process)
            _screenshot(screenshot)
            return 0
        finally:
            if not screenshot.exists():
                _screenshot(screenshot)
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
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
