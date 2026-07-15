# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — fail-closed JetBrains X11 window selection
"""Verify fail-closed X11 discovery, focus, typing, and pointer input."""

from __future__ import annotations

import shutil
import subprocess
import time

import pytest

from e2e.opencode_editors import (
    jetbrains_x11_driver,
)
from e2e.opencode_editors.jetbrains_x11_driver import (
    _window_parentage,
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
    assert _xprop_window_id("WM_TRANSIENT_FOR(WINDOW): window id #\n") is None
    assert _xprop_window_id("WM_TRANSIENT_FOR(WINDOW): window id # invalid\n") is None


def test_xdotool_timeout_is_a_fail_closed_command_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(jetbrains_x11_driver, "_required_tool", lambda _name: "/usr/bin/xdotool")

    def time_out(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(["xdotool", "search"], 1.0)

    monkeypatch.setattr(subprocess, "run", time_out)

    completed = jetbrains_x11_driver._xdotool("search")

    assert completed.returncode == 124
    assert completed.stderr == "xdotool command timed out"


def test_tool_resolution_deadlines_and_bounded_sleep_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    with pytest.raises(RuntimeError, match="tool is unavailable"):
        jetbrains_x11_driver._required_tool("missing")
    monkeypatch.setattr(
        shutil,
        "which",
        lambda name: f"/usr/bin/{name}",
    )
    assert jetbrains_x11_driver._required_tool("xdotool") == "/usr/bin/xdotool"
    assert jetbrains_x11_driver._command_timeout(None) > 0

    monkeypatch.setattr(time, "monotonic", lambda: 10.0)
    assert jetbrains_x11_driver._command_timeout(11.0) == 1.0
    with pytest.raises(RuntimeError, match="phase deadline expired"):
        jetbrains_x11_driver._command_timeout(10.0)

    sleeps: list[float] = []
    monkeypatch.setattr(time, "sleep", sleeps.append)
    jetbrains_x11_driver._bounded_poll_sleep(10.0)
    jetbrains_x11_driver._bounded_poll_sleep(10.1)
    assert sleeps == [pytest.approx(0.1)]


def test_xdotool_success_and_checked_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(jetbrains_x11_driver, "_required_tool", lambda _name: "/usr/bin/xdotool")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 0, "ok", ""),
    )
    assert jetbrains_x11_driver._xdotool("search").stdout == "ok"
    jetbrains_x11_driver._checked_xdotool("search", "search")

    results = iter(
        [
            subprocess.CompletedProcess([], 1, "stdout detail", ""),
            subprocess.CompletedProcess([], 1, "", ""),
        ]
    )
    monkeypatch.setattr(jetbrains_x11_driver, "_xdotool", lambda *_a, **_k: next(results))
    with pytest.raises(RuntimeError, match="stdout detail"):
        jetbrains_x11_driver._checked_xdotool("search", "search")
    with pytest.raises(RuntimeError, match="no diagnostic"):
        jetbrains_x11_driver._checked_xdotool("search", "search")


def test_window_geometry_name_and_focus_parsers_cover_success_and_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results = iter(
        [
            subprocess.CompletedProcess([], 1, "", ""),
            subprocess.CompletedProcess(
                [],
                0,
                "WINDOW=123\nSCREEN=0\nX=10\nY=20\nWIDTH=300\nHEIGHT=400\n",
                "",
            ),
            subprocess.CompletedProcess([], 1, "", ""),
            subprocess.CompletedProcess([], 0, "title\n", ""),
            subprocess.CompletedProcess([], 1, "", ""),
            subprocess.CompletedProcess([], 0, "invalid", ""),
            subprocess.CompletedProcess([], 0, "0x123\n", ""),
        ]
    )
    monkeypatch.setattr(jetbrains_x11_driver, "_xdotool", lambda *_a, **_k: next(results))
    assert jetbrains_x11_driver._window_rectangle("123") is None
    assert jetbrains_x11_driver._window_rectangle("123") == (0, 10, 20, 300, 400)
    monkeypatch.setattr(jetbrains_x11_driver, "_window_rectangle", lambda *_a, **_k: None)
    assert jetbrains_x11_driver._window_geometry("123") is None
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_window_rectangle",
        lambda *_a, **_k: (0, 10, 20, 300, 400),
    )
    assert jetbrains_x11_driver._window_geometry("123") == (300, 400)
    assert jetbrains_x11_driver._window_name("123") is None
    assert jetbrains_x11_driver._window_name("123") == "title"
    assert jetbrains_x11_driver._focused_window_id() is None
    assert jetbrains_x11_driver._focused_window_id() is None
    assert jetbrains_x11_driver._focused_window_id() == 0x123


