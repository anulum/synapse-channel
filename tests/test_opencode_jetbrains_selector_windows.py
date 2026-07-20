# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — JetBrains ACP selector window-contract regressions
"""Verify selector geometry, ownership, and bounded X11 snapshot classification."""

from __future__ import annotations

import subprocess

import pytest

from e2e.opencode_editors import (
    jetbrains_selector,
    jetbrains_selector_windows,
    jetbrains_x11_driver,
)
from e2e.opencode_editors.jetbrains_selector import select_pinned_agent
from e2e.opencode_editors.jetbrains_x11_geometry import X11WindowRectangle


@pytest.mark.parametrize(
    ("geometry", "phase", "expected"),
    [
        ((310, 407), jetbrains_selector_windows._SelectorGeometryPhase.UNFILTERED, True),
        ((310, 201), jetbrains_selector_windows._SelectorGeometryPhase.UNFILTERED, False),
        ((310, 201), jetbrains_selector_windows._SelectorGeometryPhase.FILTERED_READY, True),
        ((310, 42), jetbrains_selector_windows._SelectorGeometryPhase.FILTERED_READY, False),
        ((310, 1), jetbrains_selector_windows._SelectorGeometryPhase.FILTERED_VISIBLE, True),
        ((311, 200), jetbrains_selector_windows._SelectorGeometryPhase.FILTERED_VISIBLE, False),
    ],
)
def test_selector_window_geometry_is_phase_specific(
    geometry: tuple[int, int],
    phase: jetbrains_selector_windows._SelectorGeometryPhase,
    expected: bool,
) -> None:
    """Classify only geometry admitted by the active selector phase."""
    assert jetbrains_selector_windows._selector_geometry_matches(geometry, phase) is expected


def test_selector_window_refuses_owned_phase_invalid_remap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An owner-proven invalid remap is drift, never evidence of closure."""
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
        "_required_window_transient_for",
        lambda *_args, **_kwargs: 123,
    )

    with pytest.raises(RuntimeError, match="geometry changed after confirmation"):
        jetbrains_selector_windows.owned_agent_selector_popups(
            (X11WindowRectangle("replacement", 0, 1, 2, 311, 200),),
            123,
            deadline=1.0,
            geometry_phase=jetbrains_selector_windows._SelectorGeometryPhase.FILTERED_VISIBLE,
        )


def test_selector_lifecycle_refuses_owned_phase_invalid_remap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A phase-invalid remapped selector cannot count as a clean absence."""
    ownership_queries: list[tuple[str, str]] = []

    def required_name(window: str, **_kwargs: object) -> str:
        ownership_queries.append(("name", window))
        return "win0"

    def required_root(window: str, **_kwargs: object) -> bool:
        ownership_queries.append(("root", window))
        return True

    def required_transient(window: str, **_kwargs: object) -> int:
        ownership_queries.append(("transient", window))
        return 123

    monkeypatch.setattr(jetbrains_selector, "is_agent_selector_popup", lambda *_a, **_k: True)
    monkeypatch.setattr(
        jetbrains_selector,
        "visible_agent_selector_popups",
        lambda *_args, **_kwargs: ("selector",),
    )
    monkeypatch.setattr(
        jetbrains_selector,
        "visible_jetbrains_window_rectangles",
        lambda **_kwargs: (X11WindowRectangle("replacement", 0, 1, 2, 311, 200),),
    )
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_required_window_name",
        required_name,
    )
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_required_window_is_root_child",
        required_root,
    )
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_required_window_transient_for",
        required_transient,
    )
    monkeypatch.setattr(jetbrains_x11_driver, "_checked_xdotool", lambda *_a, **_k: None)
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_focus_window_for_input",
        lambda *_a, **_k: None,
    )

    with pytest.raises(RuntimeError, match="geometry changed after confirmation"):
        select_pinned_agent("selector", "123", deadline=float("inf"))

    assert ownership_queries == [
        ("name", "replacement"),
        ("root", "replacement"),
        ("transient", "replacement"),
    ]


def test_selector_window_snapshot_retries_only_canonical_badwindow_race(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retry a disappearing XID and accept only the later exact empty search."""
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

    assert jetbrains_selector_windows.visible_jetbrains_window_rectangles(deadline=7.0) == ()
    assert sleeps == [7.0]


def test_selector_window_snapshot_retries_query_tree_badwindow_race(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retry when a descendant disappears during the batched X11 tree query."""
    results = iter(
        [
            subprocess.CompletedProcess(
                [],
                1,
                "",
                "X Error of failed request:  BadWindow (invalid Window parameter)\n"
                "  Major opcode of failed request:  15 (X_QueryTree)\n"
                "  Resource id in failed request:  0x200322\n"
                "  Serial number of failed request:  83\n"
                "  Current serial number in output stream:  83\n",
            ),
            subprocess.CompletedProcess([], 1, "", ""),
        ]
    )
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_xdotool",
        lambda *_args, **_kwargs: next(results),
    )
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_bounded_poll_sleep",
        lambda _deadline: None,
    )

    assert jetbrains_selector_windows.visible_jetbrains_window_rectangles(deadline=7.0) == ()


def test_selector_window_skips_candidate_that_disappears_during_title_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Carry a canonical GetProperty race through classification as a non-match."""
    monkeypatch.setattr(
        jetbrains_x11_driver,
        "_xdotool",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [],
            1,
            "",
            "X Error of failed request:  BadWindow (invalid Window parameter)\n"
            "  Major opcode of failed request:  20 (X_GetProperty)\n"
            "  Resource id in failed request:  0x40030d\n"
            "  Serial number of failed request:  22\n"
            "  Current serial number in output stream:  22\n",
        ),
    )

    assert (
        jetbrains_selector_windows.owned_agent_selector_popups(
            (X11WindowRectangle("4195085", 0, 1, 2, 310, 407),),
            123,
            deadline=7.0,
        )
        == ()
    )
