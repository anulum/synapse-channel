# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Studio design-system reference page regressions

from __future__ import annotations

from synapse_channel.dashboard_cockpit import COCKPIT_ASSETS, load_cockpit_asset
from synapse_channel.dashboard_studio import (
    STUDIO_REFERENCE_PATH,
    _dot_row,
    _verdict,
    render_studio_reference_html,
)


def test_studio_css_is_a_servable_asset_with_the_design_tokens() -> None:
    assert COCKPIT_ASSETS["studio.css"] == "text/css"
    css = load_cockpit_asset("studio.css")
    # the instrument palette tokens and the reserved verdict colours
    for token in ("--syn-base", "--syn-brand", "--syn-green", "--syn-amber", "--syn-red"):
        assert token in css
    # the three type roles
    for role in ("--syn-font-display", "--syn-font-body", "--syn-font-mono"):
        assert role in css
    # accessibility floor: reduced-motion stills the live pulse
    assert "prefers-reduced-motion" in css


def test_board_and_command_assets_are_fixed_servable_package_data() -> None:
    expected = {
        "board-columns.css": "text/css",
        "board-columns.js": "text/javascript",
        "studio-command.css": "text/css",
        "studio-command.js": "text/javascript",
        "studio-access.js": "text/javascript",
        "studio-feeds.js": "text/javascript",
    }
    assert {name: COCKPIT_ASSETS[name] for name in expected} == expected
    assert ".syn-board-columns" in load_cockpit_asset("board-columns.css")
    assert "SynapseBoardColumns" in load_cockpit_asset("board-columns.js")
    command_css = load_cockpit_asset("studio-command.css")
    assert "prefers-reduced-motion" in command_css
    assert "SynapseStudioCommand" in load_cockpit_asset("studio-command.js")
    assert "SynapseStudioAccess" in load_cockpit_asset("studio-access.js")
    assert "SynapseStudioFeeds" in load_cockpit_asset("studio-feeds.js")


def test_verdict_helper_renders_the_toned_pill() -> None:
    html = _verdict("green", "Green")
    assert 'class="syn-verdict syn-verdict--green"' in html
    assert ">Green<" in html


def test_dot_row_renders_state_and_metadata() -> None:
    row = _dot_row("warn", "gamma", "ci", "lease 84%")
    assert "syn-dot--warn" in row
    assert "gamma" in row and "ci" in row and "lease 84%" in row


def test_reference_page_exercises_the_component_kit() -> None:
    html = render_studio_reference_html()
    assert html.startswith("<!doctype html>")
    assert '<link rel="stylesheet" href="studio.css">' in html
    assert 'class="syn"' in html
    # all three verdict tones appear on the scale, plus the amber headline
    for tone in ("green", "amber", "red"):
        assert f"syn-verdict--{tone}" in html
    # the nav marks the current surface and every status-dot state is shown
    assert 'aria-current="page"' in html
    for state in ("ok", "warn", "bad", "dark"):
        assert f"syn-dot--{state}" in html
    # the path the page is served at is linked back from within the page
    assert STUDIO_REFERENCE_PATH in html