def test_xwininfo_and_transient_queries_parse_only_proven_ownership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_required_tool",
        lambda name: f"/usr/bin/{name}",
    )
    results = iter(
        [
            subprocess.CompletedProcess([], 1, "", ""),
            subprocess.CompletedProcess(
                [],
                0,
                "Root window id: 0x1\nParent window id: 0x1\n",
                "",
            ),
            subprocess.CompletedProcess([], 1, "", ""),
            subprocess.CompletedProcess(
                [],
                0,
                "Root window id: 0x1\nParent window id: 0x2\n",
                "",
            ),
            subprocess.CompletedProcess(
                [],
                0,
                "Root window id: invalid\nParent window id: 0x2\n",
                "",
            ),
            subprocess.CompletedProcess([], 1, "", ""),
            subprocess.CompletedProcess(
                [],
                0,
                "WM_TRANSIENT_FOR(WINDOW): window id # 0x123\n",
                "",
            ),
        ]
    )
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: next(results))
    assert jetbrains_x11_driver._window_is_root_child("123") is False
    assert jetbrains_x11_driver._window_is_root_child("123") is True
    assert jetbrains_x11_driver._window_parent_ids(123) == (None, None)
    assert jetbrains_x11_driver._window_parent_ids(123) == (1, 2)
    assert jetbrains_x11_driver._window_parent_ids(123) == (None, None)
    assert jetbrains_x11_driver._window_transient_for("123") is None
    assert jetbrains_x11_driver._window_transient_for("123") == 0x123


def test_required_selector_queries_reject_unclassifiable_x11_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    name_results = iter(
        [
            subprocess.CompletedProcess([], 1, "", "display unavailable"),
            subprocess.CompletedProcess([], 0, "win0\n", ""),
        ]
    )
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_xdotool",
        lambda *_args, **_kwargs: next(name_results),
    )
    with pytest.raises(RuntimeError, match="could not classify X11 window 123"):
        jetbrains_x11_driver._required_window_name("123")
    assert jetbrains_x11_driver._required_window_name("123") == "win0"

    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_required_tool",
        lambda name: f"/usr/bin/{name}",
    )
    query_results = iter(
        [
            subprocess.CompletedProcess([], 1, "", "xwininfo failed"),
            subprocess.CompletedProcess([], 0, "Root window id: 0x1\n", ""),
            subprocess.CompletedProcess(
                [],
                0,
                "Root window id: invalid\nParent window id: invalid\n",
                "",
            ),
            subprocess.CompletedProcess(
                [],
                0,
                "Root window id: 0x1\nParent window id: 0x2\n",
                "",
            ),
            subprocess.CompletedProcess(
                [],
                0,
                "Root window id: 0x1\nParent window id: 0x1\n",
                "",
            ),
            subprocess.CompletedProcess([], 1, "", "xprop failed"),
            subprocess.CompletedProcess([], 0, "WM_TRANSIENT_FOR:  not found.\n", ""),
            subprocess.CompletedProcess(
                [],
                0,
                "OTHER_PROPERTY(WINDOW): window id # 0x456\n",
                "",
            ),
            subprocess.CompletedProcess(
                [],
                0,
                "WM_TRANSIENT_FOR(WINDOW): window id # 0x456 trailing\n",
                "",
            ),
            subprocess.CompletedProcess(
                [],
                0,
                "WM_TRANSIENT_FOR(WINDOW): window id # invalid\n",
                "",
            ),
            subprocess.CompletedProcess(
                [],
                0,
                "WM_TRANSIENT_FOR(WINDOW): window id # 0x0\n",
                "",
            ),
            subprocess.CompletedProcess(
                [],
                0,
                "WM_TRANSIENT_FOR(WINDOW): window id # 0x123\n",
                "",
            ),
        ]
    )
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: next(query_results))
    with pytest.raises(RuntimeError, match="xwininfo could not classify"):
        jetbrains_x11_driver._required_window_is_root_child("123")
    with pytest.raises(RuntimeError, match="malformed parentage"):
        jetbrains_x11_driver._required_window_is_root_child("123")
    with pytest.raises(RuntimeError, match="malformed parentage"):
        jetbrains_x11_driver._required_window_is_root_child("123")
    assert jetbrains_x11_driver._required_window_is_root_child("123") is False
    assert jetbrains_x11_driver._required_window_is_root_child("123") is True
    with pytest.raises(RuntimeError, match="xprop could not classify"):
        jetbrains_x11_driver._required_window_transient_for("123")
    assert jetbrains_x11_driver._required_window_transient_for("123") is None
    with pytest.raises(RuntimeError, match="malformed transient ownership"):
        jetbrains_x11_driver._required_window_transient_for("123")
    with pytest.raises(RuntimeError, match="malformed transient ownership"):
        jetbrains_x11_driver._required_window_transient_for("123")
    with pytest.raises(RuntimeError, match="malformed transient ownership"):
        jetbrains_x11_driver._required_window_transient_for("123")
    with pytest.raises(RuntimeError, match="malformed transient ownership"):
        jetbrains_x11_driver._required_window_transient_for("123")
    assert jetbrains_x11_driver._required_window_transient_for("123") == 0x123


