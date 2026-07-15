# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — fail-closed JetBrains X11 geometry tests
"""Verify strict single and batched ``xdotool`` geometry parsing."""

from __future__ import annotations

import pytest

from e2e.opencode_editors.jetbrains_x11_geometry import (
    X11WindowRectangle,
    parse_window_rectangle,
    parse_window_rectangles,
)

_SINGLE_RECTANGLE = "X=-10\nY=20\nWIDTH=310\nHEIGHT=407\nSCREEN=0\n"
_BATCH_RECTANGLES = (
    "WINDOW=456\nX=-10\nY=20\nWIDTH=310\nHEIGHT=407\nSCREEN=0\n"
    "WINDOW=789\nX=30\nY=40\nWIDTH=1400\nHEIGHT=1000\nSCREEN=1\n"
)


def test_single_rectangle_accepts_valid_geometry() -> None:
    assert parse_window_rectangle(_SINGLE_RECTANGLE) == (0, -10, 20, 310, 407)


@pytest.mark.parametrize(
    "output",
    [
        "",
        "X=1\n",
        _SINGLE_RECTANGLE + "WIDTH=310\n",
        _SINGLE_RECTANGLE.replace("SCREEN=0", "SCREEN=invalid"),
        _SINGLE_RECTANGLE.replace("SCREEN=0", "SCREEN=-1"),
        _SINGLE_RECTANGLE.replace("WIDTH=310", "WIDTH=0"),
        _SINGLE_RECTANGLE.replace("HEIGHT=407", "HEIGHT=-1"),
        _SINGLE_RECTANGLE + "malformed\n",
    ],
)
def test_single_rectangle_returns_none_for_malformed_or_invalid_output(output: str) -> None:
    assert parse_window_rectangle(output) is None


def test_batch_rectangles_preserve_order_and_canonicalise_window_ids() -> None:
    assert parse_window_rectangles(f"\n{_BATCH_RECTANGLES}\n") == (
        X11WindowRectangle("456", 0, -10, 20, 310, 407),
        X11WindowRectangle("789", 1, 30, 40, 1400, 1000),
    )
    assert parse_window_rectangles(_BATCH_RECTANGLES)[0].geometry == (310, 407)
    assert parse_window_rectangles("") == ()


@pytest.mark.parametrize(
    "output",
    [
        "X=1\nWINDOW=456\nY=2\nWIDTH=3\nHEIGHT=4\nSCREEN=0\n",
        "WINDOW=456\nX=1\nY=2\nWIDTH=3\nHEIGHT=4\n",
        "WINDOW=456\nX=1\nY=2\nWIDTH=3\nHEIGHT=4\nSCREEN=0\nX=1\n",
        "WINDOW=invalid\nX=1\nY=2\nWIDTH=3\nHEIGHT=4\nSCREEN=0\n",
        "WINDOW=0\nX=1\nY=2\nWIDTH=3\nHEIGHT=4\nSCREEN=0\n",
        "WINDOW=456\nX=invalid\nY=2\nWIDTH=3\nHEIGHT=4\nSCREEN=0\n",
        "WINDOW=456\nX=1\nY=2\nWIDTH=0\nHEIGHT=4\nSCREEN=0\n",
        "WINDOW=456\nX=1\nY=2\nWIDTH=3\nHEIGHT=4\nSCREEN=0\nmalformed\n",
    ],
)
def test_batch_rectangles_fail_closed_on_any_malformed_record(output: str) -> None:
    with pytest.raises(ValueError):
        parse_window_rectangles(output)
