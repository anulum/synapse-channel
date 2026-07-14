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
    _window_parentage,
    _write_acp_config,
    _xprop_window_id,
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
    monkeypatch.setattr(
        jetbrains_client,
        "_find_first_run_dialog",
        lambda _deadline: ("123", "IntelliJ IDEA User Agreement"),
    )

    with pytest.raises(RuntimeError, match="v2.0 requires explicit repository-owner"):
        jetbrains_client._complete_first_run_agreements(1.0)
