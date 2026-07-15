# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — fail-closed JetBrains X11 window selection
"""Verify fail-closed agent selection in the real IDEA orchestrator."""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from e2e.opencode_editors import (
    jetbrains_client,
    jetbrains_x11_driver,
)
from e2e.opencode_editors.jetbrains_client import (
    _open_agent_selector,
    _select_pinned_agent,
)
from e2e.opencode_editors.jetbrains_lifecycle import JetBrainsLifecycleGuard
from e2e.opencode_editors.jetbrains_x11_geometry import X11WindowRectangle


def test_agent_selector_popup_requires_pinned_window_invariants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        jetbrains_x11_driver, "_window_geometry", lambda _window, **_kwargs: (310, 407)
    )
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_required_window_name",
        lambda _window, **_kwargs: "win0",
    )
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_required_window_is_root_child",
        lambda _window, **_kwargs: True,
    )
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_required_window_transient_for",
        lambda _window, **_kwargs: 123,
    )

    assert jetbrains_client._is_agent_selector_popup("selector", "123") is True
    assert jetbrains_client._is_agent_selector_popup("selector", "invalid") is False


def test_agent_selector_popup_rejects_each_wrong_window_invariant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        jetbrains_x11_driver, "_window_geometry", lambda _window, **_kwargs: (311, 407)
    )
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_required_window_is_root_child",
        lambda _window, **_kwargs: True,
    )
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_required_window_transient_for",
        lambda _window, **_kwargs: 123,
    )
    assert jetbrains_client._is_agent_selector_popup("selector", "123") is False

    monkeypatch.setattr(
        jetbrains_x11_driver, "_window_geometry", lambda _window, **_kwargs: (310, 407)
    )
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_required_window_name",
        lambda _window, **_kwargs: "win0",
    )
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_required_window_is_root_child",
        lambda _window, **_kwargs: False,
    )
    assert jetbrains_client._is_agent_selector_popup("selector", "123") is False

    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_required_window_is_root_child",
        lambda _window, **_kwargs: True,
    )
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_required_window_transient_for",
        lambda _window, **_kwargs: 124,
    )
    assert jetbrains_client._is_agent_selector_popup("selector", "123") is False

    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_required_window_transient_for",
        lambda _window, **_kwargs: 123,
    )
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_required_window_name",
        lambda _window, **_kwargs: "other",
    )
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
        jetbrains_x11_driver,
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
        jetbrains_x11_driver,
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
        jetbrains_x11_driver,
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
        jetbrains_x11_driver,
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
        jetbrains_x11_driver, "_window_geometry", lambda _window, **_kwargs: (1400, 1000)
    )
    monkeypatch.setattr(
        jetbrains_x11_driver, "_window_is_root_child", lambda *_args, **_kwargs: True
    )
    monkeypatch.setattr(
        jetbrains_x11_driver,
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
    monkeypatch.setattr(
        jetbrains_x11_driver, "_window_geometry", lambda _window, **_kwargs: geometry
    )
    monkeypatch.setattr(
        jetbrains_x11_driver, "_window_is_root_child", lambda *_args, **_kwargs: root_child
    )
    monkeypatch.setattr(
        jetbrains_client,
        "_visible_agent_selector_popups",
        lambda *_args, **_kwargs: (),
    )

    with pytest.raises(RuntimeError, match="outside the pinned project frame"):
        _open_agent_selector("123", deadline=float("inf"))


def test_agent_selector_filters_exact_name_confirms_once_and_proves_closure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actions: list[tuple[str, ...]] = []
    captures: list[bool] = []
    guarded: list[bool] = []
    monkeypatch.setattr(
        jetbrains_client,
        "_is_agent_selector_popup",
        lambda selector, project, **_kwargs: selector == "selector" and project == "123",
    )
    monkeypatch.setattr(
        jetbrains_client,
        "_visible_agent_selector_popups",
        lambda _project, **_kwargs: ("selector",),
    )
    monkeypatch.setattr(
        jetbrains_client,
        "_visible_jetbrains_window_rectangles",
        lambda **_kwargs: (),
    )
    monkeypatch.setattr(
        jetbrains_client,
        "_owned_agent_selector_popups",
        lambda *_args, **_kwargs: (),
    )
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_checked_xdotool",
        lambda _action, *args, **_kwargs: actions.append(args),
    )
    monkeypatch.setattr(jetbrains_x11_driver, "_bounded_poll_sleep", lambda _deadline: None)

    _select_pinned_agent(
        "selector",
        "123",
        deadline=float("inf"),
        guard=lambda: guarded.append(True),
        capture_filtered_selector=lambda: captures.append(True),
    )

    assert actions == [
        ("windowfocus", "--sync", "selector"),
        ("key", "--window", "selector", "ctrl+a"),
        (
            "type",
            "--window",
            "selector",
            "--delay",
            "1",
            "--",
            "SYNAPSE OpenCode E2E",
        ),
        ("key", "--window", "selector", "Return"),
    ]
    assert captures == [True]
    assert guarded == [True, True, True]


