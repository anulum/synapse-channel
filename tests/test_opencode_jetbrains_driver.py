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
import sys
import time
from pathlib import Path

import pytest

from e2e.opencode_editors import jetbrains_client
from e2e.opencode_editors.jetbrains_client import (
    _ACP_SESSION_COMPLETIONS,
    _ACP_SESSION_PREREQUISITE,
    _CHAT_READY_MARKERS,
    _idea_command,
    _open_agent_selector,
    _select_pinned_agent,
    _wait_for_idea_log,
    _wait_for_trace,
    _window_parentage,
    _write_acp_config,
    _write_idea_profile,
    _xprop_window_id,
)


def test_idea_log_wait_requires_all_ordered_markers(tmp_path: Path) -> None:
    markers = (_ACP_SESSION_PREREQUISITE, *_ACP_SESSION_COMPLETIONS)
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


def test_idea_log_wait_uses_a_bounded_contents_reader(tmp_path: Path) -> None:
    contents = "plugins ready\ncommands available\nsession started\n"
    reads: list[bool] = []

    def read_contents() -> str:
        reads.append(True)
        return contents

    _wait_for_idea_log(
        tmp_path,
        ("unused ordered marker",),
        float("inf"),
        lambda: None,
        matcher=lambda value: "plugins ready" in value and "session started" in value,
        contents_reader=read_contents,
    )

    assert reads == [True]


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


def test_idea_log_wait_checks_the_lifecycle_guard_before_success(tmp_path: Path) -> None:
    (tmp_path / "idea.log").write_text("ready\n", encoding="utf-8")
    guarded: list[bool] = []

    _wait_for_idea_log(
        tmp_path,
        "ready",
        float("inf"),
        lambda: None,
        guard=lambda: guarded.append(True),
    )

    assert guarded == [True]


def test_trace_wait_checks_the_lifecycle_guard_before_success(tmp_path: Path) -> None:
    trace = tmp_path / "trace.jsonl"
    trace.write_text('{"method":"initialize"}\n', encoding="utf-8")
    process = subprocess.Popen(  # nosec B603
        [sys.executable, "-c", "import time; time.sleep(10)"],
        text=True,
        start_new_session=True,
    )
    guarded: list[bool] = []
    try:
        _wait_for_trace(
            trace,
            '"method":"initialize"',
            float("inf"),
            process,
            guard=lambda: guarded.append(True),
        )
    finally:
        process.terminate()
        process.wait(timeout=5)

    assert guarded == [True]


def test_trace_wait_rejects_duplicate_lifecycle_before_matching_marker(
    tmp_path: Path,
) -> None:
    trace = tmp_path / "trace.jsonl"
    trace.write_text('{"method":"initialize"}\n', encoding="utf-8")
    process = subprocess.Popen(  # nosec B603
        [sys.executable, "-c", "import time; time.sleep(10)"],
        text=True,
        start_new_session=True,
    )

    def reject_duplicate() -> None:
        raise RuntimeError("duplicate lifecycle")

    try:
        with pytest.raises(RuntimeError, match="duplicate lifecycle"):
            _wait_for_trace(
                trace,
                '"method":"initialize"',
                float("inf"),
                process,
                guard=reject_duplicate,
            )
    finally:
        process.terminate()
        process.wait(timeout=5)


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
    monkeypatch.setattr(jetbrains_client, "_window_geometry", lambda _window, **_kwargs: (310, 407))
    monkeypatch.setattr(jetbrains_client, "_window_is_root_child", lambda _window, **_kwargs: True)
    monkeypatch.setattr(jetbrains_client, "_window_transient_for", lambda _window, **_kwargs: 123)

    assert jetbrains_client._is_agent_selector_popup("selector", "123") is True
    assert jetbrains_client._is_agent_selector_popup("selector", "invalid") is False


