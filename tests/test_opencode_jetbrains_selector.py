# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — fail-closed JetBrains ACP selector lifecycle tests
"""Verify the pinned JetBrains ACP selector lifecycle through its production module."""

from __future__ import annotations

import subprocess
import time

import pytest

from e2e.opencode_editors import (
    jetbrains_selector,
    jetbrains_selector_windows,
    jetbrains_x11_driver,
)
from e2e.opencode_editors.jetbrains_selector import (
    open_agent_selector,
    select_pinned_agent,
)
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

    assert jetbrains_selector.is_agent_selector_popup("selector", "123") is True
    assert jetbrains_selector.is_agent_selector_popup("selector", "invalid") is False


def test_agent_selector_popup_rejects_each_wrong_window_invariant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(jetbrains_x11_driver, "_window_geometry", lambda _window, **_kwargs: None)
    assert jetbrains_selector.is_agent_selector_popup("selector", "123") is False

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
    assert jetbrains_selector.is_agent_selector_popup("selector", "123") is False

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
    assert jetbrains_selector.is_agent_selector_popup("selector", "123") is False

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
    assert jetbrains_selector.is_agent_selector_popup("selector", "123") is False

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
    assert jetbrains_selector.is_agent_selector_popup("selector", "123") is False


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
        jetbrains_selector_windows,
        "_agent_selector_owner_matches",
        lambda window, project_id, **_kwargs: window == "456" and project_id == 123,
    )

    assert jetbrains_selector.visible_agent_selector_popups("123", deadline=float("inf")) == (
        "456",
    )
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
        jetbrains_selector_windows,
        "_agent_selector_owner_matches",
        lambda _window, _project_id, **_kwargs: True,
    )

    with pytest.raises(RuntimeError, match="multiple pinned ACP agent selector popups"):
        jetbrains_selector.find_agent_selector_popup(float("inf"), "123")


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
        jetbrains_selector_windows,
        "_agent_selector_owner_matches",
        record_owner_query,
    )

    assert jetbrains_selector.visible_agent_selector_popups("123", deadline=float("inf")) == ()
    assert owner_queries == []


@pytest.mark.parametrize(
    ("geometry", "phase", "expected"),
    [
        ((310, 407), jetbrains_selector._SelectorGeometryPhase.UNFILTERED, True),
        ((310, 201), jetbrains_selector._SelectorGeometryPhase.UNFILTERED, False),
        ((310, 201), jetbrains_selector._SelectorGeometryPhase.FILTERED_READY, True),
        ((310, 43), jetbrains_selector._SelectorGeometryPhase.FILTERED_READY, True),
        ((310, 42), jetbrains_selector._SelectorGeometryPhase.FILTERED_READY, False),
        ((310, 408), jetbrains_selector._SelectorGeometryPhase.FILTERED_READY, False),
        ((309, 201), jetbrains_selector._SelectorGeometryPhase.FILTERED_READY, False),
        ((310, 42), jetbrains_selector._SelectorGeometryPhase.FILTERED_VISIBLE, True),
        ((310, 1), jetbrains_selector._SelectorGeometryPhase.FILTERED_VISIBLE, True),
        ((310, 0), jetbrains_selector._SelectorGeometryPhase.FILTERED_VISIBLE, False),
    ],
)
def test_agent_selector_geometry_tracks_lifecycle_phase(
    monkeypatch: pytest.MonkeyPatch,
    geometry: tuple[int, int],
    phase: jetbrains_selector._SelectorGeometryPhase,
    expected: bool,
) -> None:
    """Classify observed dynamic selector heights without weakening ownership."""
    monkeypatch.setattr(
        jetbrains_selector_windows,
        "_agent_selector_owner_matches",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_required_window_name",
        lambda *_args, **_kwargs: "other",
    )

    matches = jetbrains_selector.owned_agent_selector_popups(
        (X11WindowRectangle("selector", 0, 1, 2, *geometry),),
        123,
        deadline=1.0,
        geometry_phase=phase,
    )

    assert matches == (("selector",) if expected else ())


