# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — fleet nerve-center cockpit regressions

from __future__ import annotations

import hashlib
from urllib.request import Request, urlopen

import pytest

from dashboard_helpers import _feeds_server
from hub_e2e_helpers import http_get, running_hub
from synapse_channel.core.hub import SynapseHub
from synapse_channel.dashboard import start_dashboard_server
from synapse_channel.dashboard_cockpit import (
    COCKPIT_ASSETS,
    STUDIO_FONT_ASSETS,
    load_cockpit_asset,
    load_cockpit_asset_bytes,
    render_cockpit_html,
)

# ---------- assets ----------


def test_cockpit_assets_mapping() -> None:
    assert COCKPIT_ASSETS == {
        "board-columns.css": "text/css",
        "board-columns.js": "text/javascript",
        "cockpit.css": "text/css",
        "cockpit.js": "text/javascript",
        "risk-panel.css": "text/css",
        "risk-panel.js": "text/javascript",
        "studio-command.css": "text/css",
        "studio-command.js": "text/javascript",
        "studio-access.js": "text/javascript",
        "studio.css": "text/css",
        "studio-fonts.css": "text/css",
        "studio-feeds.js": "text/javascript",
        **{name: "font/woff2" for name in STUDIO_FONT_ASSETS},
    }


def test_load_cockpit_asset_returns_content() -> None:
    assert ":root" in load_cockpit_asset("cockpit.css")
    assert "SYNAPSE" in load_cockpit_asset("cockpit.js")


def test_cockpit_assets_render_the_risk_view() -> None:
    assert "renderRisk" in load_cockpit_asset("cockpit.js")
    assert "fetchReceipts" in load_cockpit_asset("cockpit.js")
    assert "receiptsUrl" in load_cockpit_asset("cockpit.js")
    assert "SynapseRiskPanel" in load_cockpit_asset("risk-panel.js")
    assert "/postmortem.json?task=" in load_cockpit_asset("risk-panel.js")
    assert ".risk__guidance-card" in load_cockpit_asset("risk-panel.css")


def test_load_cockpit_asset_rejects_unknown() -> None:
    with pytest.raises(KeyError):
        load_cockpit_asset("../secrets.css")
    with pytest.raises(KeyError):
        load_cockpit_asset("fonts/inter-latin.woff2")
    with pytest.raises(KeyError):
        load_cockpit_asset_bytes("../secrets.woff2")


def test_studio_font_assets_are_pinned_bounded_woff2() -> None:
    expected_hashes = {
        "fonts/inter-latin-ext.woff2": (
            "34b9c504cab7a73e37b746343a449132e56cf7b5481af2cb81dc74dcff25c956"
        ),
        "fonts/inter-latin.woff2": (
            "3100e775e8616cd2611beecfa23a4263d7037586789b43f035236a2e6fbd4c62"
        ),
        "fonts/jetbrains-mono-latin-ext.woff2": (
            "db5ff4db83e580426280e9337a58dc57d3a83784a1b03ad80914651594441d52"
        ),
        "fonts/jetbrains-mono-latin.woff2": (
            "83c005d49d8a6a50474c73a5a36ac0468076e9c4a29da7bdb14995d80560a5be"
        ),
        "fonts/space-grotesk-latin-ext.woff2": (
            "952dddb45d2f96f71cbf3b7f510b24379afc3c89ea02fcf89d377b45d62c0166"
        ),
        "fonts/space-grotesk-latin.woff2": (
            "0640890476fc1198ab4de571fb658de443c4d85b66466ec09534a8737ab1ce9d"
        ),
    }

    assert set(STUDIO_FONT_ASSETS) == set(expected_hashes)
    total_bytes = 0
    for asset_name, expected_hash in expected_hashes.items():
        body = load_cockpit_asset_bytes(asset_name)
        assert body.startswith(b"wOF2")
        assert len(body) < 100_000
        assert hashlib.sha256(body).hexdigest() == expected_hash
        total_bytes += len(body)
    assert total_bytes == 217_608


def test_dashboard_serves_a_binary_studio_font_with_the_exact_type() -> None:
    server = _feeds_server()
    try:
        request = Request(
            server.url("/fonts/inter-latin.woff2"),
            headers={"Authorization": f"Bearer {server.dashboard_token}"},
        )
        with urlopen(request, timeout=3) as response:  # nosec B310
            status = response.status
            content_type = response.headers.get_content_type()
            body = response.read()
    finally:
        server.close()

    assert status == 200
    assert content_type == "font/woff2"
    assert body == load_cockpit_asset_bytes("fonts/inter-latin.woff2")


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
        'href="risk-panel.css"',
        'src="risk-panel.js"',
        'src="cockpit.js"',
        "refreshSeconds: 5",
        'receiptsUrl: "receipts.json"',
        "<noscript>",
        "FALLBACK-MARKER",
    ):
        assert needle in html, needle
    assert html.index('src="risk-panel.js"') < html.index('src="cockpit.js"')


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
        bearer = f"Bearer {server.dashboard_token}"
        try:
            css_status, css_headers, css_body = await http_get(
                base, "/cockpit.css", authorization=bearer
            )
            js_status, js_headers, js_body = await http_get(
                base, "/cockpit.js", authorization=bearer
            )
            risk_css_status, _, risk_css_body = await http_get(
                base, "/risk-panel.css", authorization=bearer
            )
            risk_js_status, _, risk_js_body = await http_get(
                base, "/risk-panel.js", authorization=bearer
            )
            missing_status, _, _ = await http_get(base, "/nope.png", authorization=bearer)
        finally:
            server.close()

    assert css_status == 200
    assert css_headers["Content-Type"].startswith("text/css")
    assert ":root" in css_body
    assert js_status == 200
    assert js_headers["Content-Type"].startswith("text/javascript")
    assert "fleet" in js_body
    assert risk_css_status == 200
    assert ".risk__guidance" in risk_css_body
    assert risk_js_status == 200
    assert "SynapseRiskPanel" in risk_js_body
    assert missing_status == 404
