# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — fail-closed JetBrains X11 focus tests
"""Verify bounded focus ancestry without trusting an ambient X11 window."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from e2e.opencode_editors.jetbrains_x11_focus import focus_belongs_to_project

ParentLookup = Callable[[int], tuple[int | None, int | None]]


def _lookup(mapping: dict[int, tuple[int | None, int | None]]) -> ParentLookup:
    return lambda window: mapping.get(window, (None, None))


def test_focus_accepts_the_project_frame_without_parent_queries() -> None:
    def unexpected_lookup(_window: int) -> tuple[int | None, int | None]:
        raise AssertionError("same-window focus must not query X11 ancestry")

    assert focus_belongs_to_project(100, 100, unexpected_lookup) is True


def test_focus_accepts_a_bounded_nested_swing_composer() -> None:
    parents = _lookup({300: (1, 200), 200: (1, 100)})

    assert focus_belongs_to_project(100, 300, parents) is True


@pytest.mark.parametrize(
    "parents",
    [
        {300: (1, 1)},
        {300: (1, 200), 200: (1, 300)},
        {300: (None, None)},
        {300: (1, 0)},
    ],
)
def test_focus_rejects_ambient_incomplete_or_cyclic_ancestry(
    parents: dict[int, tuple[int | None, int | None]],
) -> None:
    assert focus_belongs_to_project(100, 300, _lookup(parents)) is False


def test_focus_rejects_a_chain_beyond_the_depth_bound() -> None:
    parents = _lookup({300: (1, 200), 200: (1, 100)})

    assert focus_belongs_to_project(100, 300, parents, max_depth=1) is False


@pytest.mark.parametrize(
    ("project", "focused", "depth"),
    [(0, 1, 16), (1, 0, 16), (-1, 1, 16), (1, -1, 16), (1, 1, 0)],
)
def test_focus_rejects_invalid_xids_or_bounds(project: int, focused: int, depth: int) -> None:
    assert focus_belongs_to_project(project, focused, _lookup({}), max_depth=depth) is False