def test_pointer_click_moves_and_clicks_in_one_xdotool_invocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, tuple[str, ...], float | None]] = []
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_checked_xdotool",
        lambda action, *args, deadline=None: calls.append((action, args, deadline)),
    )
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_window_rectangle",
        lambda _window, **_kwargs: (0, 1106, 70, 310, 407),
    )

    jetbrains_x11_driver._pointer_click(
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
        jetbrains_x11_driver,
        "_window_rectangle",
        lambda _window, **_kwargs: rectangle,
    )

    with pytest.raises(RuntimeError, match=message):
        jetbrains_x11_driver._pointer_click("123", *point, "test pointer input")


def test_chat_composer_focus_targets_the_validated_project_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actions: list[tuple[str, ...]] = []
    clicks: list[tuple[str, int, int, str]] = []
    monkeypatch.setattr(
        jetbrains_x11_driver, "_window_geometry", lambda _window, **_kwargs: (1400, 1000)
    )
    monkeypatch.setattr(
        jetbrains_x11_driver, "_window_is_root_child", lambda _window, **_kwargs: True
    )
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_checked_xdotool",
        lambda _action, *args, **_kwargs: actions.append(args),
    )
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_pointer_click",
        lambda window, x, y, action, **_kwargs: clicks.append((window, x, y, action)),
    )
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_xdotool",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, "123\n", ""),
    )

    jetbrains_x11_driver._focus_chat_composer("123")

    assert actions == [("windowfocus", "--sync", "123")]
    assert clicks == [("123", 1160, 870, "focus the JetBrains AI Chat composer")]


def test_chat_composer_focus_rejects_ambient_keyboard_focus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        jetbrains_x11_driver, "_window_geometry", lambda _window, **_kwargs: (1400, 1000)
    )
    monkeypatch.setattr(
        jetbrains_x11_driver, "_window_is_root_child", lambda _window, **_kwargs: True
    )
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_checked_xdotool",
        lambda _action, *_args, **_kwargs: None,
    )
    monkeypatch.setattr(jetbrains_x11_driver, "_pointer_click", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_xdotool",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, "124\n", ""),
    )
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_window_parent_ids",
        lambda _window, **_kwargs: (1, 1),
    )

    with pytest.raises(RuntimeError, match="without project-frame keyboard focus"):
        jetbrains_x11_driver._focus_chat_composer("123")


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
        jetbrains_x11_driver, "_window_geometry", lambda _window, **_kwargs: (1400, 1000)
    )
    monkeypatch.setattr(
        jetbrains_x11_driver, "_window_is_root_child", lambda _window, **_kwargs: True
    )
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_checked_xdotool",
        lambda _action, *_args, **_kwargs: None,
    )
    monkeypatch.setattr(jetbrains_x11_driver, "_pointer_click", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_xdotool",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], returncode, output, ""),
    )

    with pytest.raises(RuntimeError, match="focused=None"):
        jetbrains_x11_driver._focus_chat_composer("123")


