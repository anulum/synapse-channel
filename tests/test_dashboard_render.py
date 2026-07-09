# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — dashboard render + cockpit-dist route tests

"""Tests for dashboard HTML rendering, cockpit static distribution, and agent roles."""

from __future__ import annotations

import json
from pathlib import Path

from websockets.asyncio.client import connect

from dashboard_helpers import _feeds_server, _http_get
from hub_e2e_helpers import read_until_type, running_hub
from synapse_channel.core.hub import SynapseHub
from synapse_channel.dashboard import (
    DashboardSnapshot,
    _agent_roles_from_who,
    fetch_dashboard_snapshot,
)
from synapse_channel.dashboard_render import render_dashboard_html


def test_dashboard_html_escapes_snapshot_content() -> None:
    snapshot = DashboardSnapshot(
        online_agents=["A<script>"],
        state={
            "active_claims": [{"task_id": "T", "owner": "A<script>", "paths": ["src/<bad>.py"]}]
        },
        board={
            "tasks": [{"task_id": "T", "title": "<script>alert(1)</script>", "status": "open"}],
            "ready": ["T"],
            "progress": [{"author": "A", "kind": "note", "task_id": "T", "text": "<ok>"}],
        },
        manifest=[
            {
                "agent": "A<script>",
                "task_classes": ["chat"],
                "description": "<desc>",
                "contracts": [{"task_class": "<script>"}],
            }
        ],
    )

    html = render_dashboard_html(snapshot, refresh_seconds=5)

    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "src/&lt;bad&gt;.py" in html
    assert "contracts: 1" in html
    assert "refreshSeconds: 5" in html  # live JS polling replaces the full-page meta refresh


def test_dashboard_html_renders_empty_and_malformed_sections() -> None:
    snapshot = DashboardSnapshot(
        online_agents=[],
        state={"active_claims": "not-a-list"},
        board={
            "tasks": [],
            "ready": "not-a-list",
            "progress": [],
        },
        manifest=[],
    )

    html = render_dashboard_html(snapshot, refresh_seconds=0)

    assert "refreshSeconds: 1" in html  # zero coerced to a one-second live poll
    assert "No board tasks" in html
    assert "No progress notes" in html
    assert "No active claims" in html
    assert "No advertised capabilities" in html


def test_dashboard_html_ignores_malformed_task_and_progress_rows() -> None:
    snapshot = DashboardSnapshot(
        online_agents=[],
        state={"active_claims": []},
        board={
            "tasks": [object()],
            "ready": [],
            "progress": [object()],
        },
        manifest=[],
    )

    html = render_dashboard_html(snapshot, refresh_seconds=5)

    assert "No board tasks" in html
    assert "No progress notes" in html