def test_agent_selector_popup_rejects_each_wrong_window_invariant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(jetbrains_client, "_window_geometry", lambda _window, **_kwargs: (311, 407))
    monkeypatch.setattr(jetbrains_client, "_window_is_root_child", lambda _window, **_kwargs: True)
    monkeypatch.setattr(jetbrains_client, "_window_transient_for", lambda _window, **_kwargs: 123)
    assert jetbrains_client._is_agent_selector_popup("selector", "123") is False

    monkeypatch.setattr(jetbrains_client, "_window_geometry", lambda _window, **_kwargs: (310, 407))
    monkeypatch.setattr(jetbrains_client, "_window_is_root_child", lambda _window, **_kwargs: False)
    assert jetbrains_client._is_agent_selector_popup("selector", "123") is False

    monkeypatch.setattr(jetbrains_client, "_window_is_root_child", lambda _window, **_kwargs: True)
    monkeypatch.setattr(jetbrains_client, "_window_transient_for", lambda _window, **_kwargs: 124)
    assert jetbrains_client._is_agent_selector_popup("selector", "123") is False


def test_agent_selector_popup_search_deduplicates_one_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch = (
        "WINDOW=456\nX=1\nY=2\nWIDTH=310\nHEIGHT=407\nSCREEN=0\n"
        "WINDOW=456\nX=1\nY=2\nWIDTH=310\nHEIGHT=407\nSCREEN=0\n"
    )
    calls: list[tuple[str, ...]] = []

    def batched_geometry(*args: str, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess([], 0, batch, "")

    monkeypatch.setattr(
        jetbrains_client,
        "_xdotool",
        batched_geometry,
    )
    monkeypatch.setattr(
        jetbrains_client,
        "_agent_selector_owner_matches",
        lambda window, project_id, **_kwargs: window == "456" and project_id == 123,
    )

    assert jetbrains_client._visible_agent_selector_popups("123", deadline=float("inf")) == ("456",)
    assert calls == [
        (
            "search",
            "--onlyvisible",
            "--class",
            "jetbrains-.*",
            "getwindowgeometry",
            "--shell",
            "%@",
        )
    ]


def test_agent_selector_popup_search_fails_closed_on_multiple_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch = (
        "WINDOW=456\nX=1\nY=2\nWIDTH=310\nHEIGHT=407\nSCREEN=0\n"
        "WINDOW=789\nX=3\nY=4\nWIDTH=310\nHEIGHT=407\nSCREEN=0\n"
    )
    monkeypatch.setattr(
        jetbrains_client,
        "_xdotool",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, batch, ""),
    )
    monkeypatch.setattr(
        jetbrains_client,
        "_agent_selector_owner_matches",
        lambda _window, _project_id, **_kwargs: True,
    )

    with pytest.raises(RuntimeError, match="multiple pinned ACP agent selector popups"):
        jetbrains_client._find_agent_selector_popup(float("inf"), "123")


def test_agent_selector_popup_search_prefilters_geometry_before_owner_queries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch = "WINDOW=456\nX=1\nY=2\nWIDTH=1400\nHEIGHT=1000\nSCREEN=0\n"
    owner_queries: list[str] = []

    def record_owner_query(window: str, _project_id: int, **_kwargs: object) -> bool:
        owner_queries.append(window)
        return True

    monkeypatch.setattr(
        jetbrains_client,
        "_xdotool",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, batch, ""),
    )
    monkeypatch.setattr(
        jetbrains_client,
        "_agent_selector_owner_matches",
        record_owner_query,
    )

    assert jetbrains_client._visible_agent_selector_popups("123", deadline=float("inf")) == ()
    assert owner_queries == []


def test_agent_selector_popup_search_rejects_malformed_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        jetbrains_client,
        "_xdotool",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, "WINDOW=456\nX=1\n", ""),
    )

    with pytest.raises(RuntimeError, match="malformed batched selector geometry"):
        jetbrains_client._visible_agent_selector_popups("123", deadline=float("inf"))