def test_chat_composer_focus_accepts_a_nested_swing_focus_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        jetbrains_x11_driver, "_window_geometry", lambda _window, **_kwargs: (1400, 1000)
    )
    monkeypatch.setattr(
        jetbrains_x11_driver, "_window_is_root_child", lambda *_args, **_kwargs: True
    )
    monkeypatch.setattr(jetbrains_x11_driver, "_checked_xdotool", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(jetbrains_x11_driver, "_pointer_click", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_xdotool",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, "300\n", ""),
    )
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_window_parent_ids",
        lambda window, **_kwargs: {300: (1, 200), 200: (1, 123)}[window],
    )

    jetbrains_x11_driver._focus_chat_composer("123")


def test_chat_composer_focus_rejects_an_invalid_project_xid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        jetbrains_x11_driver, "_window_geometry", lambda _window, **_kwargs: (1400, 1000)
    )
    monkeypatch.setattr(
        jetbrains_x11_driver, "_window_is_root_child", lambda _window, **_kwargs: True
    )
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_checked_xdotool",
        lambda _action, *_args, **_kwargs: None,
    )
    monkeypatch.setattr(jetbrains_x11_driver, "_pointer_click", lambda *_args, **_kwargs: None)

    with pytest.raises(RuntimeError, match="invalid XID"):
        jetbrains_x11_driver._focus_chat_composer("project")


@pytest.mark.parametrize("geometry", [None, (999, 1000), (1400, 699)])
def test_chat_composer_focus_rejects_an_unvalidated_project_frame(
    monkeypatch: pytest.MonkeyPatch,
    geometry: tuple[int, int] | None,
) -> None:
    monkeypatch.setattr(
        jetbrains_x11_driver, "_window_geometry", lambda _window, **_kwargs: geometry
    )
    monkeypatch.setattr(
        jetbrains_x11_driver, "_window_is_root_child", lambda _window, **_kwargs: True
    )

    with pytest.raises(RuntimeError, match="outside a validated project frame"):
        jetbrains_x11_driver._focus_chat_composer("project")


def test_chat_composer_focus_rejects_a_nested_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        jetbrains_x11_driver, "_window_geometry", lambda _window, **_kwargs: (1400, 1000)
    )
    monkeypatch.setattr(
        jetbrains_x11_driver, "_window_is_root_child", lambda _window, **_kwargs: False
    )

    with pytest.raises(RuntimeError, match="root_child=False"):
        jetbrains_x11_driver._focus_chat_composer("project")


def test_chat_prompt_submission_targets_the_focused_swing_widget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    focused: list[str] = []
    actions: list[tuple[str, tuple[str, ...]]] = []
    clicks: list[tuple[str, int, int, str]] = []
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_focus_chat_composer",
        lambda window, **_kwargs: focused.append(window),
    )
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_checked_xdotool",
        lambda action, *args, **_kwargs: actions.append((action, args)),
    )
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_window_geometry",
        lambda _window, **_kwargs: (1400, 1000),
    )
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_window_is_root_child",
        lambda _window, **_kwargs: True,
    )
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_pointer_click",
        lambda window, x, y, action, **_kwargs: clicks.append((window, x, y, action)),
    )

    jetbrains_x11_driver._submit_chat_prompt("project", "governed prompt")

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
    monkeypatch.setattr(
        jetbrains_x11_driver, "_focus_chat_composer", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(jetbrains_x11_driver, "_checked_xdotool", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_window_geometry",
        lambda _window, **_kwargs: geometry,
    )
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_window_is_root_child",
        lambda _window, **_kwargs: root_child,
    )

    with pytest.raises(RuntimeError, match="outside the pinned project frame"):
        jetbrains_x11_driver._submit_chat_prompt("project", "governed prompt")