@pytest.mark.parametrize(
    ("rectangles", "matches", "message"),
    [
        (
            (X11WindowRectangle("selector", 0, 1, 2, 310, 407),),
            (),
            "cardinality changed after confirmation",
        ),
        (
            (X11WindowRectangle("other", 0, 1, 2, 310, 407),),
            ("other",),
            "cardinality changed after confirmation",
        ),
    ],
)
def test_agent_selector_rejects_visible_ownership_drift_or_replacement(
    monkeypatch: pytest.MonkeyPatch,
    rectangles: tuple[X11WindowRectangle, ...],
    matches: tuple[str, ...],
    message: str,
) -> None:
    monkeypatch.setattr(jetbrains_client, "_is_agent_selector_popup", lambda *_a, **_k: True)
    monkeypatch.setattr(
        jetbrains_client,
        "_visible_agent_selector_popups",
        lambda *_args, **_kwargs: ("selector",),
    )
    monkeypatch.setattr(
        jetbrains_client,
        "_visible_jetbrains_window_rectangles",
        lambda **_kwargs: rectangles,
    )
    monkeypatch.setattr(
        jetbrains_client,
        "_owned_agent_selector_popups",
        lambda *_args, **_kwargs: matches,
    )
    monkeypatch.setattr(jetbrains_x11_driver, "_checked_xdotool", lambda *_a, **_k: None)
    monkeypatch.setattr(jetbrains_x11_driver, "_bounded_poll_sleep", lambda _deadline: None)

    with pytest.raises(RuntimeError, match=message):
        _select_pinned_agent("selector", "123", deadline=float("inf"))


def test_agent_selector_rejects_unclassifiable_replacement_after_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(jetbrains_client, "_is_agent_selector_popup", lambda *_a, **_k: True)
    monkeypatch.setattr(
        jetbrains_client,
        "_visible_agent_selector_popups",
        lambda *_args, **_kwargs: ("selector",),
    )
    monkeypatch.setattr(
        jetbrains_client,
        "_visible_jetbrains_window_rectangles",
        lambda **_kwargs: (X11WindowRectangle("replacement", 0, 1, 2, 310, 407),),
    )
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_required_window_name",
        lambda *_args, **_kwargs: "win0",
    )
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_required_window_is_root_child",
        lambda *_args, **_kwargs: True,
    )

    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_required_tool",
        lambda name: f"/usr/bin/{name}",
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [],
            0,
            "OTHER_PROPERTY(WINDOW): window id # 0x456\n",
            "",
        ),
    )
    monkeypatch.setattr(jetbrains_x11_driver, "_checked_xdotool", lambda *_a, **_k: None)

    with pytest.raises(RuntimeError, match="malformed transient ownership"):
        _select_pinned_agent("selector", "123", deadline=float("inf"))