def test_agent_selector_popup_search_retries_only_while_guard_is_clean(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    matches = iter([(), ("456",)])
    retries: list[bool] = []
    guarded: list[bool] = []
    monkeypatch.setattr(
        jetbrains_client,
        "_visible_agent_selector_popups",
        lambda *_args, **_kwargs: next(matches),
    )

    selector = jetbrains_client._find_agent_selector_popup(
        float("inf"),
        "123",
        retry=lambda: retries.append(True),
        retry_interval_seconds=0.01,
        guard=lambda: guarded.append(True),
    )

    assert selector == "456"
    assert retries == [True]
    assert guarded == [True, True]


def test_agent_selector_popup_search_rejects_nonpositive_retry_interval() -> None:
    with pytest.raises(ValueError, match="selector retry interval must be positive"):
        jetbrains_client._find_agent_selector_popup(
            float("inf"),
            "123",
            retry_interval_seconds=0.0,
        )


def test_agent_selector_opens_only_from_the_pinned_project_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actions: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        jetbrains_client, "_window_geometry", lambda _window, **_kwargs: (1400, 1000)
    )
    monkeypatch.setattr(jetbrains_client, "_window_is_root_child", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        jetbrains_client,
        "_checked_xdotool",
        lambda _action, *args, **_kwargs: actions.append(args),
    )
    monkeypatch.setattr(
        jetbrains_client,
        "_find_agent_selector_popup",
        lambda _deadline, project, **kwargs: (
            kwargs["retry"](),
            f"selector-for-{project}",
        )[1],
    )

    assert _open_agent_selector("123", deadline=float("inf")) == "selector-for-123"
    assert actions == [
        ("windowfocus", "--sync", "123"),
        ("key", "--window", "123", "ctrl+alt+shift+k"),
    ]


@pytest.mark.parametrize(("geometry", "root_child"), [((1399, 1000), True), ((1400, 1000), False)])
def test_agent_selector_refuses_an_unpinned_project_frame(
    monkeypatch: pytest.MonkeyPatch,
    geometry: tuple[int, int],
    root_child: bool,
) -> None:
    monkeypatch.setattr(jetbrains_client, "_window_geometry", lambda _window, **_kwargs: geometry)
    monkeypatch.setattr(
        jetbrains_client, "_window_is_root_child", lambda *_args, **_kwargs: root_child
    )
    monkeypatch.setattr(
        jetbrains_client,
        "_visible_agent_selector_popups",
        lambda *_args, **_kwargs: (),
    )

    with pytest.raises(RuntimeError, match="outside the pinned project frame"):
        _open_agent_selector("123", deadline=float("inf"))


def test_xdotool_timeout_is_a_fail_closed_command_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(jetbrains_client, "_required_tool", lambda _name: "/usr/bin/xdotool")

    def time_out(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(["xdotool", "search"], 1.0)

    monkeypatch.setattr(subprocess, "run", time_out)

    completed = jetbrains_client._xdotool("search")

    assert completed.returncode == 124
    assert completed.stderr == "xdotool command timed out"


def test_pointer_click_moves_and_clicks_in_one_xdotool_invocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, tuple[str, ...], float | None]] = []
    monkeypatch.setattr(
        jetbrains_client,
        "_checked_xdotool",
        lambda action, *args, deadline=None: calls.append((action, args, deadline)),
    )
    monkeypatch.setattr(
        jetbrains_client,
        "_window_rectangle",
        lambda _window, **_kwargs: (0, 1106, 70, 310, 407),
    )

    jetbrains_client._pointer_click(
        "123",
        155,
        185,
        "select the pinned agent",
        deadline=42.0,
    )

    assert calls == [
        (
            "select the pinned agent",
            (
                "mousemove",
                "--screen",
                "0",
                "1261",
                "255",
                "sleep",
                "0.25",
                "click",
                "1",
            ),
            42.0,
        )
    ]


@pytest.mark.parametrize(
    ("rectangle", "point", "message"),
    [
        (None, (0, 0), "vanished X11 window"),
        ((0, 10, 20, 100, 100), (100, 50), "outside its X11 window"),
        ((0, 10, 20, 100, 100), (-1, 50), "outside its X11 window"),
    ],
)
def test_pointer_click_rejects_a_missing_window_or_out_of_bounds_point(
    monkeypatch: pytest.MonkeyPatch,
    rectangle: tuple[int, int, int, int, int] | None,
    point: tuple[int, int],
    message: str,
) -> None:
    monkeypatch.setattr(
        jetbrains_client,
        "_window_rectangle",
        lambda _window, **_kwargs: rectangle,
    )

    with pytest.raises(RuntimeError, match=message):
        jetbrains_client._pointer_click("123", *point, "test pointer input")


