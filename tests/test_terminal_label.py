# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for composing the tab label and the status lines
"""Label and status-line text: content, wrapping, the profile, and inert output."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from synapse_channel.terminal_label import (
    ELLIPSIS,
    NOT_DECLARED,
    Group,
    ProfileError,
    TerminalDetails,
    default_profile_path,
    derived_abbreviation,
    detail_fields,
    details_for,
    inert,
    load_profile,
    seat_of,
    status_lines,
    tab_label,
)

_GROUP = "AGENTIC-COORDINATION-ASSURANCE-AND-CONTINUITY-SYSTEMS"
_GROUPS = (Group(_GROUP, "ACAACS", False, ("SYNAPSE-CHANNEL", "REMANENTIA")),)
_FULL = TerminalDetails(
    group=_GROUP,
    abbreviation="ACAACS",
    project="SYNAPSE-CHANNEL",
    focus="terminal labels",
    vendor="claude",
    model="claude-opus-5-5",
    seat="SYNAPSE-CHANNEL/claude-f636",
    session_name="anulum-ff",
    native_session="d2b65cca-a4a8-481d-b1bb-9e1348d67e0e",
    terminal_identity="user/terminal-1485372",
)


@pytest.mark.parametrize(
    ("name", "abbreviation"),
    [
        ("SC-NEUROMORPHIC-COMPUTING-SYSTEMS", "SC-NCS"),
        ("SCPN-QUANTUM-COMPUTING-SYSTEMS", "SCPN-QCS"),
        ("SCPN-SYSTEMS-INTEGRATION-AND-CONTROL", "SCPN-SIAC"),
        ("SCPN-REACTOR-SYSTEMS", "SCPN-RS"),
        (_GROUP, "ACAACS"),
        ("web_master team", "WEB-MT"),
        ("SOLO", "S"),
        ("LONGER-NAME", "LN"),
        ("--", NOT_DECLARED),
    ],
)
def test_a_derived_abbreviation_keeps_a_short_family_prefix(name: str, abbreviation: str) -> None:
    assert derived_abbreviation(name) == abbreviation


@pytest.mark.parametrize(
    ("connection", "seat"),
    [
        ("P/seat-rx", "P/seat"),
        ("P/seat-tx", "P/seat"),
        ("P/seat-presence", "P/seat"),
        ("P/seat-pane-rx", "P/seat"),
        ("P/seat", "P/seat"),
        ("-rx", "-rx"),
    ],
)
def test_connection_names_of_one_seat_give_that_seat(connection: str, seat: str) -> None:
    assert seat_of(connection) == seat


def test_identity_and_profile_establish_group_project_and_seat_only() -> None:
    assert details_for(" SYNAPSE-CHANNEL/claude-f636-rx ", _GROUPS) == TerminalDetails(
        group=_GROUP,
        abbreviation="ACAACS",
        project="SYNAPSE-CHANNEL",
        seat="SYNAPSE-CHANNEL/claude-f636",
    )
    # A seat of the group itself: the group name stands where a project would.
    assert details_for(f"{_GROUP}/claude-f636", _GROUPS).abbreviation == "ACAACS"
    assert details_for("OTHER/seat", _GROUPS) == TerminalDetails(project="OTHER", seat="OTHER/seat")
    assert details_for("loner", _GROUPS) == TerminalDetails(seat="loner")
    assert details_for("  ", _GROUPS) == TerminalDetails()
    derived = details_for("X/s", (Group("SCPN-REACTOR-SYSTEMS", "SCPN-RS", True, ("X",)),))
    assert derived.abbreviation_derived


@pytest.mark.parametrize(
    ("details", "label"),
    [
        (_FULL, "ACAACS · SYNAPSE-CHANNEL · claude"),
        (replace(_FULL, focus=NOT_DECLARED), "ACAACS · claude"),
        (replace(_FULL, vendor=NOT_DECLARED), "ACAACS · SYNAPSE-CHANNEL"),
        (replace(_FULL, project=NOT_DECLARED), "ACAACS · claude"),
        (TerminalDetails(project="OTHER", seat="OTHER/s", vendor="codex"), "OTHER · codex"),
        (TerminalDetails(seat="loner"), "loner"),
        (TerminalDetails(vendor="kimi"), "no seat yet · kimi"),
        (TerminalDetails(), "no seat yet"),
        (TerminalDetails(seat="a#(touch x)\x1b]0;evil\x07b"), "a##(touch x)]0;evilb"),
    ],
)
def test_the_tab_label_is_group_then_project_with_a_focus_then_vendor(
    details: TerminalDetails, label: str
) -> None:
    assert tab_label(details) == label


def test_a_long_tab_label_is_cut_before_it_is_made_inert() -> None:
    label = tab_label(TerminalDetails(seat="s" * 59 + "##"))

    assert label == "s" * 59 + ELLIPSIS
    assert tab_label(TerminalDetails(seat="s" * 58 + "##")) == "s" * 58 + "####"


def test_every_detail_is_listed_complete_and_in_order() -> None:
    assert detail_fields(_FULL) == [
        f"group: {_GROUP} (ACAACS)",
        "project: SYNAPSE-CHANNEL · focus: terminal labels",
        "vendor: claude · model: claude-opus-5-5",
        "seat: SYNAPSE-CHANNEL/claude-f636",
        "session: anulum-ff",
        "native session: d2b65cca-a4a8-481d-b1bb-9e1348d67e0e",
        "terminal: user/terminal-1485372",
    ]
    assert detail_fields(replace(_FULL, abbreviation_derived=True))[0] == (
        f"group: {_GROUP} (ACAACS, derived)"
    )
    assert detail_fields(TerminalDetails())[0] == "group: not declared"


def test_widths_60_125_and_240_give_different_line_counts() -> None:
    narrow, medium, wide = (status_lines(_FULL, width) for width in (60, 125, 240))

    assert (len(narrow), len(medium), len(wide)) == (5, 3, 2)
    assert all(len(line) <= 60 for line in narrow)
    assert all(len(line) <= 125 for line in medium)
    assert wide == [
        f"group: {_GROUP} (ACAACS)  |  project: SYNAPSE-CHANNEL · focus: terminal labels  |  "
        "vendor: claude · model: claude-opus-5-5  |  seat: SYNAPSE-CHANNEL/claude-f636  |  "
        "session: anulum-ff",
        "native session: d2b65cca-a4a8-481d-b1bb-9e1348d67e0e  |  terminal: user/terminal-1485372",
    ]
    # Nothing is cut silently: a cut field and a dropped tail both end in an ellipsis.
    assert narrow[0] == f"group: {_GROUP}"[:59] + ELLIPSIS
    assert narrow[-1].endswith(ELLIPSIS)
    assert not any(line.endswith(ELLIPSIS) for line in medium + wide)


def test_a_field_that_fits_exactly_is_not_cut_and_tiny_widths_use_twenty_columns() -> None:
    details = TerminalDetails(seat="s" * 14)

    assert "seat: " + "s" * 14 in status_lines(details, 20)
    assert all(len(line) <= 20 for line in status_lines(replace(details, seat="s" * 15), 3))
    assert status_lines(replace(details, seat="s" * 15), 20)[3] == "seat: " + "s" * 13 + ELLIPSIS


def test_status_lines_are_measured_before_they_are_made_inert() -> None:
    details = TerminalDetails(seat="#" * 30)

    lines = status_lines(details, 40)

    assert "seat: " + "#" * 60 in lines
    assert inert("a#b\x00\x1b\x7f\x9bc") == "a##bc"


def test_details_survive_json_and_malformed_json_gives_defaults() -> None:
    derived = replace(_FULL, abbreviation_derived=True)

    assert TerminalDetails.from_json(derived.to_json()) == derived
    assert json.loads(_FULL.to_json())["focus"] == "terminal labels"
    for text in ("", "{", "[]", "null", '"x"'):
        assert TerminalDetails.from_json(text) == TerminalDetails()
    partial = TerminalDetails.from_json(
        json.dumps(
            {"focus": "f", "model": 7, "vendor": "  ", "abbreviation_derived": "yes", "x": 1}
        )
    )
    assert partial == TerminalDetails(focus="f")


def test_the_profile_gives_groups_with_declared_or_derived_abbreviations(tmp_path: Path) -> None:
    path = tmp_path / "profile.json"
    path.write_text(
        json.dumps(
            {
                "groups": {
                    _GROUP: {"abbreviation": " ACAACS ", "projects": ["SYNAPSE-CHANNEL"]},
                    "SCPN-REACTOR-SYSTEMS": {"projects": []},
                    "BARE": {"abbreviation": "  "},
                }
            }
        ),
        encoding="utf-8",
    )

    assert load_profile(path) == (
        Group(_GROUP, "ACAACS", False, ("SYNAPSE-CHANNEL",)),
        Group("SCPN-REACTOR-SYSTEMS", "SCPN-RS", True, ()),
        Group("BARE", "B", True, ()),
    )
    assert load_profile(tmp_path / "absent.json") == ()


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"{", "is not JSON"),
        (b"[]", 'needs an object "groups"'),
        (b'{"groups": []}', 'needs an object "groups"'),
        (b'{"groups": {"G": 3}}', 'group "G" must be an object'),
        (b'{"groups": {"G": {"projects": "P"}}}', 'group "G" needs a list of project names'),
        (b'{"groups": {"G": {"projects": [1]}}}', 'group "G" needs a list of project names'),
        (b"\xff\xfe", "cannot read"),
    ],
)
def test_an_unusable_profile_is_an_error_not_an_empty_profile(
    content: bytes, message: str, tmp_path: Path
) -> None:
    path = tmp_path / "profile.json"
    path.write_bytes(content)

    with pytest.raises(ProfileError, match=message):
        load_profile(path)


def test_the_profile_is_looked_for_under_the_configuration_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert default_profile_path() == tmp_path / "synapse" / "terminal_label_profile.json"
    assert load_profile() == ()
    monkeypatch.delenv("XDG_CONFIG_HOME")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert default_profile_path() == (
        tmp_path / "home" / ".config" / "synapse" / "terminal_label_profile.json"
    )
