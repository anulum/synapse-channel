# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — fail-closed JetBrains X11 window selection
"""Lock the top-level X11 parent invariant used by the real IDEA driver."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

from e2e.opencode_editors import jetbrains_client
from e2e.opencode_editors.jetbrains_client import (
    _CHAT_READY_MARKERS,
    _idea_command,
    _wait_for_idea_log,
    _window_parentage,
    _write_acp_config,
    _write_idea_profile,
    _xprop_window_id,
)


def test_idea_log_wait_requires_all_readiness_markers_in_order(tmp_path: Path) -> None:
    markers = (
        "Required plugins check passed",
        "Starting ACP client session ",
        "Received notification: AvailableCommandsUpdate",
    )
    idea_log = tmp_path / "idea.log"
    idea_log.write_text("\n".join(markers) + "\n", encoding="utf-8")

    _wait_for_idea_log(
        tmp_path,
        markers,
        float("inf"),
        lambda: None,
    )

    idea_log.write_text("\n".join(reversed(markers)) + "\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="IDEA log never contained"):
        _wait_for_idea_log(
            tmp_path,
            markers,
            0.0,
            lambda: None,
        )


def test_idea_log_wait_fails_closed_when_idea_exits(tmp_path: Path) -> None:
    (tmp_path / "idea.log").write_text("Required plugins check passed\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="IntelliJ IDEA exited before log evidence"):
        _wait_for_idea_log(
            tmp_path,
            (
                "Required plugins check passed",
                "Starting ACP client session ",
            ),
            float("inf"),
            lambda: 1,
        )


def test_idea_log_wait_rejects_an_empty_readiness_contract(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="at least one IDEA log marker is required"):
        _wait_for_idea_log(tmp_path, (), float("inf"), lambda: None)


def test_idea_log_wait_rejects_nonpositive_retry_interval(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="retry interval must be positive"):
        _wait_for_idea_log(
            tmp_path,
            "ready",
            float("inf"),
            lambda: None,
            retry=lambda: None,
            retry_interval_seconds=0.0,
        )


def test_idea_log_wait_retries_idempotent_ui_action_until_ready(tmp_path: Path) -> None:
    attempts = 0

    def expose_ready_marker() -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            (tmp_path / "idea.log").write_text("chat input ready\n", encoding="utf-8")

    _wait_for_idea_log(
        tmp_path,
        "chat input ready",
        time.monotonic() + 1.0,
        lambda: None,
        retry=expose_ready_marker,
        retry_interval_seconds=0.01,
    )

    assert attempts == 2


def test_chat_readiness_uses_stable_lifecycle_events(tmp_path: Path) -> None:
    idea_log = tmp_path / "idea.log"
    idea_log.write_text(
        "2026-07-15 AcpSessionLifecycleManagerRegistry - "
        "No session managers found for agent 'SYNAPSE OpenCode E2E'\n",
        encoding="utf-8",
    )

    _wait_for_idea_log(
        tmp_path,
        _CHAT_READY_MARKERS,
        float("inf"),
        lambda: None,
    )

    assert "AIAssistantInputSendAction#presentation" not in idea_log.read_text(encoding="utf-8")


def test_idea_profile_enables_the_pinned_agent_selector_before_startup(
    tmp_path: Path,
) -> None:
    _write_idea_profile(tmp_path)

    assert (tmp_path / "options" / "ide.general.xml").read_text(encoding="utf-8") == (
        "<application>\n"
        '  <component name="Registry">\n'
        '    <entry key="llm.chat.new.chat.and.agent.selector.enabled" '
        'value="true" />\n'
        "  </component>\n"
        "</application>\n"
    )
    keymap = (tmp_path / "keymaps" / "SynapseE2E.xml").read_text(encoding="utf-8")
    assert "NewChatAgentSelectorAction" in keymap
    assert "AIAssistant.Chat.SendActions.Send" not in keymap


def test_xwininfo_parentage_distinguishes_dialog_from_content_child() -> None:
    dialog = """
  Root window id: 0x1ff (the root window) (has no name)
  Parent window id: 0x1ff (the root window) (has no name)
     2 children:
"""
    content = """
  Root window id: 0x1ff (the root window) (has no name)
  Parent window id: 0x200051 "Data Sharing"
     0 children.
"""

    assert _window_parentage(dialog) == ("0x1ff", "0x1ff")
    assert _window_parentage(content) == ("0x1ff", "0x200051")


def test_xwininfo_parentage_fails_closed_on_missing_fields() -> None:
    assert _window_parentage("") == (None, None)
    assert _window_parentage("Root window id:") == (None, None)


def test_xprop_transient_parent_parser_accepts_only_a_window_id() -> None:
    result = "WM_TRANSIENT_FOR(WINDOW): window id # 0x40006e\n"

    assert _xprop_window_id(result) == 0x40006E
    assert _xprop_window_id("WM_TRANSIENT_FOR:  not found.\n") is None
    assert _xprop_window_id("WM_TRANSIENT_FOR(WINDOW): window id # invalid\n") is None


def test_agent_selector_popup_requires_pinned_window_invariants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(jetbrains_client, "_window_name", lambda _window, **_kwargs: "win0")
    monkeypatch.setattr(jetbrains_client, "_window_geometry", lambda _window, **_kwargs: (310, 407))
    monkeypatch.setattr(jetbrains_client, "_window_is_root_child", lambda _window, **_kwargs: True)
    monkeypatch.setattr(jetbrains_client, "_window_transient_for", lambda _window, **_kwargs: 123)

    assert jetbrains_client._is_agent_selector_popup("selector", "123") is True
    assert jetbrains_client._is_agent_selector_popup("selector", "invalid") is False

    monkeypatch.setattr(jetbrains_client, "_window_name", lambda _window, **_kwargs: "other")
    assert jetbrains_client._is_agent_selector_popup("selector", "123") is False
    monkeypatch.setattr(jetbrains_client, "_window_name", lambda _window, **_kwargs: "win0")

    monkeypatch.setattr(jetbrains_client, "_window_geometry", lambda _window, **_kwargs: (311, 407))
    assert jetbrains_client._is_agent_selector_popup("selector", "123") is False
    monkeypatch.setattr(jetbrains_client, "_window_geometry", lambda _window, **_kwargs: (310, 407))

    monkeypatch.setattr(jetbrains_client, "_window_is_root_child", lambda _window, **_kwargs: False)
    assert jetbrains_client._is_agent_selector_popup("selector", "123") is False
    monkeypatch.setattr(jetbrains_client, "_window_is_root_child", lambda _window, **_kwargs: True)

    monkeypatch.setattr(jetbrains_client, "_window_transient_for", lambda _window, **_kwargs: 124)
    assert jetbrains_client._is_agent_selector_popup("selector", "123") is False


def test_idea_command_binds_jvm_home_to_the_isolated_profile(tmp_path: Path) -> None:
    command = _idea_command(
        tmp_path / "idea.sh",
        home=tmp_path / "home",
        config_root=tmp_path / "config",
        system_root=tmp_path / "system",
        plugins=tmp_path / "plugins",
        log_root=tmp_path / "log",
        project=tmp_path / "project",
    )

    assert command[1] == f"-Duser.home={tmp_path / 'home'}"
    assert command[-1] == str(tmp_path / "project")


def test_chat_composer_focus_targets_the_validated_project_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actions: list[tuple[str, ...]] = []
    clicks: list[tuple[str, int, int, str]] = []
    monkeypatch.setattr(
        jetbrains_client, "_window_geometry", lambda _window, **_kwargs: (1400, 1000)
    )
    monkeypatch.setattr(jetbrains_client, "_window_is_root_child", lambda _window, **_kwargs: True)
    monkeypatch.setattr(
        jetbrains_client,
        "_checked_xdotool",
        lambda _action, *args, **_kwargs: actions.append(args),
    )
    monkeypatch.setattr(
        jetbrains_client,
        "_pointer_click",
        lambda window, x, y, action, **_kwargs: clicks.append((window, x, y, action)),
    )
    monkeypatch.setattr(
        jetbrains_client,
        "_xdotool",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, "123\n", ""),
    )

    jetbrains_client._focus_chat_composer("123")

    assert actions == [("windowfocus", "--sync", "123")]
    assert clicks == [("123", 1160, 870, "focus the JetBrains AI Chat composer")]


def test_chat_composer_focus_rejects_ambient_keyboard_focus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        jetbrains_client, "_window_geometry", lambda _window, **_kwargs: (1400, 1000)
    )
    monkeypatch.setattr(jetbrains_client, "_window_is_root_child", lambda _window, **_kwargs: True)
    monkeypatch.setattr(
        jetbrains_client,
        "_checked_xdotool",
        lambda _action, *_args, **_kwargs: None,
    )
    monkeypatch.setattr(jetbrains_client, "_pointer_click", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        jetbrains_client,
        "_xdotool",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, "124\n", ""),
    )

    with pytest.raises(RuntimeError, match="without project-frame keyboard focus"):
        jetbrains_client._focus_chat_composer("123")


@pytest.mark.parametrize(
    ("returncode", "output"),
    [(1, ""), (0, "not-a-window")],
)
def test_chat_composer_focus_rejects_unprovable_keyboard_focus(
    monkeypatch: pytest.MonkeyPatch,
    returncode: int,
    output: str,
) -> None:
    monkeypatch.setattr(
        jetbrains_client, "_window_geometry", lambda _window, **_kwargs: (1400, 1000)
    )
    monkeypatch.setattr(jetbrains_client, "_window_is_root_child", lambda _window, **_kwargs: True)
    monkeypatch.setattr(
        jetbrains_client,
        "_checked_xdotool",
        lambda _action, *_args, **_kwargs: None,
    )
    monkeypatch.setattr(jetbrains_client, "_pointer_click", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        jetbrains_client,
        "_xdotool",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], returncode, output, ""),
    )

    with pytest.raises(RuntimeError, match="focused=None"):
        jetbrains_client._focus_chat_composer("123")


def test_chat_composer_focus_rejects_an_invalid_project_xid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        jetbrains_client, "_window_geometry", lambda _window, **_kwargs: (1400, 1000)
    )
    monkeypatch.setattr(jetbrains_client, "_window_is_root_child", lambda _window, **_kwargs: True)
    monkeypatch.setattr(
        jetbrains_client,
        "_checked_xdotool",
        lambda _action, *_args, **_kwargs: None,
    )
    monkeypatch.setattr(jetbrains_client, "_pointer_click", lambda *_args, **_kwargs: None)

    with pytest.raises(RuntimeError, match="invalid XID"):
        jetbrains_client._focus_chat_composer("project")


@pytest.mark.parametrize("geometry", [None, (999, 1000), (1400, 699)])
def test_chat_composer_focus_rejects_an_unvalidated_project_frame(
    monkeypatch: pytest.MonkeyPatch,
    geometry: tuple[int, int] | None,
) -> None:
    monkeypatch.setattr(jetbrains_client, "_window_geometry", lambda _window, **_kwargs: geometry)
    monkeypatch.setattr(jetbrains_client, "_window_is_root_child", lambda _window, **_kwargs: True)

    with pytest.raises(RuntimeError, match="outside a validated project frame"):
        jetbrains_client._focus_chat_composer("project")


def test_chat_composer_focus_rejects_a_nested_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        jetbrains_client, "_window_geometry", lambda _window, **_kwargs: (1400, 1000)
    )
    monkeypatch.setattr(jetbrains_client, "_window_is_root_child", lambda _window, **_kwargs: False)

    with pytest.raises(RuntimeError, match="root_child=False"):
        jetbrains_client._focus_chat_composer("project")


def test_chat_prompt_submission_targets_the_focused_swing_widget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    focused: list[str] = []
    actions: list[tuple[str, tuple[str, ...]]] = []
    monkeypatch.setattr(
        jetbrains_client,
        "_focus_chat_composer",
        lambda window, **_kwargs: focused.append(window),
    )
    monkeypatch.setattr(
        jetbrains_client,
        "_checked_xdotool",
        lambda action, *args, **_kwargs: actions.append((action, args)),
    )

    jetbrains_client._submit_chat_prompt("project", "governed prompt")

    assert focused == ["project"]
    assert actions == [
        ("clear the ACP prompt composer", ("key", "ctrl+a")),
        (
            "type the ACP prompt",
            ("type", "--delay", "1", "--", "governed prompt"),
        ),
        (
            "invoke the pinned AI Chat send action",
            ("key", "Return"),
        ),
    ]


def test_acp_config_is_private_and_contains_only_the_selected_agent(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()

    _write_acp_config(home, ["/opt/opencode", "acp"])

    config = home / ".jetbrains" / "acp.json"
    assert config.stat().st_mode & 0o777 == 0o600
    assert config.read_text(encoding="utf-8") == (
        '{"default_mcp_settings": {"use_idea_mcp": false, "use_custom_mcp": false}, '
        '"agent_servers": {"SYNAPSE OpenCode E2E": {"command": "/opt/opencode", '
        '"args": ["acp"], "env": {}}}}\n'
    )


def test_first_run_refuses_automated_legal_acceptance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SYNAPSE_JETBRAINS_EULA_ACCEPTED_VERSION", raising=False)
    monkeypatch.setattr(
        jetbrains_client,
        "_find_first_run_dialog",
        lambda _deadline: ("123", "IntelliJ IDEA User Agreement"),
    )

    with pytest.raises(
        RuntimeError,
        match="SYNAPSE_JETBRAINS_EULA_ACCEPTED_VERSION=2.0",
    ):
        jetbrains_client._complete_first_run_agreements(1.0)


def test_first_run_accepts_only_attested_v2_then_declines_telemetry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dialogs = iter(
        [
            ("eula", "IntelliJ IDEA User Agreement"),
            ("sharing", "Data Sharing"),
        ]
    )
    checks: list[tuple[str, str]] = []
    clicks: list[tuple[str, int, int, str]] = []
    monkeypatch.setenv("SYNAPSE_JETBRAINS_EULA_ACCEPTED_VERSION", "2.0")
    monkeypatch.setattr(
        jetbrains_client,
        "_find_first_run_dialog",
        lambda _deadline: next(dialogs),
    )
    monkeypatch.setattr(
        jetbrains_client,
        "_require_agreement_window",
        lambda window, title, **_kwargs: checks.append((window, title)),
    )
    monkeypatch.setattr(
        jetbrains_client,
        "_pointer_click",
        lambda window, x, y, action, **_kwargs: clicks.append((window, x, y, action)),
    )
    jetbrains_client._complete_first_run_agreements(float("inf"))

    assert checks == [
        ("eula", "IntelliJ IDEA User Agreement"),
        ("eula", "IntelliJ IDEA User Agreement"),
        ("sharing", "Data Sharing"),
    ]
    assert [(window, x, y) for window, x, y, _action in clicks] == [
        ("eula", 44, 392),
        ("eula", 542, 432),
        ("sharing", 326, 432),
    ]


def test_user_agreement_rejects_wrong_attested_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SYNAPSE_JETBRAINS_EULA_ACCEPTED_VERSION", "2.1")

    with pytest.raises(RuntimeError, match="refusing owner attestation '2.1'"):
        jetbrains_client._require_user_agreement_authorization()