def test_agent_selector_clicks_the_pinned_row_once_and_proves_closure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    matches = iter([("selector",), ()])
    clicks: list[tuple[str, int, int, str]] = []
    guarded: list[bool] = []
    monkeypatch.setattr(
        jetbrains_client,
        "_is_agent_selector_popup",
        lambda selector, project, **_kwargs: selector == "selector" and project == "123",
    )
    monkeypatch.setattr(
        jetbrains_client,
        "_visible_agent_selector_popups",
        lambda _project, **_kwargs: next(matches),
    )
    monkeypatch.setattr(
        jetbrains_client,
        "_pointer_click",
        lambda window, x, y, action, **_kwargs: clicks.append((window, x, y, action)),
    )
    monkeypatch.setattr(jetbrains_client, "_window_geometry", lambda *_args, **_kwargs: None)

    _select_pinned_agent(
        "selector",
        "123",
        deadline=float("inf"),
        guard=lambda: guarded.append(True),
    )

    assert clicks == [("selector", 155, 185, "select the pinned SYNAPSE OpenCode ACP agent")]
    assert guarded == [True]


@pytest.mark.parametrize(
    ("popup_is_pinned", "matches", "message"),
    [
        (False, ("selector",), "outside the pinned ACP agent selector popup"),
        (True, (), "ambiguous JetBrains ACP agent selection"),
        (True, ("selector", "other"), "ambiguous JetBrains ACP agent selection"),
    ],
)
def test_agent_selector_refuses_unpinned_or_ambiguous_confirmation(
    monkeypatch: pytest.MonkeyPatch,
    popup_is_pinned: bool,
    matches: tuple[str, ...],
    message: str,
) -> None:
    monkeypatch.setattr(
        jetbrains_client,
        "_is_agent_selector_popup",
        lambda *_args, **_kwargs: popup_is_pinned,
    )
    monkeypatch.setattr(
        jetbrains_client,
        "_visible_agent_selector_popups",
        lambda *_args, **_kwargs: matches,
    )

    with pytest.raises(RuntimeError, match=message):
        _select_pinned_agent("selector", "123", deadline=float("inf"))


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
    monkeypatch.setattr(
        jetbrains_client,
        "_window_parent_ids",
        lambda _window, **_kwargs: (1, 1),
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


def test_chat_composer_focus_accepts_a_nested_swing_focus_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        jetbrains_client, "_window_geometry", lambda _window, **_kwargs: (1400, 1000)
    )
    monkeypatch.setattr(jetbrains_client, "_window_is_root_child", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(jetbrains_client, "_checked_xdotool", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(jetbrains_client, "_pointer_click", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        jetbrains_client,
        "_xdotool",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, "300\n", ""),
    )
    monkeypatch.setattr(
        jetbrains_client,
        "_window_parent_ids",
        lambda window, **_kwargs: {300: (1, 200), 200: (1, 123)}[window],
    )

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
    clicks: list[tuple[str, int, int, str]] = []
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
    monkeypatch.setattr(
        jetbrains_client,
        "_window_geometry",
        lambda _window, **_kwargs: (1400, 1000),
    )
    monkeypatch.setattr(
        jetbrains_client,
        "_window_is_root_child",
        lambda _window, **_kwargs: True,
    )
    monkeypatch.setattr(
        jetbrains_client,
        "_pointer_click",
        lambda window, x, y, action, **_kwargs: clicks.append((window, x, y, action)),
    )

    jetbrains_client._submit_chat_prompt("project", "governed prompt")

    assert focused == ["project"]
    assert actions == [
        ("clear the ACP prompt composer", ("key", "ctrl+a")),
        (
            "type the ACP prompt",
            ("type", "--delay", "1", "--", "governed prompt"),
        ),
    ]
    assert clicks == [("project", 1336, 924, "submit the JetBrains ACP prompt")]


@pytest.mark.parametrize(
    ("geometry", "root_child"),
    [((1399, 1000), True), ((1400, 1000), False)],
)
def test_chat_prompt_submission_refuses_an_unpinned_project_frame(
    monkeypatch: pytest.MonkeyPatch,
    geometry: tuple[int, int],
    root_child: bool,
) -> None:
    monkeypatch.setattr(jetbrains_client, "_focus_chat_composer", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(jetbrains_client, "_checked_xdotool", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        jetbrains_client,
        "_window_geometry",
        lambda _window, **_kwargs: geometry,
    )
    monkeypatch.setattr(
        jetbrains_client,
        "_window_is_root_child",
        lambda _window, **_kwargs: root_child,
    )

    with pytest.raises(RuntimeError, match="outside the pinned project frame"):
        jetbrains_client._submit_chat_prompt("project", "governed prompt")


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
