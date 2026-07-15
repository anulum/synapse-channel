# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — isolated JetBrains startup and first-run setup
"""Prepare the pinned IDEA profile and safely cross its deterministic setup UI."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from e2e.opencode_editors.jetbrains_x11_driver import (
    _bounded_poll_sleep,
    _pointer_click,
    _window_geometry,
    _window_is_root_child,
    _window_name,
    _window_transient_for,
    _xdotool,
)

_USER_AGREEMENT_TITLE = "IntelliJ IDEA User Agreement"
_USER_AGREEMENT_VERSION = "2.0"
_USER_AGREEMENT_ENV = "SYNAPSE_JETBRAINS_EULA_ACCEPTED_VERSION"
_DATA_SHARING_TITLE = "Data Sharing"
_AGENT_SELECTOR_REGISTRY_KEY = "llm.chat.new.chat.and.agent.selector.enabled"


def find_first_run_dialog(deadline: float) -> tuple[str, str]:
    """Wait for one recognised top-level page of the pinned first-run UI."""
    while time.monotonic() < deadline:
        for title in (_USER_AGREEMENT_TITLE, _DATA_SHARING_TITLE):
            result = _xdotool(
                "search",
                "--onlyvisible",
                "--name",
                f"^{title}$",
                deadline=deadline,
            )
            if result.returncode == 0:
                for window in reversed(result.stdout.splitlines()):
                    if (
                        _window_name(window, deadline=deadline) == title
                        and _window_geometry(window, deadline=deadline) == (600, 460)
                        and _window_is_root_child(window, deadline=deadline)
                    ):
                        return window, title
        _bounded_poll_sleep(deadline)
    raise RuntimeError("IntelliJ IDEA did not expose a recognised pinned first-run dialog")


def find_project_window(deadline: float) -> str:
    """Wait past fixed-size first-run windows for the real project frame."""
    while time.monotonic() < deadline:
        result = _xdotool(
            "search",
            "--onlyvisible",
            "--class",
            "jetbrains-.*",
            deadline=deadline,
        )
        if result.returncode == 0:
            for window in reversed(result.stdout.splitlines()):
                geometry = _window_geometry(window, deadline=deadline)
                if (
                    geometry is not None
                    and geometry[0] > 640
                    and geometry[1] > 460
                    and _window_is_root_child(window, deadline=deadline)
                ):
                    return window
        _bounded_poll_sleep(deadline)
    raise RuntimeError("IntelliJ IDEA did not expose a visible project window")


def _require_agreement_window(
    window: str,
    title: str,
    *,
    deadline: float | None = None,
) -> None:
    """Refuse pointer input unless the exact pinned agreement page is present."""
    geometry = _window_geometry(window, deadline=deadline)
    actual_title = _window_name(window, deadline=deadline)
    root_child = _window_is_root_child(window, deadline=deadline)
    if geometry != (600, 460) or actual_title != title or not root_child:
        rendered = "?x?" if geometry is None else f"{geometry[0]}x{geometry[1]}"
        raise RuntimeError(
            "refusing JetBrains agreement input outside the pinned semantic UI: "
            f"title={actual_title!r}, geometry={rendered}, root_child={root_child}"
        )


def complete_first_run_agreements(deadline: float) -> None:
    """Accept an owner-attested agreement and explicitly decline telemetry."""
    window, title = find_first_run_dialog(deadline)
    if title == _USER_AGREEMENT_TITLE:
        _accept_user_agreement(window, title, deadline=deadline)
        while time.monotonic() < deadline:
            window, title = find_first_run_dialog(deadline)
            if title == _DATA_SHARING_TITLE:
                break
            _bounded_poll_sleep(deadline)
        else:
            raise RuntimeError("IntelliJ IDEA did not advance to Data Sharing")
    # The nested "Content window" has the same class and geometry, so the
    # semantic title and root-parent invariant are both mandatory.
    _require_agreement_window(window, title, deadline=deadline)
    _pointer_click(
        window,
        326,
        432,
        "decline JetBrains usage-statistics sharing",
        deadline=deadline,
    )


def _require_user_agreement_authorization() -> None:
    """Require the repository owner's exact version-bound legal attestation."""
    accepted = os.environ.get(_USER_AGREEMENT_ENV, "").strip()
    if accepted != _USER_AGREEMENT_VERSION:
        raise RuntimeError(
            f"JetBrains User Agreement v{_USER_AGREEMENT_VERSION} requires "
            f"{_USER_AGREEMENT_ENV}={_USER_AGREEMENT_VERSION}; refusing "
            f"owner attestation {accepted!r}"
        )


