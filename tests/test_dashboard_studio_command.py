# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Studio command-centre renderer regressions

from __future__ import annotations

from synapse_channel.dashboard_studio import STUDIO_REFERENCE_PATH
from synapse_channel.dashboard_studio_command import (
    DEFAULT_POLL_SECONDS,
    STUDIO_COMMAND_PATH,
    _script,
    render_studio_command_html,
)
from synapse_channel.studio_snapshot import STUDIO_SNAPSHOT_PATH


def test_command_path_is_a_subpath_of_studio() -> None:
    assert STUDIO_COMMAND_PATH == "/studio/command"


def test_page_loads_the_design_system_by_absolute_path() -> None:
    # the page lives at a subpath, so a relative stylesheet href would 404 — it must be absolute
    html = render_studio_command_html()
    assert 'href="/studio.css"' in html
    assert "studio.css" in html


def test_page_carries_every_instrument_hook() -> None:
    html = render_studio_command_html()
    for hook in (
        'id="cc-clock"',
        'id="cc-verdict"',
        'id="cc-agents"',
        'id="cc-claims"',
        'id="cc-tasks"',
        'id="cc-conflicts"',
        'id="cc-signals"',
        'id="cc-posture"',
        'id="cc-offline"',
        'id="cc-fallback-body"',
        'id="cc-posture-list"',
        "Coordination clock",
        "coordination clock",  # the panel label
        "security posture",
    ):
        assert hook in html, hook


def test_page_navigates_between_command_and_design() -> None:
    html = render_studio_command_html()
    assert f'href="{STUDIO_COMMAND_PATH}" aria-current="page"' in html
    assert f'href="{STUDIO_REFERENCE_PATH}"' in html


def test_page_honours_reduced_motion_and_a_table_fallback() -> None:
    html = render_studio_command_html()
    assert "prefers-reduced-motion" in html
    assert "cc-sweep" in html  # the radar sweep that reduced-motion stills
    assert "cc-fallback" in html  # the claims-table fallback it reveals


def test_script_binds_the_snapshot_path_and_poll_interval() -> None:
    script = _script(snapshot_path=STUDIO_SNAPSHOT_PATH, poll_seconds=8)
    assert f'"{STUDIO_SNAPSHOT_PATH}"' in script
    assert "8000" in script  # seconds rendered as milliseconds
    assert "__SNAPSHOT__" not in script and "__POLL_MS__" not in script  # fully substituted
    assert "drawClock" in script and "fetch(SNAPSHOT" in script
    assert "cc-posture-list" in script


def test_default_poll_interval_is_applied() -> None:
    html = render_studio_command_html()
    assert f"{DEFAULT_POLL_SECONDS * 1000}" in html
    custom = render_studio_command_html(poll_seconds=20)
    assert "20000" in custom
