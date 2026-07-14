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
from collections.abc import Callable
from pathlib import Path

_AGENT_NAME = "SYNAPSE OpenCode E2E"
_TIMEOUT_SECONDS = 150.0
_USER_AGREEMENT_TITLE = "IntelliJ IDEA User Agreement"
_USER_AGREEMENT_VERSION = "2.0"
_USER_AGREEMENT_ENV = "SYNAPSE_JETBRAINS_EULA_ACCEPTED_VERSION"
_DATA_SHARING_TITLE = "Data Sharing"
_AGENT_SELECTOR_REGISTRY_KEY = "llm.chat.new.chat.and.agent.selector.enabled"
_DEFAULT_AGENT_READY_MARKERS = (
    "Default-agent CDN readiness wait finished",
    "Skipping default-agent CDN readiness wait because rollout decision is already YES",
)


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


def _window_name(window: str) -> str | None:
    """Return one visible X11 window's semantic title, if it still exists."""
    completed = _xdotool("getwindowname", window)
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


def _window_is_root_child(window: str) -> bool:
    """Return whether a visible window is a top-level child of the X11 root."""
    completed = subprocess.run(  # nosec B603
        ["xwininfo", "-id", window, "-tree"],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if completed.returncode != 0:
        return False
    root, parent = _window_parentage(completed.stdout)
    return root is not None and parent == root


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


def _window_transient_for(window: str) -> int | None:
    """Return the XID that owns one transient top-level window."""
    completed = subprocess.run(  # nosec B603
        ["xprop", "-id", window, "WM_TRANSIENT_FOR"],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if completed.returncode != 0:
        return None
    return _xprop_window_id(completed.stdout)


def _find_first_run_dialog(deadline: float) -> tuple[str, str]:
    """Wait for one recognised top-level page of the pinned first-run UI."""
    while time.monotonic() < deadline:
        for title in (_USER_AGREEMENT_TITLE, _DATA_SHARING_TITLE):
            result = _xdotool("search", "--onlyvisible", "--name", f"^{title}$")
            if result.returncode == 0:
                for window in reversed(result.stdout.splitlines()):
                    if (
                        _window_name(window) == title
                        and _window_geometry(window) == (600, 460)
                        and _window_is_root_child(window)
                    ):
                        return window, title
        time.sleep(0.25)
    raise RuntimeError("IntelliJ IDEA did not expose a recognised pinned first-run dialog")


def _find_project_window(deadline: float) -> str:
    """Wait past fixed-size first-run windows for the real project frame."""
    while time.monotonic() < deadline:
        result = _xdotool("search", "--onlyvisible", "--class", "jetbrains-.*")
        if result.returncode == 0:
            for window in reversed(result.stdout.splitlines()):
                geometry = _window_geometry(window)
                if (
                    geometry is not None
                    and geometry[0] > 640
                    and geometry[1] > 460
                    and _window_is_root_child(window)
                ):
                    return window
        time.sleep(0.25)
    raise RuntimeError("IntelliJ IDEA did not expose a visible project window")


def _pointer_click(window: str, x: int, y: int, action: str) -> None:
    """Click one deterministic point after its caller validates the window."""
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


def _require_agreement_window(window: str, title: str) -> None:
    """Refuse pointer input unless the exact pinned agreement page is present."""
    geometry = _window_geometry(window)
    actual_title = _window_name(window)
    root_child = _window_is_root_child(window)
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
        _accept_user_agreement(window, title)
        while time.monotonic() < deadline:
            window, title = _find_first_run_dialog(deadline)
            if title == _DATA_SHARING_TITLE:
                break
            time.sleep(0.25)
        else:
            raise RuntimeError("IntelliJ IDEA did not advance to Data Sharing")
    # The nested "Content window" has the same class and geometry, so the
    # semantic title and root-parent invariant are both mandatory.
    _require_agreement_window(window, title)
    _pointer_click(window, 326, 432, "decline JetBrains usage-statistics sharing")


def _require_user_agreement_authorization() -> None:
    """Require the repository owner's exact version-bound legal attestation."""
    accepted = os.environ.get(_USER_AGREEMENT_ENV, "").strip()
    if accepted != _USER_AGREEMENT_VERSION:
        raise RuntimeError(
            f"JetBrains User Agreement v{_USER_AGREEMENT_VERSION} requires "
            f"{_USER_AGREEMENT_ENV}={_USER_AGREEMENT_VERSION}; refusing "
            f"owner attestation {accepted!r}"
        )


def _accept_user_agreement(window: str, title: str) -> None:
    """Accept only the exact agreement version attested by the owner."""
    _require_user_agreement_authorization()
    _require_agreement_window(window, title)
    _pointer_click(window, 44, 392, "confirm the JetBrains User Agreement checkbox")
    time.sleep(0.25)
    _require_agreement_window(window, title)
    _pointer_click(window, 542, 432, "accept the JetBrains User Agreement")


def _is_islands_popup(window: str, project: str) -> bool:
    """Match only the pinned onboarding transient owned by the project frame."""
    title = _window_name(window)
    try:
        project_id = int(project)
    except ValueError:
        return False
    return (
        title is not None
        and not title.strip()
        and _window_geometry(window) == (386, 486)
        and _window_is_root_child(window)
        and _window_transient_for(window) == project_id
    )


def _find_islands_popup(deadline: float, project: str) -> str:
    """Wait for the exact late first-run onboarding transient."""
    while time.monotonic() < deadline:
        result = _xdotool("search", "--onlyvisible", "--class", "jetbrains-.*")
        if result.returncode == 0:
            for window in reversed(result.stdout.splitlines()):
                if _is_islands_popup(window, project):
                    return window
        time.sleep(0.25)
    raise RuntimeError("IntelliJ IDEA did not expose the pinned Islands onboarding popup")


def _skip_islands_onboarding(deadline: float, project: str) -> None:
    """Dismiss the pinned onboarding transient and prove it disappeared."""
    popup = _find_islands_popup(deadline, project)
    if not _is_islands_popup(popup, project):
        raise RuntimeError("refusing input outside the pinned Islands onboarding popup")
    _pointer_click(popup, 191, 444, "skip the JetBrains Islands quick tour")
    while time.monotonic() < deadline:
        if _window_geometry(popup) is None:
            return
        time.sleep(0.25)
    raise RuntimeError("JetBrains Islands onboarding popup remained after Skip")


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
        ["import", "-window", "root", str(path)], check=False, timeout=15
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
) -> None:
    """Wait for exact IDEA log evidence while proving the process remains live."""
    requested = (markers,) if isinstance(markers, str) else markers
    if not requested:
        raise ValueError("at least one IDEA log marker is required")
    idea_log = log_root / "idea.log"
    while time.monotonic() < deadline:
        if idea_log.is_file():
            log_text = idea_log.read_text(encoding="utf-8", errors="replace")
            if any(marker in log_text for marker in requested):
                return
        if poll() is not None:
            raise RuntimeError(f"IntelliJ IDEA exited before log evidence {requested!r}")
        time.sleep(0.25)
    raise RuntimeError(f"IntelliJ IDEA log never contained {requested!r}")


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
        )
        try:
            deadline = time.monotonic() + _TIMEOUT_SECONDS
            _complete_first_run_agreements(deadline)
            window = _find_project_window(deadline)
            _skip_islands_onboarding(deadline, window)
            window = _find_project_window(deadline)
            _wait_for_idea_log(
                log_root, "Local ACP agents reloaded: 1 active", deadline, process.poll
            )
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
            _wait_for_idea_log(log_root, _DEFAULT_AGENT_READY_MARKERS, deadline, process.poll)
            _wait_for_idea_log(
                log_root,
                "No session managers found for agent 'SYNAPSE OpenCode E2E'",
                deadline,
                process.poll,
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
            _wait_for_idea_log(
                log_root,
                "Creating AcpSessionLifecycleManager for agent 'acp.synapse-opencode-e2e'",
                deadline,
                process.poll,
            )
            _wait_for_trace(trace, '"method":"initialize"', deadline, process)
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