def _accept_user_agreement(window: str, title: str, *, deadline: float) -> None:
    """Accept only the exact agreement version attested by the owner."""
    _require_user_agreement_authorization()
    _require_agreement_window(window, title, deadline=deadline)
    _pointer_click(
        window,
        44,
        392,
        "confirm the JetBrains User Agreement checkbox",
        deadline=deadline,
    )
    _bounded_poll_sleep(deadline)
    _require_agreement_window(window, title, deadline=deadline)
    _pointer_click(
        window,
        542,
        432,
        "accept the JetBrains User Agreement",
        deadline=deadline,
    )


def _is_islands_popup(
    window: str,
    project: str,
    *,
    deadline: float | None = None,
) -> bool:
    """Match only the pinned onboarding transient owned by the project frame."""
    title = _window_name(window, deadline=deadline)
    try:
        project_id = int(project)
    except ValueError:
        return False
    return (
        title is not None
        and not title.strip()
        and _window_geometry(window, deadline=deadline) == (386, 486)
        and _window_is_root_child(window, deadline=deadline)
        and _window_transient_for(window, deadline=deadline) == project_id
    )


def find_islands_popup(deadline: float, project: str) -> str:
    """Wait for the exact late first-run onboarding transient."""
    while time.monotonic() < deadline:
        result = _xdotool(
            "search",
            "--onlyvisible",
            "--class",
            "jetbrains-.*",
            deadline=deadline,
        )
        if result.returncode == 0:
            for window in reversed(result.stdout.splitlines()):
                if _is_islands_popup(window, project, deadline=deadline):
                    return window
        _bounded_poll_sleep(deadline)
    raise RuntimeError("IntelliJ IDEA did not expose the pinned Islands onboarding popup")


def skip_islands_onboarding(deadline: float, project: str) -> None:
    """Dismiss the pinned onboarding transient and prove it disappeared."""
    popup = find_islands_popup(deadline, project)
    if not _is_islands_popup(popup, project, deadline=deadline):
        raise RuntimeError("refusing input outside the pinned Islands onboarding popup")
    _pointer_click(
        popup,
        191,
        444,
        "skip the JetBrains Islands quick tour",
        deadline=deadline,
    )
    while time.monotonic() < deadline:
        if _window_geometry(popup, deadline=deadline) is None:
            return
        _bounded_poll_sleep(deadline)
    raise RuntimeError("JetBrains Islands onboarding popup remained after Skip")


def write_acp_config(home: Path, proxy_argv: list[str], *, agent_name: str) -> None:
    """Write an owner-only ACP config containing exactly the pinned agent."""
    if not proxy_argv or not all(proxy_argv):
        raise ValueError("JetBrains ACP proxy argv must contain non-empty strings")
    if not Path(proxy_argv[0]).is_absolute():
        raise ValueError("JetBrains ACP proxy executable must be an absolute path")
    config_dir = home / ".jetbrains"
    config_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    config = {
        "default_mcp_settings": {"use_idea_mcp": False, "use_custom_mcp": False},
        "agent_servers": {
            agent_name: {
                "command": proxy_argv[0],
                "args": proxy_argv[1:],
                "env": {},
            }
        },
    }
    config_path = config_dir / "acp.json"
    config_path.write_text(json.dumps(config) + "\n", encoding="utf-8")
    config_path.chmod(0o600)


def write_idea_profile(config_root: Path) -> None:
    """Write the isolated IDEA keymap and selector-registry profile."""
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


def idea_command(
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
