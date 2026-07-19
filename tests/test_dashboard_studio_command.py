# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Studio command-centre renderer regressions

from __future__ import annotations

import json
import re

from synapse_channel.dashboard_access_http import DASHBOARD_ACCESS_PATH
from synapse_channel.dashboard_studio import STUDIO_REFERENCE_PATH
from synapse_channel.dashboard_studio_command import (
    DEFAULT_POLL_SECONDS,
    EVENTS_FEED_PATH,
    OPERATOR_ACTIONS_FEED_PATH,
    STUDIO_COMMAND_PATH,
    STUDIO_COMMAND_SCRIPTS,
    STUDIO_COMMAND_STYLES,
    _runtime_config,
    render_studio_command_html,
)
from synapse_channel.studio_snapshot import STUDIO_SNAPSHOT_PATH


def test_command_path_is_a_subpath_of_studio() -> None:
    assert STUDIO_COMMAND_PATH == "/studio/command"


def test_page_loads_every_fixed_asset_by_absolute_path() -> None:
    html = render_studio_command_html()
    for asset in STUDIO_COMMAND_STYLES:
        assert f'href="/{asset}"' in html
    positions = [html.index(f'src="/{asset}"') for asset in STUDIO_COMMAND_SCRIPTS]
    assert positions == sorted(positions)
    assert "<style>" not in html


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
        'id="cc-peers"',
        'id="cc-connection"',
        'id="cc-access"',
        'id="cc-hub"',
        'id="cc-version"',
        'id="cc-offline"',
        'id="cc-fallback-body"',
        'id="cc-board-columns"',
        'id="cc-board-title"',
        'id="cc-posture-list"',
        'id="cc-peers-list"',
        'id="cc-livefeed-list"',
        "Coordination clock",
        "coordination clock",  # the panel label
        "security posture",
        "observed peers (advisory)",
        "live feed",
        "exact board and claim states",
    ):
        assert hook in html, hook


def test_page_navigates_between_command_and_design() -> None:
    html = render_studio_command_html()
    assert f'href="{STUDIO_COMMAND_PATH}" aria-current="page"' in html
    assert f'href="{STUDIO_REFERENCE_PATH}"' in html
    assert 'href="#cc-board-columns"' in html
    assert 'href="#cc-livefeed-list"' in html
    assert 'href="#cc-peers-list"' in html
    assert 'aria-label="Studio navigation"' in html


def test_page_carries_the_accessible_claims_fallback_and_board_boundary() -> None:
    html = render_studio_command_html()
    assert "cc-fallback" in html
    assert "Read-only projection; actions remain hub-enforced." in html
    assert 'aria-live="polite"' in html


def test_runtime_config_binds_only_fixed_paths_and_poll_interval() -> None:
    assert json.loads(_runtime_config(poll_seconds=8)) == {
        "accessUrl": DASHBOARD_ACCESS_PATH,
        "eventsUrl": EVENTS_FEED_PATH,
        "operatorActionsUrl": OPERATOR_ACTIONS_FEED_PATH,
        "pollMs": 8000,
        "snapshotUrl": STUDIO_SNAPSHOT_PATH,
    }


def test_runtime_config_is_inert_json_not_inline_executable_code() -> None:
    html = render_studio_command_html()
    match = re.search(
        r'<script id="syn-studio-config" type="application/json">([^<]+)</script>', html
    )
    assert match is not None
    assert json.loads(match.group(1))["pollMs"] == DEFAULT_POLL_SECONDS * 1000
    assert "<script>" not in html
    assert "window.__SYN_STUDIO__" not in html


def test_access_asset_precedes_the_self_starting_command_asset() -> None:
    assert STUDIO_COMMAND_SCRIPTS.index("studio-access.js") < STUDIO_COMMAND_SCRIPTS.index(
        "studio-command.js"
    )


def test_default_poll_interval_is_applied() -> None:
    html = render_studio_command_html()
    assert f'"pollMs":{DEFAULT_POLL_SECONDS * 1000}' in html
    custom = render_studio_command_html(poll_seconds=20)
    assert '"pollMs":20000' in custom
    floored = render_studio_command_html(poll_seconds=0)
    assert '"pollMs":1000' in floored