def test_cockpit_dist_serves_index_assets_and_refuses_escapes(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<title>cockpit</title>", encoding="utf-8")
    (dist / "app.js").write_text("console.log('ok')", encoding="utf-8")
    (dist / "tool.exe").write_bytes(b"MZ")
    (tmp_path / "secret.txt").write_text("outside", encoding="utf-8")

    server = _feeds_server(cockpit_dist=dist)
    try:
        index_status, index_type, index_body = _http_get(server.url("/cockpit/"))
        bare_status, _, _ = _http_get(server.url("/cockpit"))
        js_status, js_type, _ = _http_get(server.url("/cockpit/app.js"))
        escape_status, _, _ = _http_get(server.url("/cockpit/../secret.txt"))
        suffix_status, _, _ = _http_get(server.url("/cockpit/tool.exe"))
        missing_status, _, _ = _http_get(server.url("/cockpit/nope.css"))
    finally:
        server.close()

    assert (index_status, index_type) == (200, "text/html")
    assert "cockpit" in index_body
    assert bare_status == 200
    assert (js_status, js_type) == (200, "text/javascript")
    assert escape_status == 404
    assert suffix_status == 404
    assert missing_status == 404


def test_cockpit_dist_reports_absence_when_unconfigured() -> None:
    server = _feeds_server()
    try:
        status, _, body = _http_get(server.url("/cockpit/"))
    finally:
        server.close()

    assert status == 404
    assert "--cockpit-dist" in body


def test_cockpit_dist_serves_pwa_manifest_and_service_worker(tmp_path: Path) -> None:
    """A PWA cockpit needs its manifest served as application/manifest+json.

    The service worker is a plain .js served at the /cockpit/ scope (already
    supported); the manifest suffix is the one the whitelist was missing, so an
    installable cockpit is refused without it.
    """
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<title>cockpit</title>", encoding="utf-8")
    (dist / "manifest.webmanifest").write_text('{"name":"SYNAPSE Cockpit"}', encoding="utf-8")
    (dist / "sw.js").write_text("self.addEventListener('install', () => {})", encoding="utf-8")

    server = _feeds_server(cockpit_dist=dist)
    try:
        man_status, man_type, man_body = _http_get(server.url("/cockpit/manifest.webmanifest"))
        sw_status, sw_type, _ = _http_get(server.url("/cockpit/sw.js"))
    finally:
        server.close()

    assert (man_status, man_type) == (200, "application/manifest+json")
    assert "SYNAPSE Cockpit" in man_body
    assert (sw_status, sw_type) == (200, "text/javascript")  # SW at /cockpit/ scope


def test_agent_roles_from_who_coerces_and_drops_malformed_bindings() -> None:
    who = {
        "agent_roles": {
            "proj/claude": ["proj/coordinator", "proj/git"],
            "proj/bob": "not-a-list",  # dropped: a binding must be a list
            123: [456],  # name and role are string-coerced
        }
    }
    assert _agent_roles_from_who(who) == {
        "proj/claude": ["proj/coordinator", "proj/git"],
        "123": ["456"],
    }


def test_agent_roles_from_who_is_empty_for_a_missing_or_non_mapping_field() -> None:
    assert _agent_roles_from_who({}) == {}
    assert _agent_roles_from_who({"agent_roles": ["not", "a", "mapping"]}) == {}


def test_dashboard_snapshot_to_dict_carries_agent_roles_top_level() -> None:
    snapshot = DashboardSnapshot(
        online_agents=["proj/claude"],
        state={},
        board={},
        manifest=[],
        agent_roles={"proj/claude": ["proj/coordinator"]},
    )
    payload = snapshot.to_dict()
    # top-level, next to online_agents, so the cockpit can join roster to roles
    assert payload["agent_roles"] == {"proj/claude": ["proj/coordinator"]}
    assert "proj/claude" in payload["online_agents"]


async def test_fetch_dashboard_snapshot_carries_agent_roles_from_who() -> None:
    # A role-declaring agent's binding reaches the dashboard snapshot (and its JSON
    # document) via the who snapshot, so the cockpit can show role chips.
    async with running_hub(SynapseHub()) as (_hub, uri):
        async with connect(uri) as holder_ws:
            await read_until_type(holder_ws, "welcome")
            await holder_ws.send(
                json.dumps(
                    {
                        "sender": "proj/holder",
                        "type": "heartbeat",
                        "target": "System",
                        "payload": "online",
                        "roles": ["proj/coordinator"],
                    }
                )
            )
            # confirm the binding is live before querying, so there is no race
            await holder_ws.send(json.dumps({"sender": "proj/holder", "type": "who_request"}))
            who = await read_until_type(holder_ws, "who_snapshot")
            assert who["agent_roles"]["proj/holder"] == ["proj/coordinator"]
            snapshot = await fetch_dashboard_snapshot(
                uri=uri,
                name="SYNAPSE-CHANNEL/dashboard",
                token=None,
                ready_timeout=1.0,
                response_timeout=1.0,
            )
    assert snapshot.agent_roles["proj/holder"] == ["proj/coordinator"]
    assert snapshot.to_dict()["agent_roles"]["proj/holder"] == ["proj/coordinator"]


def test_dashboard_html_renders_observed_peer_rows() -> None:
    """Reachable and unreachable observed peers render as escaped advisory rows."""
    from synapse_channel.core.journal import EventKind
    from synapse_channel.core.multihub_fold import fold_observed_state
    from synapse_channel.core.multihub_merge import HubEvent
    from synapse_channel.observed_peers import ObservedPeerSnapshot

    reachable = ObservedPeerSnapshot(
        hub_id="east",
        uri="ws://east",
        reachable=True,
        cursor=4,
        log_end_seq=6,
        state=fold_observed_state(
            [
                HubEvent(
                    "east",
                    4,
                    4.0,
                    EventKind.CLAIM,
                    {"task_id": "REMOTE", "owner": "east/agent", "paths": ["src/x.py"]},
                )
            ]
        ),
    )
    unreachable = ObservedPeerSnapshot(
        hub_id="west",
        uri="ws://west",
        reachable=False,
        error="connection <refused>",
    )
    silent = ObservedPeerSnapshot(hub_id="quiet", uri="ws://quiet", reachable=True)
    snapshot = DashboardSnapshot(
        online_agents=[],
        state={},
        board={},
        manifest=[],
        observed_peers=(reachable, unreachable, silent),
    )

    page = render_dashboard_html(snapshot, refresh_seconds=5)

    assert "observed@east" in page
    assert "cursor=4 lag=2 claims=1" in page
    assert "east/agent" in page
    assert "observed@west" in page
    assert "unreachable" in page
    assert "connection &lt;refused&gt;" in page
    assert "observed@quiet" in page
    assert "lag=unknown" in page
    assert "no observed claim owners" in page