def test_filtered_selector_rejects_dynamic_unowned_popup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A valid collapsed height never bypasses the project ownership proof."""
    monkeypatch.setattr(
        jetbrains_selector_windows,
        "_agent_selector_owner_matches",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_required_window_name",
        lambda *_args, **_kwargs: "win0",
    )

    with pytest.raises(RuntimeError, match="outside the pinned project frame"):
        jetbrains_selector.owned_agent_selector_popups(
            (X11WindowRectangle("selector", 0, 1, 2, 310, 201),),
            123,
            deadline=1.0,
            geometry_phase=jetbrains_selector._SelectorGeometryPhase.FILTERED_READY,
        )


def test_agent_selector_snapshot_rejects_exact_title_with_unowned_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail immediately when a selector-shaped exact title loses project ownership."""
    monkeypatch.setattr(
        jetbrains_selector_windows,
        "_agent_selector_owner_matches",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_required_window_name",
        lambda *_args, **_kwargs: "win0",
    )

    with pytest.raises(RuntimeError, match="outside the pinned project frame"):
        jetbrains_selector.owned_agent_selector_popups(
            (X11WindowRectangle("selector", 0, 1, 2, 310, 407),),
            123,
            deadline=1.0,
        )


def test_agent_selector_popup_search_rejects_malformed_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_xdotool",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, "WINDOW=456\nX=1\n", ""),
    )

    with pytest.raises(RuntimeError, match="malformed batched selector geometry"):
        jetbrains_selector.visible_agent_selector_popups("123", deadline=float("inf"))


def test_agent_selector_popup_search_retries_only_while_guard_is_clean(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    matches = iter([(), ("456",)])
    retries: list[bool] = []
    guarded: list[bool] = []
    monkeypatch.setattr(
        jetbrains_selector,
        "visible_agent_selector_popups",
        lambda *_args, **_kwargs: next(matches),
    )

    selector = jetbrains_selector.find_agent_selector_popup(
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
        jetbrains_selector.find_agent_selector_popup(
            float("inf"),
            "123",
            retry_interval_seconds=0.0,
        )


def test_agent_selector_opens_only_from_the_pinned_project_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actions: list[tuple[str, ...]] = []
    focused: list[tuple[str, float]] = []
    monkeypatch.setattr(
        jetbrains_x11_driver, "_window_geometry", lambda _window, **_kwargs: (1400, 1000)
    )
    monkeypatch.setattr(
        jetbrains_x11_driver, "_window_is_root_child", lambda *_args, **_kwargs: True
    )
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_focus_window_for_input",
        lambda window, *, deadline: focused.append((window, deadline)),
    )
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_checked_xdotool",
        lambda _action, *args, **_kwargs: actions.append(args),
    )
    monkeypatch.setattr(
        jetbrains_selector,
        "find_agent_selector_popup",
        lambda _deadline, project, **kwargs: (
            kwargs["retry"](),
            f"selector-for-{project}",
        )[1],
    )

    assert open_agent_selector("123", deadline=float("inf")) == "selector-for-123"
    assert actions == [
        ("key", "ctrl+alt+shift+k"),
    ]
    assert focused == [("123", float("inf"))]


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
        jetbrains_selector,
        "visible_agent_selector_popups",
        lambda *_args, **_kwargs: (),
    )

    with pytest.raises(RuntimeError, match="outside the pinned project frame"):
        open_agent_selector("123", deadline=float("inf"))


def test_agent_selector_reacquires_after_filtering_confirms_once_and_proves_closure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actions: list[tuple[str, ...]] = []
    captures: list[bool] = []
    guarded: list[bool] = []
    focused: list[tuple[str, float]] = []
    geometry_phases: list[jetbrains_selector._SelectorGeometryPhase] = []
    monkeypatch.setattr(
        jetbrains_selector,
        "is_agent_selector_popup",
        lambda selector, project, **_kwargs: selector == "selector" and project == "123",
    )
    selector_snapshots = iter([("selector",), (), ("replacement",)])

    def visible_selector(
        _project: str,
        **kwargs: object,
    ) -> tuple[str, ...]:
        phase = kwargs.get(
            "geometry_phase",
            jetbrains_selector._SelectorGeometryPhase.UNFILTERED,
        )
        assert isinstance(phase, jetbrains_selector._SelectorGeometryPhase)
        geometry_phases.append(phase)
        return next(selector_snapshots)

    monkeypatch.setattr(
        jetbrains_selector,
        "visible_agent_selector_popups",
        visible_selector,
    )
    monkeypatch.setattr(
        jetbrains_selector,
        "visible_jetbrains_window_rectangles",
        lambda **_kwargs: (),
    )
    monkeypatch.setattr(
        jetbrains_selector,
        "owned_agent_selector_popups",
        lambda *_args, **_kwargs: (),
    )
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_focus_window_for_input",
        lambda window, *, deadline: focused.append((window, deadline)),
    )
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_checked_xdotool",
        lambda _action, *args, **_kwargs: actions.append(args),
    )
    monkeypatch.setattr(jetbrains_x11_driver, "_bounded_poll_sleep", lambda _deadline: None)

    select_pinned_agent(
        "selector",
        "123",
        deadline=float("inf"),
        guard=lambda: guarded.append(True),
        capture_filtered_selector=lambda: captures.append(True),
    )

    assert actions == [
        ("key", "ctrl+a"),
        (
            "type",
            "--delay",
            "1",
            "--",
            "SYNAPSE OpenCode E2E",
        ),
        ("key", "Return"),
    ]
    assert focused == [
        ("selector", float("inf")),
        ("replacement", float("inf")),
    ]
    assert captures == [True]
    assert guarded == [True, True, True, True, True]
    assert geometry_phases == [
        jetbrains_selector._SelectorGeometryPhase.UNFILTERED,
        jetbrains_selector._SelectorGeometryPhase.FILTERED_READY,
        jetbrains_selector._SelectorGeometryPhase.FILTERED_READY,
    ]