def test_agent_selector_rejects_malformed_replacement_parentage_after_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(jetbrains_client, "_is_agent_selector_popup", lambda *_a, **_k: True)
    monkeypatch.setattr(
        jetbrains_client,
        "_visible_agent_selector_popups",
        lambda *_args, **_kwargs: ("selector",),
    )
    monkeypatch.setattr(
        jetbrains_client,
        "_visible_jetbrains_window_rectangles",
        lambda **_kwargs: (X11WindowRectangle("replacement", 0, 1, 2, 310, 407),),
    )
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_required_window_name",
        lambda *_args, **_kwargs: "win0",
    )
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_required_tool",
        lambda name: f"/usr/bin/{name}",
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [],
            0,
            "Root window id: invalid\nParent window id: invalid\n",
            "",
        ),
    )
    monkeypatch.setattr(jetbrains_x11_driver, "_checked_xdotool", lambda *_a, **_k: None)

    with pytest.raises(RuntimeError, match="malformed parentage"):
        _select_pinned_agent("selector", "123", deadline=float("inf"))


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


def test_required_environment_fails_closed_on_missing_or_blank_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SYNAPSE_REQUIRED_TEST_VALUE", raising=False)
    with pytest.raises(RuntimeError, match="SYNAPSE_REQUIRED_TEST_VALUE is required"):
        jetbrains_client._required_env("SYNAPSE_REQUIRED_TEST_VALUE")

    monkeypatch.setenv("SYNAPSE_REQUIRED_TEST_VALUE", " value ")
    assert jetbrains_client._required_env("SYNAPSE_REQUIRED_TEST_VALUE") == "value"


def test_agent_selector_search_and_confirmation_timeout_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        jetbrains_client,
        "_visible_agent_selector_popups",
        lambda *_args, **_kwargs: (),
    )
    with pytest.raises(RuntimeError, match="did not expose"):
        jetbrains_client._find_agent_selector_popup(0.0, "123")

    monkeypatch.setattr(jetbrains_client, "_is_agent_selector_popup", lambda *_a, **_k: True)
    snapshots = iter([("selector",), ()])
    monkeypatch.setattr(
        jetbrains_client,
        "_visible_agent_selector_popups",
        lambda *_args, **_kwargs: next(snapshots),
    )
    monkeypatch.setattr(jetbrains_x11_driver, "_checked_xdotool", lambda *_a, **_k: None)
    monkeypatch.setattr(jetbrains_x11_driver, "_bounded_poll_sleep", lambda _deadline: None)
    with pytest.raises(RuntimeError, match="changed while filtering"):
        _select_pinned_agent("selector", "123", deadline=float("inf"))

    monkeypatch.setattr(
        jetbrains_client,
        "_visible_agent_selector_popups",
        lambda *_args, **_kwargs: ("selector",),
    )
    with pytest.raises(RuntimeError, match="remained open"):
        _select_pinned_agent("selector", "123", deadline=0.0)


def test_selector_retry_suppression_and_one_loop_closure_are_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = iter([0.0, 1.0, 2.0, 2.5, 3.0])
    retries: list[bool] = []
    monkeypatch.setattr(time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(
        jetbrains_client,
        "_visible_agent_selector_popups",
        lambda *_args, **_kwargs: (),
    )
    monkeypatch.setattr(jetbrains_x11_driver, "_bounded_poll_sleep", lambda _deadline: None)
    with pytest.raises(RuntimeError, match="did not expose"):
        jetbrains_client._find_agent_selector_popup(
            3.0,
            "123",
            retry=lambda: retries.append(True),
            retry_interval_seconds=5.0,
        )
    assert retries == [True]

    monkeypatch.setattr(time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(jetbrains_client, "_is_agent_selector_popup", lambda *_a, **_k: True)
    monkeypatch.setattr(
        jetbrains_client,
        "_visible_agent_selector_popups",
        lambda *_args, **_kwargs: ("selector",),
    )
    rectangles = iter(
        [
            (X11WindowRectangle("selector", 0, 1, 2, 310, 407),),
            (),
        ]
    )
    monkeypatch.setattr(
        jetbrains_client,
        "_visible_jetbrains_window_rectangles",
        lambda **_kwargs: next(rectangles),
    )
    monkeypatch.setattr(
        jetbrains_client,
        "_owned_agent_selector_popups",
        lambda snapshot, *_args, **_kwargs: ("selector",) if snapshot else (),
    )
    monkeypatch.setattr(jetbrains_x11_driver, "_checked_xdotool", lambda *_a, **_k: None)
    sleeps: list[bool] = []
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_bounded_poll_sleep",
        lambda _deadline: sleeps.append(True),
    )
    _select_pinned_agent("selector", "123", deadline=1.0)
    assert sleeps == [True, True]


def test_visible_selector_snapshot_accepts_only_an_empty_search_miss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_xdotool",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 1, "", ""),
    )
    assert jetbrains_client._visible_jetbrains_window_rectangles(deadline=1.0) == ()
    assert jetbrains_client._visible_agent_selector_popups("invalid", deadline=1.0) == ()


