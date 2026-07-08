# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — dashboard HTTP surface tests

"""Tests for the dashboard HTTP surface (snapshot, HTML, JSON, headers, docs)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import cast
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

import synapse_channel.dashboard as dashboard_module
from dashboard_helpers import _http_get
from hub_e2e_helpers import AgentHandle, close_agents, connect_agent, running_hub
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.hub_config import HubConfig, HubLimits, config_fingerprint
from synapse_channel.dashboard import (
    _DashboardHandler,
    fetch_dashboard_snapshot,
    start_dashboard_server,
)


async def _prepare_dashboard_hub(uri: str) -> AgentHandle:
    handle = await connect_agent("SYNAPSE-CHANNEL/demo", uri)
    await handle.agent.advertise(
        description="demo worker",
        task_classes=["chat"],
        model="local",
        contracts=[
            {
                "task_class": "chat",
                "input_schema": {"type": "object"},
                "output_schema": {"type": "string"},
            }
        ],
    )
    await handle.recorder.wait_for(
        lambda message: (
            message.get("type") == "capability_advertised"
            and message.get("card", {}).get("agent") == "SYNAPSE-CHANNEL/demo"
        )
    )
    await handle.agent.post_task("TASK-1", title="Dashboard task")
    await handle.recorder.wait_for(
        lambda message: (
            message.get("type") == "ledger_task_posted"
            and message.get("task", {}).get("task_id") == "TASK-1"
        )
    )
    await handle.agent.post_progress("TASK-1", "visible", kind="note")
    await handle.recorder.wait_for(
        lambda message: (
            message.get("type") == "ledger_progress_posted"
            and message.get("note", {}).get("task_id") == "TASK-1"
        )
    )
    await handle.agent.claim("TASK-1", paths=["src/synapse_channel/dashboard.py"])
    await handle.recorder.wait_for(
        lambda message: (
            message.get("type") == "claim_granted" and message.get("task_id") == "TASK-1"
        )
    )
    return handle


async def test_dashboard_snapshot_fetches_real_hub_state() -> None:
    async with running_hub(SynapseHub()) as (_hub, uri):
        handle = await _prepare_dashboard_hub(uri)
        try:
            snapshot = await fetch_dashboard_snapshot(
                uri=uri,
                name="SYNAPSE-CHANNEL/dashboard",
                token=None,
                ready_timeout=1.0,
                response_timeout=1.0,
            )
        finally:
            await close_agents(handle)

    assert "SYNAPSE-CHANNEL/demo" in snapshot.online_agents
    assert snapshot.board["tasks"][0]["task_id"] == "TASK-1"
    assert snapshot.state["active_claims"][0]["owner"] == "SYNAPSE-CHANNEL/demo"
    assert snapshot.manifest[0]["agent"] == "SYNAPSE-CHANNEL/demo"


async def test_dashboard_snapshot_carries_the_hub_pinning_tag() -> None:
    from synapse_channel import __version__

    config = HubConfig(limits=HubLimits(max_clients=9))
    async with running_hub(SynapseHub.from_config(config)) as (_hub, uri):
        snapshot = await fetch_dashboard_snapshot(
            uri=uri,
            name="SYNAPSE-CHANNEL/dashboard",
            token=None,
            ready_timeout=1.0,
            response_timeout=1.0,
        )

    assert snapshot.hub_version == __version__
    assert snapshot.config_epoch == config_fingerprint(config)
    # The pinning tag reaches /snapshot.json unchanged.
    payload = snapshot.to_dict()
    assert payload["hub_version"] == __version__
    assert payload["config_epoch"] == config_fingerprint(config)


async def test_dashboard_snapshot_reports_missing_hub_responses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SilentAgent:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            self.running = True

        async def connect(self) -> None:
            while self.running:
                await asyncio.sleep(1)

        async def wait_until_ready(self, *, timeout: float) -> bool:
            return True

        async def request_who(self) -> None:
            return None

        async def request_state(self) -> None:
            return None

        async def request_board(self) -> None:
            return None

        async def request_manifest(self) -> None:
            return None

    monkeypatch.setattr(dashboard_module, "SynapseAgent", SilentAgent)

    with pytest.raises(dashboard_module.DashboardUnavailable, match="hub did not return"):
        await fetch_dashboard_snapshot(
            uri="ws://127.0.0.1:1",
            name="SYNAPSE-CHANNEL/dashboard",
            token=None,
            ready_timeout=0.01,
            response_timeout=0.01,
        )


async def test_dashboard_http_server_serves_real_html_and_json() -> None:
    async with running_hub(SynapseHub()) as (_hub, uri):
        handle = await _prepare_dashboard_hub(uri)
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
        try:
            html_status, html_type, html_body = await asyncio.to_thread(_http_get, server.url("/"))
            json_status, json_type, json_body = await asyncio.to_thread(
                _http_get, server.url("/snapshot.json")
            )
        finally:
            server.close()
            await close_agents(handle)

    assert html_status == 200
    assert html_type == "text/html"
    assert "Dashboard task" in html_body
    assert json_status == 200
    assert json_type == "application/json"
    payload = json.loads(json_body)
    assert payload["board"]["tasks"][0]["task_id"] == "TASK-1"
    assert payload["manifest"][0]["contracts"][0]["task_class"] == "chat"


async def test_dashboard_http_server_serves_the_studio_reference_and_css() -> None:
    async with running_hub(SynapseHub()) as (_hub, uri):
        handle = await _prepare_dashboard_hub(uri)
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
        try:
            studio_status, studio_type, studio_body = await asyncio.to_thread(
                _http_get, server.url("/studio")
            )
            css_status, css_type, css_body = await asyncio.to_thread(
                _http_get, server.url("/studio.css")
            )
            command_status, command_type, command_body = await asyncio.to_thread(
                _http_get, server.url("/studio/command")
            )
        finally:
            server.close()
            await close_agents(handle)

    assert studio_status == 200
    assert studio_type == "text/html"
    assert "syn-verdict" in studio_body
    assert css_status == 200
    assert css_type == "text/css"
    assert "--syn-brand" in css_body
    # the live command centre serves its hub-independent shell, wired to /studio.json
    assert command_status == 200
    assert command_type == "text/html"
    assert "Coordination clock" in command_body
    assert "/studio.json" in command_body
    assert 'href="/studio.css"' in command_body  # absolute, so it resolves from the subpath


async def test_dashboard_http_server_requires_dashboard_bearer_token() -> None:
    async with running_hub(SynapseHub()) as (_hub, uri):
        handle = await _prepare_dashboard_hub(uri)
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
            dashboard_token="viewer",
        )
        try:
            missing_status, missing_type, missing_body = await asyncio.to_thread(
                _http_get, server.url("/")
            )
            wrong_status, _, _ = await asyncio.to_thread(
                _http_get, server.url("/snapshot.json"), authorization="Bearer wrong"
            )
            ok_status, ok_type, ok_body = await asyncio.to_thread(
                _http_get, server.url("/snapshot.json"), authorization="Bearer viewer"
            )
        finally:
            server.close()
            await close_agents(handle)

    assert server.dashboard_token == "viewer"
    assert missing_status == 401
    assert missing_type == "text/plain"
    assert missing_body == "dashboard authorization required\n"
    assert wrong_status == 401
    assert ok_status == 200
    assert ok_type == "application/json"
    assert json.loads(ok_body)["board"]["tasks"][0]["task_id"] == "TASK-1"


async def test_dashboard_http_server_reports_unavailable_hub() -> None:
    server = start_dashboard_server(
        host="127.0.0.1",
        port=0,
        uri="ws://127.0.0.1:1",
        name="SYNAPSE-CHANNEL/dashboard",
        token=None,
        ready_timeout=0.01,
        response_timeout=0.01,
        refresh_seconds=5,
        allow_non_loopback=False,
    )
    try:
        status, content_type, body = _http_get(server.url("/"))
    finally:
        server.close()

    assert status == 503
    assert content_type == "text/plain"
    assert "could not reach hub" in body


async def test_dashboard_http_server_rejects_unknown_paths() -> None:
    server = start_dashboard_server(
        host="127.0.0.1",
        port=0,
        uri="ws://127.0.0.1:1",
        name="SYNAPSE-CHANNEL/dashboard",
        token=None,
        ready_timeout=0.01,
        response_timeout=0.01,
        refresh_seconds=5,
        allow_non_loopback=False,
    )
    try:
        status, content_type, body = _http_get(server.url("/missing"))
    finally:
        server.close()

    assert status == 404
    assert content_type == "text/plain"
    assert body == "not found\n"


def _response_headers(url: str) -> dict[str, str]:
    """Return the response headers for ``url``, whatever the HTTP status."""
    try:
        with urlopen(Request(url, headers={"Connection": "close"}), timeout=3) as response:  # nosec B310
            return {k: v for k, v in response.headers.items()}
    except HTTPError as exc:
        return {k: v for k, v in exc.headers.items()}


def test_dashboard_responses_carry_browser_hardening_headers() -> None:
    # The 404 path runs through _write without reaching a hub, so it exercises the
    # header set on every response cheaply.
    server = start_dashboard_server(
        host="127.0.0.1",
        port=0,
        uri="ws://127.0.0.1:1",
        name="SYNAPSE-CHANNEL/dashboard",
        token=None,
        ready_timeout=0.01,
        response_timeout=0.01,
        refresh_seconds=5,
        allow_non_loopback=False,
    )
    try:
        headers = _response_headers(server.url("/missing"))
    finally:
        server.close()

    assert headers.get("X-Content-Type-Options") == "nosniff"
    assert headers.get("Referrer-Policy") == "no-referrer"
    assert headers.get("X-Frame-Options") == "DENY"
    csp = headers.get("Content-Security-Policy", "")
    assert "frame-ancestors 'none'" in csp
    assert "base-uri 'none'" in csp
    assert "object-src 'none'" in csp
    assert "default-src 'self'" in csp


def test_dashboard_public_docs_describe_local_readonly_surface() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")
    cli_docs = Path("docs/cli.md").read_text(encoding="utf-8")
    changelog = Path("CHANGELOG.md").read_text(encoding="utf-8")

    for document in (readme, cli_docs, changelog):
        assert "synapse dashboard" in document

    assert "loopback" in cli_docs
    assert "/snapshot.json" in cli_docs
    assert "--allow-non-loopback" in cli_docs
    assert "--dashboard-token" in cli_docs
    assert "Authorization: Bearer" in cli_docs
    assert "fleet" in cli_docs
    assert "task-dependency graph" in cli_docs
    assert "branch-conflict candidates" in cli_docs
    assert "not run git" in cli_docs
    assert "--a2a-state-file" in cli_docs


def test_dashboard_handler_authorizes_everything_without_a_token() -> None:
    """With no configured bearer token the loopback surface stays open."""
    import types

    from synapse_channel.dashboard import _DashboardHandler

    stub = cast("_DashboardHandler", types.SimpleNamespace(dashboard_token=None, headers={}))
    assert _DashboardHandler._authorized(stub) is True
    bearer = cast(
        "_DashboardHandler",
        types.SimpleNamespace(dashboard_token="secret", headers={"Authorization": "Bearer secret"}),
    )
    assert _DashboardHandler._authorized(bearer) is True
    wrong = cast("_DashboardHandler", types.SimpleNamespace(dashboard_token="secret", headers={}))
    assert _DashboardHandler._authorized(wrong) is False