def test_agent_selector_rejects_ambiguous_filtered_reacquisition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refuse multiple owner-proven selectors after the exact filter is typed."""
    monkeypatch.setattr(time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(jetbrains_selector, "is_agent_selector_popup", lambda *_a, **_k: True)
    snapshots = iter([("selector",), ("selector", "replacement")])
    monkeypatch.setattr(
        jetbrains_selector,
        "visible_agent_selector_popups",
        lambda *_args, **_kwargs: next(snapshots),
    )
    monkeypatch.setattr(jetbrains_x11_driver, "_checked_xdotool", lambda *_a, **_k: None)
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_focus_window_for_input",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(jetbrains_x11_driver, "_bounded_poll_sleep", lambda _deadline: None)

    with pytest.raises(RuntimeError, match="while filtering the pinned agent"):
        select_pinned_agent("selector", "123", deadline=1.0)


def test_agent_selector_rejects_ambiguous_replacement_after_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refuse multiple owner-proven selector windows after confirmation."""
    monkeypatch.setattr(time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(jetbrains_selector, "is_agent_selector_popup", lambda *_a, **_k: True)
    monkeypatch.setattr(
        jetbrains_selector,
        "visible_agent_selector_popups",
        lambda *_args, **_kwargs: ("selector",),
    )
    monkeypatch.setattr(
        jetbrains_selector,
        "visible_jetbrains_window_rectangles",
        lambda **_kwargs: (
            X11WindowRectangle("selector", 0, 1, 2, 310, 407),
            X11WindowRectangle("replacement", 0, 1, 2, 310, 407),
        ),
    )
    monkeypatch.setattr(
        jetbrains_selector,
        "owned_agent_selector_popups",
        lambda *_args, **_kwargs: ("selector", "replacement"),
    )
    monkeypatch.setattr(jetbrains_x11_driver, "_checked_xdotool", lambda *_a, **_k: None)
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_focus_window_for_input",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(jetbrains_x11_driver, "_bounded_poll_sleep", lambda _deadline: None)

    with pytest.raises(RuntimeError, match="cardinality changed after confirmation"):
        select_pinned_agent("selector", "123", deadline=1.0)


def test_agent_selector_rejects_visible_ownership_drift_after_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(jetbrains_selector, "is_agent_selector_popup", lambda *_a, **_k: True)
    monkeypatch.setattr(
        jetbrains_selector,
        "visible_agent_selector_popups",
        lambda *_args, **_kwargs: ("selector",),
    )
    monkeypatch.setattr(
        jetbrains_selector,
        "visible_jetbrains_window_rectangles",
        lambda **_kwargs: (X11WindowRectangle("selector", 0, 1, 2, 310, 407),),
    )
    monkeypatch.setattr(
        jetbrains_selector,
        "owned_agent_selector_popups",
        lambda *_args, **_kwargs: (),
    )
    monkeypatch.setattr(jetbrains_x11_driver, "_checked_xdotool", lambda *_a, **_k: None)
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_focus_window_for_input",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(jetbrains_x11_driver, "_bounded_poll_sleep", lambda _deadline: None)

    with pytest.raises(RuntimeError, match="lost pinned ownership after confirmation"):
        select_pinned_agent("selector", "123", deadline=float("inf"))


def test_agent_selector_accepts_owner_proven_xid_remap_after_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep classifying one remapped selector until its closure is stable."""
    monkeypatch.setattr(time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(jetbrains_selector, "is_agent_selector_popup", lambda *_a, **_k: True)
    monkeypatch.setattr(
        jetbrains_selector,
        "visible_agent_selector_popups",
        lambda *_args, **_kwargs: ("selector",),
    )
    rectangles = iter(
        [
            (X11WindowRectangle("replacement", 0, 1, 2, 310, 407),),
            (),
            (),
        ]
    )
    monkeypatch.setattr(
        jetbrains_selector,
        "visible_jetbrains_window_rectangles",
        lambda **_kwargs: next(rectangles),
    )
    monkeypatch.setattr(
        jetbrains_selector,
        "owned_agent_selector_popups",
        lambda snapshot, *_args, **_kwargs: ("replacement",) if snapshot else (),
    )
    monkeypatch.setattr(jetbrains_x11_driver, "_checked_xdotool", lambda *_a, **_k: None)
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_focus_window_for_input",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(jetbrains_x11_driver, "_bounded_poll_sleep", lambda _deadline: None)

    select_pinned_agent("selector", "123", deadline=1.0)


def test_agent_selector_rejects_unclassifiable_replacement_after_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(jetbrains_selector, "is_agent_selector_popup", lambda *_a, **_k: True)
    monkeypatch.setattr(
        jetbrains_selector,
        "visible_agent_selector_popups",
        lambda *_args, **_kwargs: ("selector",),
    )
    monkeypatch.setattr(
        jetbrains_selector,
        "visible_jetbrains_window_rectangles",
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
            "WM_TRANSIENT_FOR(WINDOW): window id # 0x4_56\n",
            "",
        ),
    )
    monkeypatch.setattr(jetbrains_x11_driver, "_checked_xdotool", lambda *_a, **_k: None)
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_focus_window_for_input",
        lambda *_a, **_k: None,
    )

    with pytest.raises(RuntimeError, match="malformed transient ownership"):
        select_pinned_agent("selector", "123", deadline=float("inf"))


def test_agent_selector_rejects_malformed_replacement_parentage_after_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(jetbrains_selector, "is_agent_selector_popup", lambda *_a, **_k: True)
    monkeypatch.setattr(
        jetbrains_selector,
        "visible_agent_selector_popups",
        lambda *_args, **_kwargs: ("selector",),
    )
    monkeypatch.setattr(
        jetbrains_selector,
        "visible_jetbrains_window_rectangles",
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
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_focus_window_for_input",
        lambda *_a, **_k: None,
    )

    with pytest.raises(RuntimeError, match="malformed parentage"):
        select_pinned_agent("selector", "123", deadline=float("inf"))


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
        jetbrains_selector,
        "is_agent_selector_popup",
        lambda *_args, **_kwargs: popup_is_pinned,
    )
    monkeypatch.setattr(
        jetbrains_selector,
        "visible_agent_selector_popups",
        lambda *_args, **_kwargs: matches,
    )

    with pytest.raises(RuntimeError, match=message):
        select_pinned_agent("selector", "123", deadline=float("inf"))


def test_agent_selector_search_and_confirmation_timeout_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        jetbrains_selector,
        "visible_agent_selector_popups",
        lambda *_args, **_kwargs: (),
    )
    with pytest.raises(RuntimeError, match="did not expose"):
        jetbrains_selector.find_agent_selector_popup(0.0, "123")

    monkeypatch.setattr(jetbrains_selector, "is_agent_selector_popup", lambda *_a, **_k: True)
    snapshots = iter([("selector",), ()])
    monkeypatch.setattr(
        jetbrains_selector,
        "visible_agent_selector_popups",
        lambda *_args, **_kwargs: next(snapshots),
    )
    monkeypatch.setattr(jetbrains_x11_driver, "_checked_xdotool", lambda *_a, **_k: None)
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_focus_window_for_input",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(jetbrains_x11_driver, "_bounded_poll_sleep", lambda _deadline: None)
    filtering_clock = iter([0.0, 1.0])
    monkeypatch.setattr(time, "monotonic", lambda: next(filtering_clock))
    with pytest.raises(RuntimeError, match="did not stabilise while filtering"):
        select_pinned_agent("selector", "123", deadline=1.0)

    monkeypatch.setattr(
        jetbrains_selector,
        "visible_agent_selector_popups",
        lambda *_args, **_kwargs: ("selector",),
    )
    confirmation_clock = iter([0.0, 1.0])
    monkeypatch.setattr(time, "monotonic", lambda: next(confirmation_clock))
    with pytest.raises(RuntimeError, match="remained open"):
        select_pinned_agent("selector", "123", deadline=1.0)


def test_selector_retry_suppression_and_one_loop_closure_are_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = iter([0.0, 1.0, 2.0, 2.5, 3.0])
    retries: list[bool] = []
    monkeypatch.setattr(time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(
        jetbrains_selector,
        "visible_agent_selector_popups",
        lambda *_args, **_kwargs: (),
    )
    monkeypatch.setattr(jetbrains_x11_driver, "_bounded_poll_sleep", lambda _deadline: None)
    with pytest.raises(RuntimeError, match="did not expose"):
        jetbrains_selector.find_agent_selector_popup(
            3.0,
            "123",
            retry=lambda: retries.append(True),
            retry_interval_seconds=5.0,
        )
    assert retries == [True]

    monkeypatch.setattr(time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(jetbrains_selector, "is_agent_selector_popup", lambda *_a, **_k: True)
    monkeypatch.setattr(
        jetbrains_selector,
        "visible_agent_selector_popups",
        lambda *_args, **_kwargs: ("selector",),
    )
    rectangles = iter(
        [
            (X11WindowRectangle("selector", 0, 1, 2, 310, 407),),
            (),
            (),
        ]
    )
    monkeypatch.setattr(
        jetbrains_selector,
        "visible_jetbrains_window_rectangles",
        lambda **_kwargs: next(rectangles),
    )
    monkeypatch.setattr(
        jetbrains_selector,
        "owned_agent_selector_popups",
        lambda snapshot, *_args, **_kwargs: ("selector",) if snapshot else (),
    )
    monkeypatch.setattr(jetbrains_x11_driver, "_checked_xdotool", lambda *_a, **_k: None)
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_focus_window_for_input",
        lambda *_a, **_k: None,
    )
    sleeps: list[bool] = []
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_bounded_poll_sleep",
        lambda _deadline: sleeps.append(True),
    )
    select_pinned_agent("selector", "123", deadline=1.0)
    assert sleeps == [True, True, True]


def test_visible_selector_snapshot_accepts_only_an_empty_search_miss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_xdotool",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 1, "", ""),
    )
    assert jetbrains_selector.visible_jetbrains_window_rectangles(deadline=1.0) == ()
    assert jetbrains_selector.visible_agent_selector_popups("invalid", deadline=1.0) == ()


def test_visible_selector_snapshot_retries_exact_disappearing_window_race(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bad_window = subprocess.CompletedProcess(
        [],
        1,
        "",
        "X Error of failed request:  BadWindow (invalid Window parameter)\n"
        "  Major opcode of failed request:  3 (X_GetWindowAttributes)\n"
        "  Resource id in failed request:  0x40039d\n",
    )
    results = iter(
        [
            bad_window,
            bad_window,
            subprocess.CompletedProcess([], 1, "", ""),
        ]
    )
    sleeps: list[float] = []
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_xdotool",
        lambda *_args, **_kwargs: next(results),
    )
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_bounded_poll_sleep",
        lambda deadline: sleeps.append(deadline),
    )

    assert jetbrains_selector.visible_jetbrains_window_rectangles(deadline=7.0) == ()
    assert sleeps == [7.0, 7.0]


def test_visible_selector_snapshot_bounds_persistent_disappearing_window_race(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[bool] = []
    sleeps: list[bool] = []

    def persistent_race(*_args: str, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(True)
        return subprocess.CompletedProcess(
            [],
            1,
            "",
            "X Error of failed request:  BadWindow (invalid Window parameter)\n"
            "  Major opcode of failed request:  3 (X_GetWindowAttributes)\n",
        )

    monkeypatch.setattr(jetbrains_x11_driver, "_xdotool", persistent_race)
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_bounded_poll_sleep",
        lambda _deadline: sleeps.append(True),
    )

    with pytest.raises(RuntimeError, match="snapshot visible JetBrains windows"):
        jetbrains_selector.visible_jetbrains_window_rectangles(deadline=float("inf"))
    assert calls == [True, True, True]
    assert sleeps == [True, True]


@pytest.mark.parametrize(
    ("returncode", "diagnostic"),
    [
        (0, ""),
        (0, "unexpected warning"),
        (1, "display unavailable"),
        (
            1,
            "X Error of failed request:  BadWindow (invalid Window parameter)\n"
            "  Major opcode of failed request:  3 (X_GetWindowAttributes)\n"
            "transport failed\n",
        ),
        (
            1,
            "prefix X Error of failed request:  BadWindow (invalid Window parameter)\n"
            "  Major opcode of failed request:  3 (X_GetWindowAttributes)\n",
        ),
        (2, "transport failed"),
        (
            2,
            "X Error of failed request:  BadWindow (invalid Window parameter)\n"
            "  Major opcode of failed request:  3 (X_GetWindowAttributes)\n",
        ),
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
        jetbrains_selector.visible_jetbrains_window_rectangles(deadline=1.0)