@pytest.mark.parametrize(
    ("returncode", "diagnostic"),
    [
        (0, ""),
        (0, "unexpected warning"),
        (1, "display unavailable"),
        (2, "transport failed"),
        (124, "xdotool command timed out"),
    ],
)
def test_visible_selector_snapshot_rejects_x11_failure(
    monkeypatch: pytest.MonkeyPatch,
    returncode: int,
    diagnostic: str,
) -> None:
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_xdotool",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [],
            returncode,
            "",
            diagnostic,
        ),
    )

    with pytest.raises(RuntimeError, match="JetBrains snapshot|snapshot visible JetBrains"):
        jetbrains_client._visible_jetbrains_window_rectangles(deadline=1.0)


class _FakeIdeaProcess:
    """Minimal live process seam for the orchestration acceptance test."""

    def __init__(self, returncode: int) -> None:
        self.returncode = returncode

    def poll(self) -> None:
        """Report the fake IDEA process as live."""
        return None


class _FakeLifecycle:
    """Record lifecycle gates exercised by the client orchestrator."""

    def __init__(self, events: list[str]) -> None:
        self._events = events

    def assert_at_most_one(self) -> None:
        """Record the pre-exact cardinality gate."""
        self._events.append("at-most-one")

    def require_none(self) -> None:
        """Record the pre-selection zero-lifecycle gate."""
        self._events.append("none")

    def require_exactly_one(self) -> None:
        """Record the post-handshake exact cardinality gate."""
        self._events.append("exactly-one")

    def idea_contents(self) -> str:
        """Return ordered readiness evidence for the matcher callback."""
        return (
            "Required plugins check passed\n"
            "Starting ACP client session 1\n"
            "Received notification: AvailableCommandsUpdate\n"
        )


