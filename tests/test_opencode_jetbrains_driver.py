# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — fail-closed JetBrains X11 window selection
"""Lock the top-level X11 parent invariant used by the real IDEA driver."""

from __future__ import annotations

from pathlib import Path

import pytest

from e2e.opencode_editors import jetbrains_client
from e2e.opencode_editors.jetbrains_client import (
    _idea_command,
    _wait_for_idea_log,
    _window_parentage,
    _write_acp_config,
    _write_idea_profile,
    _xprop_window_id,
)


def test_idea_log_wait_accepts_only_the_requested_readiness_marker(tmp_path: Path) -> None:
    marker = "No session managers found for agent 'SYNAPSE OpenCode E2E'"
    (tmp_path / "idea.log").write_text(f"{marker}\n", encoding="utf-8")

    _wait_for_idea_log(
        tmp_path,
        marker,
        float("inf"),
        lambda: None,
    )

    with pytest.raises(RuntimeError, match="IDEA log never contained"):
        _wait_for_idea_log(
            tmp_path,
            "other marker",
            0.0,
            lambda: None,
        )


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
    assert "NewChatAgentSelectorAction" in (tmp_path / "keymaps" / "SynapseE2E.xml").read_text(
        encoding="utf-8"
    )


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
        lambda window, title: checks.append((window, title)),
    )
    monkeypatch.setattr(
        jetbrains_client,
        "_pointer_click",
        lambda window, x, y, action: clicks.append((window, x, y, action)),
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
