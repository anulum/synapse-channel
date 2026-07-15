# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — fail-closed JetBrains X11 window selection
"""Verify isolated IDEA configuration and first-run legal safeguards."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from e2e.opencode_editors import (
    jetbrains_setup,
)
from e2e.opencode_editors.jetbrains_setup import (
    idea_command as _idea_command,
)
from e2e.opencode_editors.jetbrains_setup import (
    write_acp_config as _write_acp_config,
)
from e2e.opencode_editors.jetbrains_setup import (
    write_idea_profile as _write_idea_profile,
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
    keymap = (tmp_path / "keymaps" / "SynapseE2E.xml").read_text(encoding="utf-8")
    assert "NewChatAgentSelectorAction" in keymap
    assert "AIAssistant.Chat.SendActions.Send" not in keymap


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

    _write_acp_config(
        home,
        ["/opt/opencode", "acp"],
        agent_name="SYNAPSE OpenCode E2E",
    )

    config = home / ".jetbrains" / "acp.json"
    assert config.stat().st_mode & 0o777 == 0o600
    assert config.read_text(encoding="utf-8") == (
        '{"default_mcp_settings": {"use_idea_mcp": false, "use_custom_mcp": false}, '
        '"agent_servers": {"SYNAPSE OpenCode E2E": {"command": "/opt/opencode", '
        '"args": ["acp"], "env": {}}}}\n'
    )


@pytest.mark.parametrize(
    ("proxy_argv", "message"),
    [
        ([], "must contain non-empty strings"),
        (["/opt/opencode", ""], "must contain non-empty strings"),
        (["opencode", "acp"], "must be an absolute path"),
    ],
)
def test_acp_config_rejects_empty_or_relative_proxy_commands(
    tmp_path: Path,
    proxy_argv: list[str],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        _write_acp_config(
            tmp_path,
            proxy_argv,
            agent_name="SYNAPSE OpenCode E2E",
        )


def test_first_run_refuses_automated_legal_acceptance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SYNAPSE_JETBRAINS_EULA_ACCEPTED_VERSION", raising=False)
    monkeypatch.setattr(
        jetbrains_setup,
        "find_first_run_dialog",
        lambda _deadline: ("123", "IntelliJ IDEA User Agreement"),
    )

    with pytest.raises(
        RuntimeError,
        match="SYNAPSE_JETBRAINS_EULA_ACCEPTED_VERSION=2.0",
    ):
        jetbrains_setup.complete_first_run_agreements(1.0)


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
        jetbrains_setup,
        "find_first_run_dialog",
        lambda _deadline: next(dialogs),
    )
    monkeypatch.setattr(
        jetbrains_setup,
        "_require_agreement_window",
        lambda window, title, **_kwargs: checks.append((window, title)),
    )
    monkeypatch.setattr(
        jetbrains_setup,
        "_pointer_click",
        lambda window, x, y, action, **_kwargs: clicks.append((window, x, y, action)),
    )
    jetbrains_setup.complete_first_run_agreements(float("inf"))

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
        jetbrains_setup._require_user_agreement_authorization()


def test_first_run_and_project_discovery_require_exact_top_level_geometry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        jetbrains_setup,
        "_xdotool",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, "bad\ngood\n", ""),
    )
    monkeypatch.setattr(
        jetbrains_setup,
        "_window_name",
        lambda window, **_kwargs: "IntelliJ IDEA User Agreement" if window == "good" else "other",
    )
    monkeypatch.setattr(
        jetbrains_setup,
        "_window_geometry",
        lambda window, **_kwargs: (600, 460) if window == "good" else (100, 100),
    )
    monkeypatch.setattr(
        jetbrains_setup,
        "_window_is_root_child",
        lambda window, **_kwargs: window == "good",
    )
    assert jetbrains_setup.find_first_run_dialog(float("inf")) == (
        "good",
        "IntelliJ IDEA User Agreement",
    )

    monkeypatch.setattr(
        jetbrains_setup,
        "_window_geometry",
        lambda window, **_kwargs: (1400, 1000) if window == "good" else None,
    )
    assert jetbrains_setup.find_project_window(float("inf")) == "good"


def test_setup_discovery_retries_and_short_circuits_every_invalid_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_run_calls = 0
    sleeps: list[str] = []

    def first_run_search(*_args: str, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal first_run_calls
        first_run_calls += 1
        if first_run_calls <= 2:
            return subprocess.CompletedProcess([], 1, "", "not ready")
        return subprocess.CompletedProcess(
            [],
            0,
            "good\nwrong-root\nwrong-geometry\nwrong-title\n",
            "",
        )

    monkeypatch.setattr(jetbrains_setup, "_xdotool", first_run_search)
    monkeypatch.setattr(
        jetbrains_setup,
        "_window_name",
        lambda window, **_kwargs: (
            "wrong" if window == "wrong-title" else "IntelliJ IDEA User Agreement"
        ),
    )
    monkeypatch.setattr(
        jetbrains_setup,
        "_window_geometry",
        lambda window, **_kwargs: (1, 1) if window == "wrong-geometry" else (600, 460),
    )
    monkeypatch.setattr(
        jetbrains_setup,
        "_window_is_root_child",
        lambda window, **_kwargs: window != "wrong-root",
    )
    monkeypatch.setattr(
        jetbrains_setup,
        "_bounded_poll_sleep",
        lambda _deadline: sleeps.append("first-run"),
    )
    assert jetbrains_setup.find_first_run_dialog(float("inf"))[0] == "good"
    assert sleeps == ["first-run"]

    project_calls = 0

    def project_search(*_args: str, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal project_calls
        project_calls += 1
        if project_calls == 1:
            return subprocess.CompletedProcess([], 1, "", "not ready")
        return subprocess.CompletedProcess(
            [],
            0,
            "good\nwrong-root\nshort\nnarrow\nmissing\n",
            "",
        )

    geometries = {
        "missing": None,
        "narrow": (640, 1000),
        "short": (1400, 460),
        "wrong-root": (1400, 1000),
        "good": (1400, 1000),
    }
    monkeypatch.setattr(jetbrains_setup, "_xdotool", project_search)
    monkeypatch.setattr(
        jetbrains_setup,
        "_window_geometry",
        lambda window, **_kwargs: geometries[window],
    )
    monkeypatch.setattr(
        jetbrains_setup,
        "_window_is_root_child",
        lambda window, **_kwargs: window != "wrong-root",
    )
    assert jetbrains_setup.find_project_window(float("inf")) == "good"
    assert sleeps == ["first-run", "first-run"]


def test_setup_discovery_and_agreement_validation_time_out_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(RuntimeError, match="recognised pinned first-run dialog"):
        jetbrains_setup.find_first_run_dialog(0.0)
    with pytest.raises(RuntimeError, match="visible project window"):
        jetbrains_setup.find_project_window(0.0)

    monkeypatch.setattr(jetbrains_setup, "_window_geometry", lambda *_a, **_k: None)
    monkeypatch.setattr(jetbrains_setup, "_window_name", lambda *_a, **_k: "wrong")
    monkeypatch.setattr(jetbrains_setup, "_window_is_root_child", lambda *_a, **_k: False)
    with pytest.raises(RuntimeError, match="outside the pinned semantic UI"):
        jetbrains_setup._require_agreement_window("123", "Data Sharing")

    monkeypatch.setattr(
        jetbrains_setup,
        "find_first_run_dialog",
        lambda _deadline: ("123", "IntelliJ IDEA User Agreement"),
    )
    monkeypatch.setattr(jetbrains_setup, "_accept_user_agreement", lambda *_a, **_k: None)
    with pytest.raises(RuntimeError, match="did not advance to Data Sharing"):
        jetbrains_setup.complete_first_run_agreements(0.0)


@pytest.mark.parametrize(
    ("geometry", "title", "root_child"),
    [
        ((1, 1), "Data Sharing", True),
        ((600, 460), "wrong", True),
        ((600, 460), "Data Sharing", False),
    ],
)
def test_agreement_validator_rejects_each_independent_invariant(
    monkeypatch: pytest.MonkeyPatch,
    geometry: tuple[int, int],
    title: str,
    root_child: bool,
) -> None:
    monkeypatch.setattr(jetbrains_setup, "_window_geometry", lambda *_a, **_k: geometry)
    monkeypatch.setattr(jetbrains_setup, "_window_name", lambda *_a, **_k: title)
    monkeypatch.setattr(jetbrains_setup, "_window_is_root_child", lambda *_a, **_k: root_child)
    with pytest.raises(RuntimeError, match="outside the pinned semantic UI"):
        jetbrains_setup._require_agreement_window("123", "Data Sharing")


def test_agreement_flow_retries_transition_and_accepts_direct_data_sharing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dialogs = iter(
        [
            ("eula", "IntelliJ IDEA User Agreement"),
            ("loading", "Loading"),
            ("sharing", "Data Sharing"),
        ]
    )
    sleeps: list[bool] = []
    monkeypatch.setattr(jetbrains_setup, "find_first_run_dialog", lambda _deadline: next(dialogs))
    monkeypatch.setattr(jetbrains_setup, "_accept_user_agreement", lambda *_a, **_k: None)
    monkeypatch.setattr(jetbrains_setup, "_require_agreement_window", lambda *_a, **_k: None)
    monkeypatch.setattr(jetbrains_setup, "_pointer_click", lambda *_a, **_k: None)
    monkeypatch.setattr(
        jetbrains_setup,
        "_bounded_poll_sleep",
        lambda _deadline: sleeps.append(True),
    )
    jetbrains_setup.complete_first_run_agreements(float("inf"))
    assert sleeps == [True]

    monkeypatch.setattr(
        jetbrains_setup,
        "find_first_run_dialog",
        lambda _deadline: ("sharing", "Data Sharing"),
    )
    jetbrains_setup.complete_first_run_agreements(float("inf"))


def test_islands_popup_discovery_and_skip_enforce_transient_ownership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(jetbrains_setup, "_window_name", lambda *_a, **_k: "")
    monkeypatch.setattr(jetbrains_setup, "_window_geometry", lambda *_a, **_k: (386, 486))
    monkeypatch.setattr(jetbrains_setup, "_window_is_root_child", lambda *_a, **_k: True)
    monkeypatch.setattr(jetbrains_setup, "_window_transient_for", lambda *_a, **_k: 123)
    assert jetbrains_setup._is_islands_popup("popup", "123") is True
    assert jetbrains_setup._is_islands_popup("popup", "invalid") is False

    monkeypatch.setattr(
        jetbrains_setup,
        "_xdotool",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, "other\npopup\n", ""),
    )
    assert jetbrains_setup.find_islands_popup(float("inf"), "123") == "popup"
    with pytest.raises(RuntimeError, match="pinned Islands onboarding popup"):
        jetbrains_setup.find_islands_popup(0.0, "123")

    clicks: list[str] = []
    monkeypatch.setattr(jetbrains_setup, "find_islands_popup", lambda *_a, **_k: "popup")
    monkeypatch.setattr(
        jetbrains_setup,
        "_pointer_click",
        lambda *_args, **_kwargs: clicks.append("clicked"),
    )
    geometries = iter([(386, 486), (386, 486), None])
    monkeypatch.setattr(
        jetbrains_setup,
        "_window_geometry",
        lambda *_args, **_kwargs: next(geometries),
    )
    monkeypatch.setattr(jetbrains_setup, "_bounded_poll_sleep", lambda _deadline: None)
    jetbrains_setup.skip_islands_onboarding(float("inf"), "123")
    assert clicks == ["clicked"]


@pytest.mark.parametrize(
    ("title", "geometry", "root_child", "transient"),
    [
        (None, (386, 486), True, 123),
        ("title", (386, 486), True, 123),
        ("", (1, 1), True, 123),
        ("", (386, 486), False, 123),
        ("", (386, 486), True, 124),
    ],
)
def test_islands_popup_rejects_each_independent_invariant(
    monkeypatch: pytest.MonkeyPatch,
    title: str | None,
    geometry: tuple[int, int],
    root_child: bool,
    transient: int,
) -> None:
    monkeypatch.setattr(jetbrains_setup, "_window_name", lambda *_a, **_k: title)
    monkeypatch.setattr(jetbrains_setup, "_window_geometry", lambda *_a, **_k: geometry)
    monkeypatch.setattr(jetbrains_setup, "_window_is_root_child", lambda *_a, **_k: root_child)
    monkeypatch.setattr(jetbrains_setup, "_window_transient_for", lambda *_a, **_k: transient)
    assert jetbrains_setup._is_islands_popup("popup", "123") is False


def test_islands_discovery_retries_once_before_owned_popup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    sleeps: list[bool] = []

    def search(*_args: str, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return subprocess.CompletedProcess([], 1, "", "not ready")
        return subprocess.CompletedProcess([], 0, "popup\nother\n", "")

    monkeypatch.setattr(jetbrains_setup, "_xdotool", search)
    monkeypatch.setattr(
        jetbrains_setup,
        "_is_islands_popup",
        lambda window, *_args, **_kwargs: window == "popup",
    )
    monkeypatch.setattr(
        jetbrains_setup,
        "_bounded_poll_sleep",
        lambda _deadline: sleeps.append(True),
    )
    assert jetbrains_setup.find_islands_popup(float("inf"), "123") == "popup"
    assert sleeps == [True]


def test_setup_candidate_exhaustion_and_valid_agreement_cover_all_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_results = iter(
        [
            subprocess.CompletedProcess([], 0, "invalid\n", ""),
            subprocess.CompletedProcess([], 0, "good\n", ""),
        ]
    )
    monkeypatch.setattr(jetbrains_setup, "_xdotool", lambda *_a, **_k: next(first_results))
    monkeypatch.setattr(
        jetbrains_setup,
        "_window_name",
        lambda window, **_kwargs: "Data Sharing" if window == "good" else "wrong",
    )
    monkeypatch.setattr(jetbrains_setup, "_window_geometry", lambda *_a, **_k: (600, 460))
    monkeypatch.setattr(jetbrains_setup, "_window_is_root_child", lambda *_a, **_k: True)
    assert jetbrains_setup.find_first_run_dialog(float("inf")) == ("good", "Data Sharing")

    project_results = iter(
        [
            subprocess.CompletedProcess([], 0, "invalid\n", ""),
            subprocess.CompletedProcess([], 0, "good\n", ""),
        ]
    )
    monkeypatch.setattr(jetbrains_setup, "_xdotool", lambda *_a, **_k: next(project_results))
    monkeypatch.setattr(
        jetbrains_setup,
        "_window_geometry",
        lambda window, **_kwargs: (1400, 1000) if window == "good" else None,
    )
    monkeypatch.setattr(jetbrains_setup, "_bounded_poll_sleep", lambda _deadline: None)
    assert jetbrains_setup.find_project_window(float("inf")) == "good"

    monkeypatch.setattr(jetbrains_setup, "_window_geometry", lambda *_a, **_k: (600, 460))
    monkeypatch.setattr(jetbrains_setup, "_window_name", lambda *_a, **_k: "Data Sharing")
    jetbrains_setup._require_agreement_window("good", "Data Sharing")

    island_results = iter(
        [
            subprocess.CompletedProcess([], 0, "invalid\n", ""),
            subprocess.CompletedProcess([], 0, "popup\n", ""),
        ]
    )
    monkeypatch.setattr(jetbrains_setup, "_xdotool", lambda *_a, **_k: next(island_results))
    monkeypatch.setattr(
        jetbrains_setup,
        "_is_islands_popup",
        lambda window, *_args, **_kwargs: window == "popup",
    )
    assert jetbrains_setup.find_islands_popup(float("inf"), "123") == "popup"


def test_islands_skip_rejects_drift_and_open_popup_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(jetbrains_setup, "find_islands_popup", lambda *_a, **_k: "popup")
    monkeypatch.setattr(jetbrains_setup, "_is_islands_popup", lambda *_a, **_k: False)
    with pytest.raises(RuntimeError, match="outside the pinned Islands"):
        jetbrains_setup.skip_islands_onboarding(0.0, "123")

    monkeypatch.setattr(jetbrains_setup, "_is_islands_popup", lambda *_a, **_k: True)
    monkeypatch.setattr(jetbrains_setup, "_pointer_click", lambda *_a, **_k: None)
    with pytest.raises(RuntimeError, match="remained after Skip"):
        jetbrains_setup.skip_islands_onboarding(0.0, "123")
