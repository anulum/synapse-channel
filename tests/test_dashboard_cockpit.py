# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — fleet nerve-center cockpit regressions

from __future__ import annotations

import pytest

from hub_e2e_helpers import http_get, running_hub
from synapse_channel.core.hub import SynapseHub
from synapse_channel.dashboard import start_dashboard_server
from synapse_channel.dashboard_cockpit import (
    COCKPIT_ASSETS,
    load_cockpit_asset,
    render_cockpit_html,
)

# ---------- assets ----------


def test_cockpit_assets_mapping() -> None:
    assert COCKPIT_ASSETS == {
        "cockpit.css": "text/css",
        "cockpit.js": "text/javascript",
        "studio.css": "text/css",
    }


def test_load_cockpit_asset_returns_content() -> None:
    assert ":root" in load_cockpit_asset("cockpit.css")
    assert "SYNAPSE" in load_cockpit_asset("cockpit.js")


def test_cockpit_assets_render_the_risk_view() -> None:
    assert "renderRisk" in load_cockpit_asset("cockpit.js")
    assert "fetchReceipts" in load_cockpit_asset("cockpit.js")
    assert "receiptsUrl" in load_cockpit_asset("cockpit.js")
    assert ".risk__verdict" in load_cockpit_asset("cockpit.css")


def test_load_cockpit_asset_rejects_unknown() -> None:
    with pytest.raises(KeyError):
        load_cockpit_asset("../secrets.css")


# ---------- shell rendering ----------


def test_render_cockpit_html_embeds_shell_and_fallback() -> None:
    html = render_cockpit_html(refresh_seconds=5, fallback_html="<p>FALLBACK-MARKER</p>")
    for needle in (
        'id="fleet-svg"',
        'id="vitals"',
        'id="beacon"',
        'id="lanes"',
        'id="risk"',
        'id="risk-verdict"',
        'id="receipts"',
        'href="cockpit.css"',
        'src="cockpit.js"',
        "refreshSeconds: 5",
        'receiptsUrl: "receipts.json"',
        "<noscript>",
        "FALLBACK-MARKER",
    ):
        assert needle in html, needle


def test_render_cockpit_html_coerces_refresh_floor() -> None:
    assert "refreshSeconds: 1" in render_cockpit_html(refresh_seconds=0, fallback_html="")


# ---------- real HTTP asset serving ----------


async def test_dashboard_serves_cockpit_assets_and_404() -> None:
    async with running_hub(SynapseHub()) as (_hub, uri):
        server = start_dashboard_server(
            host="127.0.0.1",
            port=0,
            uri=uri,
            name="SYNAPSE-CHANNEL/dashboard",
            token=None,
            ready_timeout=1.0,
            response_timeout=1.0,
            refresh_seconds=5,
            allow_non_loopback=False,
        )
        base = f"http://{server.host}:{server.port}"
        try:
            css_status, css_headers, css_body = await http_get(base, "/cockpit.css")
            js_status, js_headers, js_body = await http_get(base, "/cockpit.js")
            missing_status, _, _ = await http_get(base, "/nope.png")
        finally:
            server.close()

    assert css_status == 200
    assert css_headers["Content-Type"].startswith("text/css")
    assert ":root" in css_body
    assert js_status == 200
    assert js_headers["Content-Type"].startswith("text/javascript")
    assert "fleet" in js_body
    assert missing_status == 404