@pytest.mark.parametrize("returncode", [0, 7])
def test_main_orchestrates_full_pinned_flow_and_preserves_failure_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    returncode: int,
) -> None:
    project = tmp_path / "project"
    artifacts = tmp_path / "artifacts"
    home = tmp_path / "home"
    data = tmp_path / "data"
    for directory in (project, artifacts, home, data):
        directory.mkdir()
    environment = {
        "SYNAPSE_JETBRAINS_BIN": str(tmp_path / "idea"),
        "SYNAPSE_JETBRAINS_PLUGINS": str(tmp_path / "plugins"),
        "SYNAPSE_EDITOR_E2E_PROJECT": str(project),
        "SYNAPSE_ACP_TRACE": str(tmp_path / "trace.jsonl"),
        "SYNAPSE_EDITOR_E2E_PROMPT": "governed prompt",
        "SYNAPSE_ACP_PROXY_ARGV_JSON": '["/opt/opencode", "acp"]',
        "HOME": str(home),
        "SYNAPSE_EDITOR_E2E_ARTIFACT_DIR": str(artifacts),
        "XDG_DATA_HOME": str(data),
    }
    for name, value in environment.items():
        monkeypatch.setenv(name, value)

    events: list[str] = []
    lifecycle = _FakeLifecycle(events)
    monkeypatch.setattr(
        jetbrains_client,
        "write_acp_config",
        lambda *_args, **_kwargs: events.append("config"),
    )
    monkeypatch.setattr(
        jetbrains_client,
        "write_idea_profile",
        lambda *_args, **_kwargs: events.append("profile"),
    )
    monkeypatch.setattr(
        jetbrains_client,
        "idea_command",
        lambda *_args, **_kwargs: ["idea"],
    )
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *_args, **_kwargs: _FakeIdeaProcess(returncode),
    )
    monkeypatch.setattr(
        jetbrains_client,
        "complete_first_run_agreements",
        lambda _deadline: events.append("agreements"),
    )
    monkeypatch.setattr(
        jetbrains_client,
        "find_project_window",
        lambda _deadline: "123",
    )
    monkeypatch.setattr(
        jetbrains_client,
        "skip_islands_onboarding",
        lambda _deadline, _window: events.append("onboarding"),
    )
    monkeypatch.setattr(
        JetBrainsLifecycleGuard,
        "capture",
        lambda *_args, **_kwargs: lifecycle,
    )
    monkeypatch.setattr(
        jetbrains_client,
        "_open_agent_selector",
        lambda *_args, **_kwargs: "selector",
    )

    def select_agent(
        _selector: str,
        _window: str,
        *,
        guard: Callable[[], object],
        capture_filtered_selector: Callable[[], None],
        **_kwargs: object,
    ) -> None:
        guard()
        capture_filtered_selector()
        events.append("selected")

    monkeypatch.setattr(jetbrains_client, "_select_pinned_agent", select_agent)

    def wait_log(
        log_root: Path,
        _markers: object,
        _deadline: float,
        _poll: Callable[[], int | None],
        **kwargs: object,
    ) -> None:
        (log_root / "idea.log").write_text(lifecycle.idea_contents(), encoding="utf-8")
        retry = kwargs.get("retry")
        if retry is not None:
            cast(Callable[[], object], retry)()
        guard = kwargs.get("guard")
        if guard is not None:
            cast(Callable[[], object], guard)()
        reader = kwargs.get("contents_reader")
        matcher = kwargs.get("matcher")
        if reader is not None and matcher is not None:
            contents = cast(Callable[[], str], reader)()
            assert cast(Callable[[str], bool], matcher)(contents)
        events.append("log")

    def wait_trace(
        _trace: Path,
        marker: str,
        _deadline: float,
        _process: object,
        *,
        guard: Callable[[], object] | None = None,
    ) -> None:
        if guard is not None:
            guard()
        events.append(marker)

    def screenshot(path: Path, **_kwargs: object) -> None:
        path.write_bytes(b"png")
        events.append(path.name)

    monkeypatch.setattr(jetbrains_client, "wait_for_idea_log", wait_log)
    monkeypatch.setattr(jetbrains_client, "wait_for_trace", wait_trace)
    monkeypatch.setattr(jetbrains_client, "capture_screenshot", screenshot)
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_checked_xdotool",
        lambda *_args, **_kwargs: events.append("chat"),
    )
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_submit_chat_prompt",
        lambda *_args, **_kwargs: events.append("prompt"),
    )
    monkeypatch.setattr(
        jetbrains_client,
        "capture_evidence_and_terminate",
        lambda *_args, **_kwargs: events.append("cleanup"),
    )

    assert jetbrains_client.main() == 0
    assert (artifacts / "intellij-agent-selector.png").read_bytes() == b"png"
    assert (artifacts / "intellij.png").read_bytes() == b"png"
    assert (artifacts / "intellij-idea-tail.log").is_file()
    assert "selected" in events
    assert "prompt" in events
    assert events[-1] == "cleanup"


@pytest.mark.parametrize("proxy_json", ["{}", '["opencode", 1]', "[]"])
def test_main_rejects_non_string_or_empty_proxy_arguments(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    proxy_json: str,
) -> None:
    required = {
        "SYNAPSE_JETBRAINS_BIN": str(tmp_path / "idea"),
        "SYNAPSE_JETBRAINS_PLUGINS": str(tmp_path / "plugins"),
        "SYNAPSE_EDITOR_E2E_PROJECT": str(tmp_path / "project"),
        "SYNAPSE_ACP_TRACE": str(tmp_path / "trace"),
        "SYNAPSE_EDITOR_E2E_PROMPT": "prompt",
        "SYNAPSE_ACP_PROXY_ARGV_JSON": proxy_json,
    }
    for name, value in required.items():
        monkeypatch.setenv(name, value)

    with pytest.raises(RuntimeError, match="must contain non-empty string arguments"):
        jetbrains_client.main()
