# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — fail-closed JetBrains X11 geometry parsing
"""Parse single and batched ``xdotool`` window-geometry records."""

from __future__ import annotations

from dataclasses import dataclass

_REQUIRED_GEOMETRY_FIELDS = frozenset({"SCREEN", "X", "Y", "WIDTH", "HEIGHT"})


@dataclass(frozen=True, slots=True)
class X11WindowRectangle:
    """One validated batched X11 window-geometry record.

    Parameters
    ----------
    window:
        Canonical positive decimal X11 window identifier.
    screen:
        Non-negative X11 screen index.
    x, y:
        Window origin, which may be negative on multi-monitor desktops.
    width, height:
        Strictly positive window dimensions.
    """

    window: str
    screen: int
    x: int
    y: int
    width: int
    height: int

    @property
    def geometry(self) -> tuple[int, int]:
        """Return the window dimensions as ``(width, height)``."""
        return self.width, self.height


def _record_fields(lines: list[str]) -> dict[str, str]:
    """Return one key/value record, rejecting malformed or duplicate fields."""
    fields: dict[str, str] = {}
    for line in lines:
        key, separator, value = line.partition("=")
        if not separator or not key or not value or key in fields:
            raise ValueError(f"malformed xdotool geometry field: {line!r}")
        fields[key] = value
    return fields


def _validated_rectangle(fields: dict[str, str]) -> tuple[int, int, int, int, int]:
    """Validate and convert the geometry fields shared by both output forms."""
    missing = _REQUIRED_GEOMETRY_FIELDS.difference(fields)
    if missing:
        raise ValueError(f"xdotool geometry record is missing fields: {sorted(missing)!r}")
    try:
        rectangle = (
            int(fields["SCREEN"]),
            int(fields["X"]),
            int(fields["Y"]),
            int(fields["WIDTH"]),
            int(fields["HEIGHT"]),
        )
    except ValueError as exc:
        raise ValueError("xdotool geometry record contains a non-integer value") from exc
    screen, _x, _y, width, height = rectangle
    if screen < 0 or width <= 0 or height <= 0:
        raise ValueError("xdotool geometry record contains an invalid extent")
    return rectangle


def parse_window_rectangle(output: str) -> tuple[int, int, int, int, int] | None:
    """Parse one ``getwindowgeometry --shell`` result.

    Parameters
    ----------
    output:
        Standard output from one non-batched ``xdotool`` geometry query.

    Returns
    -------
    tuple[int, int, int, int, int] | None
        Validated ``(screen, x, y, width, height)`` values, or ``None`` for
        malformed output from a window that may have vanished.
    """
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    try:
        return _validated_rectangle(_record_fields(lines))
    except ValueError:
        return None


def parse_window_rectangles(output: str) -> tuple[X11WindowRectangle, ...]:
    """Parse batched ``search ... getwindowgeometry --shell %@`` output.

    Parameters
    ----------
    output:
        Standard output containing zero or more ``WINDOW=``-delimited records.

    Returns
    -------
    tuple[X11WindowRectangle, ...]
        Validated records in the stacking order reported by ``xdotool``.

    Raises
    ------
    ValueError
        If any non-empty record is incomplete, duplicated, or invalid. Partial
        results are never returned.
    """
    records: list[X11WindowRectangle] = []
    current: list[str] = []

    def append_current() -> None:
        if not current:
            return
        fields = _record_fields(current)
        window_raw = fields["WINDOW"]
        try:
            window = int(window_raw)
        except ValueError as exc:
            raise ValueError("xdotool batch geometry WINDOW is not an integer") from exc
        if window <= 0:
            raise ValueError("xdotool batch geometry WINDOW must be positive")
        screen, x, y, width, height = _validated_rectangle(fields)
        records.append(
            X11WindowRectangle(
                window=str(window),
                screen=screen,
                x=x,
                y=y,
                width=width,
                height=height,
            )
        )

    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("WINDOW="):
            append_current()
            current = [line]
        elif not current:
            raise ValueError("xdotool batch geometry data preceded its WINDOW field")
        else:
            current.append(line)
    append_current()
    return tuple(records)
