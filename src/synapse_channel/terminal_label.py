# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — what a terminal tab and its status line say about a seat
"""Compose the tab label and the status lines that identify a seat's terminal.

The tab label is short: the group's abbreviation, the project when a specific
focus is assigned, and the vendor. The status lines carry every detail, wrapped
to as many lines as the terminal width needs.

Nothing here talks to a terminal. :mod:`synapse_channel.terminal_label_tmux`
applies the result to one tmux session.

**Every value is client-chosen text.** An identity, a focus or a model id ends
in a tmux format string, and tmux expands ``#(command)`` there by running the
command. :func:`inert` is therefore applied to every line this module returns
for display: control characters are removed and each ``#`` is doubled, so tmux
shows the text and expands nothing.

The product does not know the groups of any organisation. Group names,
abbreviations and member projects come from a profile file; without one a
group is "not declared". A group listed without an abbreviation gets a derived
one, and the status line says that it is derived.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from synapse_channel.core.errors import SynapseError

NOT_DECLARED = "not declared"
"""Shown for a detail that nobody declared; the product never guesses one."""

SEPARATOR = " · "
"""Between the parts of the tab label."""

FIELD_SEPARATOR = "  |  "
"""Between two details on one status line."""

MAX_STATUS_LINES = 5
"""tmux draws at most five status lines."""

MAX_LABEL_LENGTH = 60
"""Longest tab label before it is cut with an ellipsis."""

ELLIPSIS = "…"

PROFILE_FILENAME = "terminal_label_profile.json"
"""Name of the profile file under the Synapse configuration directory."""

_RECEIVER_SUFFIXES = ("-presence", "-pane-rx", "-rx", "-tx")
"""Suffixes of connection names that belong to one seat."""

_FAMILY_PREFIX_MAX = 4
"""A first word of at most this many letters is kept whole in a derived abbreviation."""


class ProfileError(SynapseError, ValueError):
    """The profile file exists but cannot be used."""

    code = "terminal_label_profile"


@dataclass(frozen=True, slots=True)
class TerminalDetails:
    """Everything a terminal can say about the seat working in it."""

    group: str = NOT_DECLARED
    abbreviation: str = NOT_DECLARED
    abbreviation_derived: bool = False
    project: str = NOT_DECLARED
    focus: str = NOT_DECLARED
    vendor: str = NOT_DECLARED
    model: str = NOT_DECLARED
    seat: str = NOT_DECLARED
    session_name: str = NOT_DECLARED
    native_session: str = NOT_DECLARED
    terminal_identity: str = NOT_DECLARED

    def to_json(self) -> str:
        """Return the details as one JSON object."""
        return json.dumps(asdict(self), sort_keys=True)

    @classmethod
    def from_json(cls, text: str) -> TerminalDetails:
        """Return details stored by :meth:`to_json`; unknown or malformed input gives defaults."""
        try:
            data = json.loads(text)
        except (TypeError, ValueError):
            return cls()
        if not isinstance(data, dict):
            return cls()
        known = cls()
        values: dict[str, str | bool] = {}
        for name, default in asdict(known).items():
            value = data.get(name, default)
            if isinstance(default, bool):
                values[name] = value is True
            elif isinstance(value, str) and value.strip():
                values[name] = value
        return replace(known, **values)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class Group:
    """One group of the profile."""

    name: str
    abbreviation: str
    derived: bool
    projects: tuple[str, ...]


def inert(text: str) -> str:
    """Return ``text`` so that tmux displays it and expands nothing.

    Control characters, including escape and the C1 range, are removed, and
    every ``#`` is doubled. The result is safe inside ``set-titles-string`` and
    ``status-format``.
    """
    kept = "".join(
        character for character in text if not (ord(character) < 32 or 127 <= ord(character) < 160)
    )
    return kept.replace("#", "##")


def derived_abbreviation(group: str) -> str:
    """Return the initials of a group name, keeping a short family prefix whole.

    ``SCPN-QUANTUM-COMPUTING-SYSTEMS`` gives ``SCPN-QCS``; a name whose first
    word is longer than four letters gives the initials of every word.
    """
    words = [word for word in group.replace("_", "-").replace(" ", "-").split("-") if word]
    if not words:
        return NOT_DECLARED
    if len(words) > 1 and len(words[0]) <= _FAMILY_PREFIX_MAX:
        return f"{words[0].upper()}-{''.join(word[0].upper() for word in words[1:])}"
    return "".join(word[0].upper() for word in words)


def default_profile_path() -> Path:
    """Return where the profile file is looked for."""
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "synapse" / PROFILE_FILENAME


def load_profile(path: Path | None = None) -> tuple[Group, ...]:
    """Read the groups of the profile file; a missing file gives no groups.

    The file is JSON: ``{"groups": {"<full name>": {"abbreviation": "…",
    "projects": ["…"]}}}``. ``abbreviation`` may be left out and is then derived.

    Raises
    ------
    ProfileError
        The file exists but is not readable JSON of that shape.
    """
    source = default_profile_path() if path is None else path
    try:
        text = source.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ()
    except (OSError, UnicodeDecodeError) as exc:
        raise ProfileError(f"cannot read {source}: {exc}") from exc
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ProfileError(f"{source} is not JSON: {exc}") from exc
    groups = data.get("groups") if isinstance(data, dict) else None
    if not isinstance(groups, dict):
        raise ProfileError(f'{source} needs an object "groups"')
    found = []
    for name, entry in groups.items():
        if not isinstance(entry, dict):
            raise ProfileError(f'{source}: group "{name}" must be an object')
        abbreviation = entry.get("abbreviation")
        projects = entry.get("projects", [])
        if not isinstance(projects, list) or not all(isinstance(item, str) for item in projects):
            raise ProfileError(f'{source}: group "{name}" needs a list of project names')
        declared = isinstance(abbreviation, str) and bool(abbreviation.strip())
        found.append(
            Group(
                name=name,
                abbreviation=str(abbreviation).strip() if declared else derived_abbreviation(name),
                derived=not declared,
                projects=tuple(projects),
            )
        )
    return tuple(found)


def seat_of(connection_name: str) -> str:
    """Return the seat a connection name belongs to.

    ``PROJECT/seat-rx`` and ``PROJECT/seat-presence`` are connections of the
    seat ``PROJECT/seat``.
    """
    for suffix in _RECEIVER_SUFFIXES:
        if connection_name.endswith(suffix) and len(connection_name) > len(suffix):
            return connection_name[: -len(suffix)]
    return connection_name


def details_for(identity: str, groups: tuple[Group, ...]) -> TerminalDetails:
    """Return what the identity and the profile alone establish.

    The project is the part before the first ``/``. The group is the profile
    group that lists the project, or the group of that very name. Everything
    else stays "not declared".
    """
    seat = seat_of(identity.strip())
    if not seat:
        return TerminalDetails()
    project = seat.split("/", 1)[0] if "/" in seat else NOT_DECLARED
    for group in groups:
        if project in group.projects or project == group.name:
            return TerminalDetails(
                group=group.name,
                abbreviation=group.abbreviation,
                abbreviation_derived=group.derived,
                project=project,
                seat=seat,
            )
    return TerminalDetails(project=project, seat=seat)


def _declared(value: str) -> bool:
    return value != NOT_DECLARED


def tab_label(details: TerminalDetails) -> str:
    """Return the tab label, ready for display.

    ``<group abbreviation> · <project, when a focus is assigned> · <vendor>``.
    Without a group the project stands first; without a seat the label says
    so. The text is cut at 60 characters and made inert.
    """
    parts = []
    if _declared(details.abbreviation):
        parts.append(details.abbreviation)
        if _declared(details.focus) and _declared(details.project):
            parts.append(details.project)
    elif _declared(details.project):
        parts.append(details.project)
    elif not _declared(details.seat):
        parts.append("no seat yet")
    else:
        parts.append(details.seat)
    if _declared(details.vendor):
        parts.append(details.vendor)
    text = SEPARATOR.join(parts)
    if len(text) > MAX_LABEL_LENGTH:
        text = text[: MAX_LABEL_LENGTH - 1] + ELLIPSIS
    return inert(text)


def detail_fields(details: TerminalDetails) -> list[str]:
    """Return every detail as readable text, complete and in display order."""
    group = details.group
    if _declared(details.abbreviation):
        marker = ", derived" if details.abbreviation_derived else ""
        group = f"{details.group} ({details.abbreviation}{marker})"
    return [
        f"group: {group}",
        f"project: {details.project}{SEPARATOR}focus: {details.focus}",
        f"vendor: {details.vendor}{SEPARATOR}model: {details.model}",
        f"seat: {details.seat}",
        f"session: {details.session_name}",
        f"native session: {details.native_session}",
        f"terminal: {details.terminal_identity}",
    ]


def _cut(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 1] + ELLIPSIS


def status_lines(details: TerminalDetails, width: int) -> list[str]:
    """Return the details packed into status lines of at most ``width`` characters.

    Fields are packed greedily in display order. A field longer than a line is
    cut with an ellipsis; it stays complete in :func:`detail_fields`. At most
    five lines are returned; when more would be needed the fifth ends with an
    ellipsis. Each line is made inert after it is measured, so a doubled ``#``
    does not change the wrapping.
    """
    limit = max(width, 20)
    lines: list[str] = []
    for field in detail_fields(details):
        candidate = _cut(field, limit)
        if lines and len(lines[-1]) + len(FIELD_SEPARATOR) + len(candidate) <= limit:
            lines[-1] = lines[-1] + FIELD_SEPARATOR + candidate
        else:
            lines.append(candidate)
    if len(lines) > MAX_STATUS_LINES:
        last = lines[MAX_STATUS_LINES - 1]
        lines = lines[: MAX_STATUS_LINES - 1] + [_cut(last, limit - 1) + ELLIPSIS]
    return [inert(line) for line in lines]
