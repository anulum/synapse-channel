# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — read-only dashboard CLI and HTTP surface tests

from __future__ import annotations

import asyncio
import json
import socket
from pathlib import Path
from typing import cast
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

import synapse_channel.dashboard as dashboard_module
from hub_e2e_helpers import AgentHandle, close_agents, connect_agent, running_hub
from synapse_channel import cli
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.hub_config import HubConfig, HubLimits, config_fingerprint
from synapse_channel.core.journal import EventKind, record_ledger_task
from synapse_channel.core.ledger import LedgerTask
from synapse_channel.core.merkle import proof_from_json, verify_inclusion
from synapse_channel.core.persistence import EventStore
from synapse_channel.dashboard import (
    DashboardServer,
    DashboardSnapshot,
    fetch_dashboard_snapshot,
    render_dashboard_html,
    start_dashboard_server,
    validate_dashboard_bind,
)
from synapse_channel.dashboard_operator import (
    ACCEPTED,
    DELIVERED,
    DENIED,
    REJECTED,
    UNDELIVERED,
    UNREACHABLE,
    RelayOutcome,
)
from synapse_channel.participants.session_metric_note import (
    SESSION_METRIC_NOTE_KIND,
    format_session_metric_note,
)
from synapse_channel.participants.session_telemetry import SessionMetrics


def _http_get(url: str, *, authorization: str | None = None) -> tuple[int, str, str]:
    headers = {"Connection": "close"}
    if authorization is not None:
        headers["Authorization"] = authorization
    request = Request(url, headers=headers)
    try:
        with urlopen(request, timeout=3) as response:  # nosec B310
            return (
                response.status,
                response.headers.get_content_type(),
                response.read().decode("utf-8"),
            )
    except HTTPError as exc:
        return exc.code, exc.headers.get_content_type(), exc.read().decode("utf-8")


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


def test_dashboard_parser_wires_command() -> None:
    args = cli.build_parser().parse_args(
        [
            "dashboard",
            "--host",
            "127.0.0.1",
            "--port",
            "0",
            "--refresh-seconds",
            "7",
            "--dashboard-token",
            "viewer",
        ]
    )

    assert args.command == "dashboard"
    assert args.host == "127.0.0.1"
    assert args.port == 0
    assert args.refresh_seconds == 7
    assert args.dashboard_token == "viewer"


def test_dashboard_refuses_non_loopback_without_override() -> None:
    with pytest.raises(ValueError, match="loopback"):
        validate_dashboard_bind("0.0.0.0", allow_non_loopback=False)  # nosec B104
    with pytest.raises(ValueError, match="loopback"):
        validate_dashboard_bind("dashboard.example.invalid", allow_non_loopback=False)

    validate_dashboard_bind("0.0.0.0", allow_non_loopback=True)  # nosec B104


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


def test_dashboard_non_loopback_gets_generated_dashboard_token() -> None:
    server = start_dashboard_server(
        host="0.0.0.0",  # nosec B104
        port=0,
        uri="ws://127.0.0.1:1",
        name="SYNAPSE-CHANNEL/dashboard",
        token=None,
        ready_timeout=0.01,
        response_timeout=0.01,
        refresh_seconds=5,
        allow_non_loopback=True,
    )
    try:
        assert server.dashboard_token is not None
        assert len(server.dashboard_token) >= 32
        status, content_type, body = _http_get(server.url("/missing"))
    finally:
        server.close()

    assert status == 401
    assert content_type == "text/plain"
    assert body == "dashboard authorization required\n"


def test_dashboard_rejects_empty_dashboard_token() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        start_dashboard_server(
            host="127.0.0.1",
            port=0,
            uri="ws://127.0.0.1:1",
            name="SYNAPSE-CHANNEL/dashboard",
            token=None,
            ready_timeout=0.01,
            response_timeout=0.01,
            refresh_seconds=5,
            allow_non_loopback=False,
            dashboard_token="",
        )


def test_dashboard_http_server_reports_unavailable_hub() -> None:
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


def test_dashboard_http_server_rejects_unknown_paths() -> None:
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


# --- dispatcher ---------------------------------------------------------------


class _FakeDashboardServer:
    """A started-server stand-in recording the dispatcher's lifecycle calls."""

    def __init__(self, *, token: str | None, generated: bool) -> None:
        self.dashboard_token = token
        self.dashboard_token_generated = generated
        self.closed = False

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:8765{path}"

    def close(self) -> None:
        self.closed = True


def _dashboard_args(**overrides: object) -> object:
    argv = ["dashboard"]
    args = cli.build_parser().parse_args(argv)
    for key, value in overrides.items():
        setattr(args, key, value)
    return args


def _run_dispatcher(
    monkeypatch: pytest.MonkeyPatch,
    server: _FakeDashboardServer,
) -> int:
    from synapse_channel import cli_dashboard

    monkeypatch.setattr(cli_dashboard, "start_dashboard_server", lambda **_: server)

    def interrupt(_: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("synapse_channel.cli_dashboard.time.sleep", interrupt)
    args = _dashboard_args()
    handler = args.func  # type: ignore[attr-defined]
    return int(handler(args))


def test_cmd_dashboard_serves_until_interrupted_and_closes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The dispatcher prints the URLs, blocks, and closes the server on Ctrl-C."""
    server = _FakeDashboardServer(token=None, generated=False)
    assert _run_dispatcher(monkeypatch, server) == 0
    out = capsys.readouterr().out
    assert "dashboard: http://127.0.0.1:8765/" in out
    assert "snapshot JSON: http://127.0.0.1:8765/snapshot.json" in out
    assert "dashboard auth" not in out  # no token configured, nothing to announce
    assert server.closed


def test_cmd_dashboard_announces_a_generated_token(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A token the server generated must be shown once — the user cannot know it."""
    server = _FakeDashboardServer(token="generated-secret", generated=True)
    assert _run_dispatcher(monkeypatch, server) == 0
    out = capsys.readouterr().out
    assert "dashboard token: generated-secret" in out
    assert "Authorization: Bearer" in out
    assert server.closed


def test_cmd_dashboard_never_echoes_a_supplied_token(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An operator-supplied token is announced but not echoed back to the terminal."""
    server = _FakeDashboardServer(token="operator-secret", generated=False)
    assert _run_dispatcher(monkeypatch, server) == 0
    out = capsys.readouterr().out
    assert "operator-secret" not in out
    assert "Authorization: Bearer" in out


def test_cmd_dashboard_rejects_an_invalid_bind(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A bind the server refuses (ValueError) exits 2 with the reason printed."""
    from synapse_channel import cli_dashboard

    def refuse(**_: object) -> object:
        msg = "refusing non-loopback bind"
        raise ValueError(msg)

    monkeypatch.setattr(cli_dashboard, "start_dashboard_server", refuse)
    args = _dashboard_args()
    handler = args.func  # type: ignore[attr-defined]
    assert int(handler(args)) == 2
    assert "refusing non-loopback bind" in capsys.readouterr().out


def test_dashboard_url_brackets_an_ipv6_host() -> None:
    """An IPv6 bind renders a bracketed URL that a browser accepts."""
    server = _FakeDashboardServer(token=None, generated=False)
    del server  # the URL shaping lives on the real DashboardServer
    import types

    from synapse_channel.dashboard import DashboardServer

    shaped = cast("DashboardServer", types.SimpleNamespace(host="::1", port=8765))
    assert DashboardServer.url(shaped, "/snapshot.json") == "http://[::1]:8765/snapshot.json"
    plain = cast("DashboardServer", types.SimpleNamespace(host="127.0.0.1", port=8765))
    assert DashboardServer.url(plain, "/") == "http://127.0.0.1:8765/"


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


def _reliability_server(reliability_db: Path | None, **overrides: str) -> DashboardServer:
    """Start a dashboard against an unreachable hub — the reliability feed
    reads the durable store, so it must serve even when the hub is down."""
    return start_dashboard_server(
        host="127.0.0.1",
        port=0,
        uri="ws://127.0.0.1:1",
        name="SYNAPSE-CHANNEL/dashboard",
        token=None,
        ready_timeout=0.01,
        response_timeout=0.01,
        refresh_seconds=5,
        allow_non_loopback=False,
        reliability_db=reliability_db,
        dashboard_token=overrides.get("dashboard_token"),
    )


def test_dashboard_reliability_endpoint_reports_absence_without_a_store() -> None:
    server = _reliability_server(None)
    try:
        status, content_type, body = _http_get(server.url("/reliability.json"))
    finally:
        server.close()

    assert status == 404
    assert content_type == "text/plain"
    assert "--reliability-db" in body


def test_dashboard_reliability_endpoint_serves_the_report_with_the_hub_down(
    tmp_path: Path,
) -> None:
    db = tmp_path / "hub.db"
    store = EventStore(db)
    store.append(
        EventKind.CLAIM,
        {"task_id": "T", "owner": "alice", "status": "claimed", "paths": [], "worktree": "w"},
        ts=1.0,
    )
    store.close()

    server = _reliability_server(db)
    try:
        status, content_type, body = _http_get(server.url("/reliability.json"))
    finally:
        server.close()

    assert status == 200
    assert content_type == "application/json"
    payload = json.loads(body)
    assert payload["note"] == "audit signals, not scores"
    assert "owners" in payload and "findings" in payload


_MALFORMED_FEED_QUERIES = (
    "seq=abc",
    "seq=",
    "seq=-1",
    "seq=1.5",
    "seq=99999999999999999999999999",
    "seq=0x10",
    "seq=%00",
    "seq[]=1",
    "seq=1&seq=2",
    "task=",
    "task=%ff%fe",
    "direction=sideways",
    "direction=",
    "limit=-5",
    "limit=abc",
    "limit=999999999999",
    "=noname",
    "&&&",
    "seq=" + "9" * 4096,
    "task=" + "A" * 8192,
)
"""Hostile query strings thrown at every store feed by the fuzz test below."""


def test_dashboard_feed_queries_never_crash_on_malformed_input(tmp_path: Path) -> None:
    # The store feeds parse an untrusted query string (?seq=, ?task=, ?direction=,
    # ?limit=). No input may crash the handler into a 500: every response must be a
    # deliberate status. This fuzzes each feed with hostile queries and asserts the
    # handler always answers 200 (parsed), 400 (rejected), 404 (absent anchor), or
    # 503 (store error) — never an unhandled 500.
    db = tmp_path / "hub.db"
    store = EventStore(db)
    store.append(
        EventKind.CLAIM,
        {
            "task_id": "T",
            "owner": "alice",
            "status": "claimed",
            "paths": [],
            "worktree": "w",
            "claimed_at": 1.0,
            "lease_expires_at": 61.0,
        },
        ts=1.0,
    )
    store.close()

    feeds = ("/state-at.json", "/merkle-proof.json", "/events.json", "/causality.json")
    server = _reliability_server(db)
    try:
        for feed in feeds:
            for raw_query in _MALFORMED_FEED_QUERIES:
                status, _, _ = _http_get(server.url(f"{feed}?{raw_query}"))
                assert status in {200, 400, 404, 503}, f"{feed}?{raw_query} -> {status}"
    finally:
        server.close()


def test_dashboard_reliability_endpoint_fails_visible_on_a_missing_store(
    tmp_path: Path,
) -> None:
    server = _reliability_server(tmp_path / "absent.db")
    try:
        status, content_type, body = _http_get(server.url("/reliability.json"))
    finally:
        server.close()

    assert status == 503
    assert content_type == "text/plain"
    assert "missing event store" in body


def test_dashboard_reliability_endpoint_requires_the_dashboard_token(
    tmp_path: Path,
) -> None:
    db = tmp_path / "hub.db"
    EventStore(db).close()

    server = _reliability_server(db, dashboard_token="secret")
    try:
        denied_status, _, _ = _http_get(server.url("/reliability.json"))
        allowed_status, _, allowed_body = _http_get(
            server.url("/reliability.json"), authorization="Bearer secret"
        )
    finally:
        server.close()

    assert denied_status == 401
    assert allowed_status == 200
    assert json.loads(allowed_body)["note"] == "audit signals, not scores"


def test_dashboard_parser_wires_the_reliability_store_flag() -> None:
    parser = cli.build_parser(command="dashboard")

    default = parser.parse_args(["dashboard"])
    assert default.reliability_db is None

    named = parser.parse_args(["dashboard", "--reliability-db", "./hub.db"])
    assert named.reliability_db == Path("./hub.db")


def test_cmd_dashboard_announces_the_reliability_url(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """With --reliability-db the dispatcher names the reliability endpoint."""
    from synapse_channel import cli_dashboard

    server = _FakeDashboardServer(token=None, generated=False)
    monkeypatch.setattr(cli_dashboard, "start_dashboard_server", lambda **_: server)

    def interrupt(_: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("synapse_channel.cli_dashboard.time.sleep", interrupt)
    args = _dashboard_args(reliability_db=Path("./hub.db"))
    handler = args.func  # type: ignore[attr-defined]

    assert int(handler(args)) == 0
    out = capsys.readouterr().out
    assert "reliability JSON: http://127.0.0.1:8765/reliability.json" in out


def _feeds_server(
    *,
    reliability_db: Path | None = None,
    federation_store: Path | None = None,
    cockpit_dist: Path | None = None,
    dashboard_token: str | None = None,
) -> DashboardServer:
    """Start a dashboard with store feeds against an unreachable hub."""
    return start_dashboard_server(
        host="127.0.0.1",
        port=0,
        uri="ws://127.0.0.1:1",
        name="SYNAPSE-CHANNEL/dashboard",
        token=None,
        dashboard_token=dashboard_token,
        ready_timeout=0.01,
        response_timeout=0.01,
        refresh_seconds=5,
        allow_non_loopback=False,
        reliability_db=reliability_db,
        federation_store=federation_store,
        cockpit_dist=cockpit_dist,
    )


def _seed_feed_store(db: Path) -> None:
    store = EventStore(db)
    store.append(
        EventKind.CLAIM,
        {"task_id": "T", "owner": "alice", "status": "claimed", "paths": [], "worktree": "w"},
        ts=1.0,
    )
    store.append(EventKind.RELEASE, {"task_id": "T"}, ts=2.0)
    store.close()


def test_events_feed_reports_absence_without_a_store() -> None:
    server = _feeds_server()
    try:
        status, _, body = _http_get(server.url("/events.json"))
    finally:
        server.close()

    assert status == 404
    assert "--feeds-db" in body


def test_events_feed_serves_the_tail_with_the_hub_down(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    _seed_feed_store(db)

    server = _feeds_server(reliability_db=db)
    try:
        status, content_type, body = _http_get(server.url("/events.json?since=1&limit=5"))
    finally:
        server.close()

    assert status == 200
    assert content_type == "application/json"
    payload = json.loads(body)
    assert [event["seq"] for event in payload["events"]] == [2]
    assert payload["events"][0]["ts"] == 2.0
    assert payload["next_cursor"] == 2


def test_events_feed_refuses_malformed_numbers(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    _seed_feed_store(db)

    server = _feeds_server(reliability_db=db)
    try:
        status, _, body = _http_get(server.url("/events.json?since=abc"))
    finally:
        server.close()

    assert status == 400
    assert "must be an integer or 'latest'" in body


def test_events_feed_fails_visible_on_a_missing_store(tmp_path: Path) -> None:
    server = _feeds_server(reliability_db=tmp_path / "absent.db")
    try:
        status, _, body = _http_get(server.url("/events.json"))
    finally:
        server.close()

    assert status == 503
    assert "missing event store" in body


def _seed_session_note(db: Path) -> None:
    store = EventStore(db)
    note = format_session_metric_note(
        SessionMetrics(
            turns=3,
            errors=0,
            abstentions=0,
            input_tokens=200,
            output_tokens=40,
            cost_usd=0.25,
            total_latency_seconds=4.0,
            max_rate_limit_utilisation=None,
            last_input_tokens=120,
        )
    )
    store.append(
        EventKind.LEDGER_PROGRESS,
        {"kind": SESSION_METRIC_NOTE_KIND, "text": note, "author": "alpha", "task_id": "s1"},
        ts=1.0,
    )
    store.close()


def test_sessions_feed_reports_absence_without_a_store() -> None:
    server = _feeds_server()
    try:
        status, _, body = _http_get(server.url("/sessions.json"))
    finally:
        server.close()

    assert status == 404
    assert "--feeds-db" in body


def test_sessions_feed_serves_the_report_with_the_hub_down(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    _seed_session_note(db)

    server = _feeds_server(reliability_db=db)
    try:
        status, content_type, body = _http_get(server.url("/sessions.json"))
    finally:
        server.close()

    assert status == 200
    assert content_type == "application/json"
    payload = json.loads(body)
    assert [record["agent"] for record in payload["sessions"]] == ["alpha"]
    assert payload["sessions"][0]["seq"] == 1  # the causality-join anchor
    assert payload["sessions"][0]["cost_usd"] == 0.25
    assert payload["totals"]["cost_usd"] == 0.25


def test_sessions_feed_is_honest_empty_on_a_log_without_notes(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    _seed_feed_store(db)  # claims and releases, no session_metric notes

    server = _feeds_server(reliability_db=db)
    try:
        status, _, body = _http_get(server.url("/sessions.json"))
    finally:
        server.close()

    assert status == 200
    payload = json.loads(body)
    assert payload["sessions"] == []
    assert payload["totals"]["sessions"] == 0


def test_sessions_feed_fails_visible_on_a_missing_store(tmp_path: Path) -> None:
    server = _feeds_server(reliability_db=tmp_path / "absent.db")
    try:
        status, _, body = _http_get(server.url("/sessions.json"))
    finally:
        server.close()

    assert status == 503
    assert "missing event store" in body


def _seed_waits_store(db: Path) -> None:
    store = EventStore(db)
    record_ledger_task(
        store,
        LedgerTask(task_id="PENDING", title="prereq", created_at=1.0, updated_at=1.0),
    )
    record_ledger_task(
        store,
        LedgerTask(
            task_id="BLOCKED",
            title="ship it",
            created_at=2.0,
            updated_at=2.0,
            depends_on=("PENDING",),
            suggested_owner="amy",
        ),
    )
    store.close()


def test_waits_feed_reports_absence_without_a_store() -> None:
    server = _feeds_server()
    try:
        status, _, body = _http_get(server.url("/waits.json"))
    finally:
        server.close()

    assert status == 404
    assert "--feeds-db" in body


def test_waits_feed_serves_the_gates_with_the_hub_down(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    _seed_waits_store(db)

    server = _feeds_server(reliability_db=db)
    try:
        status, content_type, body = _http_get(server.url("/waits.json"))
    finally:
        server.close()

    assert status == 200
    assert content_type == "application/json"
    payload = json.loads(body)
    assert payload["wait_count"] == 1
    gate = payload["waits"][0]
    assert gate["task_id"] == "BLOCKED"
    assert gate["who"] == "amy"
    assert gate["on_what"] == ["PENDING"]


def test_waits_feed_fails_visible_on_a_missing_store(tmp_path: Path) -> None:
    server = _feeds_server(reliability_db=tmp_path / "absent.db")
    try:
        status, _, body = _http_get(server.url("/waits.json"))
    finally:
        server.close()

    assert status == 503
    assert "missing event store" in body


def test_causality_feed_mirrors_the_cli_shape(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    _seed_feed_store(db)

    server = _feeds_server(reliability_db=db)
    try:
        by_seq_status, _, by_seq_body = _http_get(
            server.url("/causality.json?seq=1&direction=effects")
        )
        by_task_status, _, by_task_body = _http_get(
            server.url("/causality.json?task=T&direction=causes")
        )
    finally:
        server.close()

    assert (by_seq_status, by_task_status) == (200, 200)
    by_seq = json.loads(by_seq_body)
    assert by_seq["direction"] == "effects"
    assert by_seq["seq"] == 1
    assert by_seq["present"] is True
    by_task = json.loads(by_task_body)
    assert by_task["seq"] == 2  # the task's most recent event anchors the query


def test_causality_feed_maps_errors_to_honest_statuses(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    _seed_feed_store(db)

    server = _feeds_server(reliability_db=db)
    try:
        ghost_status, _, ghost_body = _http_get(server.url("/causality.json?task=GHOST"))
        bad_seq_status, _, _ = _http_get(server.url("/causality.json?seq=abc"))
        bad_direction_status, _, _ = _http_get(
            server.url("/causality.json?seq=1&direction=sideways")
        )
        unconfigured = _feeds_server()
        try:
            absent_status, _, _ = _http_get(unconfigured.url("/causality.json?seq=1"))
        finally:
            unconfigured.close()
    finally:
        server.close()

    assert ghost_status == 404
    assert "no recorded event for task" in ghost_body
    assert bad_seq_status == 400
    assert bad_direction_status == 400
    assert absent_status == 404


def test_federation_feed_serves_peerings_and_reports_absence(tmp_path: Path) -> None:
    from synapse_channel.core.federation import FederationPeer
    from synapse_channel.core.federation_store import (
        FederationRecord,
        PeerProvenance,
        save_store,
    )

    store = tmp_path / "federation.json"
    save_store(
        store,
        [
            FederationRecord(
                peer=FederationPeer(domain_id="atelier.example"),
                provenance=PeerProvenance(source="ws://a:1", imported_at=1.0, confirmed_by="ops"),
            )
        ],
    )

    configured = _feeds_server(federation_store=store)
    try:
        status, _, body = _http_get(configured.url("/federation.json"))
    finally:
        configured.close()
    unconfigured = _feeds_server()
    try:
        absent_status, _, absent_body = _http_get(unconfigured.url("/federation.json"))
    finally:
        unconfigured.close()

    assert status == 200
    payload = json.loads(body)
    assert payload["peerings"][0]["domain"] == "atelier.example"
    assert payload["peerings"][0]["state"] == "active"
    assert payload["namespaces"] == []
    assert absent_status == 404
    assert "--federation-store" in absent_body


def test_federation_feed_fails_visible_on_a_corrupt_store(tmp_path: Path) -> None:
    store = tmp_path / "federation.json"
    store.write_text("{not json", encoding="utf-8")

    server = _feeds_server(federation_store=store)
    try:
        status, _, _ = _http_get(server.url("/federation.json"))
    finally:
        server.close()

    assert status == 503


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


def test_feed_endpoints_sit_behind_the_dashboard_token(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    _seed_feed_store(db)

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
        reliability_db=db,
        dashboard_token="secret",
    )
    try:
        denied, _, _ = _http_get(server.url("/events.json"))
        allowed, _, _ = _http_get(server.url("/events.json"), authorization="Bearer secret")
    finally:
        server.close()

    assert denied == 401
    assert allowed == 200


def test_dashboard_parser_wires_the_feed_flags() -> None:
    parser = cli.build_parser(command="dashboard")

    default = parser.parse_args(["dashboard"])
    assert default.reliability_db is None
    assert default.federation_store is None
    assert default.cockpit_dist is None

    named = parser.parse_args(
        [
            "dashboard",
            "--feeds-db",
            "./hub.db",
            "--federation-store",
            "./federation.json",
            "--cockpit-dist",
            "./dist",
        ]
    )
    assert named.reliability_db == Path("./hub.db")
    assert named.federation_store == Path("./federation.json")
    assert named.cockpit_dist == Path("./dist")

    alias = parser.parse_args(["dashboard", "--reliability-db", "./hub.db"])
    assert alias.reliability_db == Path("./hub.db")


def test_cmd_dashboard_announces_every_configured_feed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from synapse_channel import cli_dashboard

    server = _FakeDashboardServer(token=None, generated=False)
    monkeypatch.setattr(cli_dashboard, "start_dashboard_server", lambda **_: server)

    def interrupt(_: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("synapse_channel.cli_dashboard.time.sleep", interrupt)
    args = _dashboard_args(
        reliability_db=Path("./hub.db"),
        federation_store=Path("./federation.json"),
        cockpit_dist=Path("./dist"),
    )
    handler = args.func  # type: ignore[attr-defined]

    assert int(handler(args)) == 0
    out = capsys.readouterr().out
    assert "events tail JSON: http://127.0.0.1:8765/events.json" in out
    assert "causality JSON: http://127.0.0.1:8765/causality.json" in out
    assert "federation JSON: http://127.0.0.1:8765/federation.json" in out
    assert "cockpit: http://127.0.0.1:8765/cockpit/" in out


def test_causality_feed_fails_visible_on_a_missing_store(tmp_path: Path) -> None:
    server = _feeds_server(reliability_db=tmp_path / "absent.db")
    try:
        status, _, body = _http_get(server.url("/causality.json?seq=1"))
    finally:
        server.close()

    assert status == 503
    assert "missing event store" in body


def test_events_feed_supports_the_latest_tail_shortcut(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    _seed_feed_store(db)

    server = _feeds_server(reliability_db=db)
    try:
        status, _, body = _http_get(server.url("/events.json?since=latest"))
    finally:
        server.close()

    assert status == 200
    payload = json.loads(body)
    assert payload["events"] == []  # caught up instantly, no history walk
    assert payload["next_cursor"] == 2  # the log's end, ready for the next poll


def test_metrics_feed_reports_absence_without_a_store() -> None:
    server = _feeds_server()
    try:
        status, _, body = _http_get(server.url("/metrics.json"))
    finally:
        server.close()

    assert status == 404
    assert "--feeds-db" in body


def test_metrics_feed_serves_log_metrics_with_the_hub_down(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    _seed_feed_store(db)

    server = _feeds_server(reliability_db=db)
    try:
        status, content_type, body = _http_get(server.url("/metrics.json"))
    finally:
        server.close()

    assert status == 200
    assert content_type == "application/json"
    payload = json.loads(body)
    assert payload["log"]["total_events"] == 2
    assert payload["events_by_kind"] == {"claim": 1, "release": 1}
    assert payload["windows"]["last_hour"]["events"] == 2
    assert "hub's own /metrics" in payload["note"]


def test_metrics_feed_fails_visible_on_a_missing_store(tmp_path: Path) -> None:
    server = _feeds_server(reliability_db=tmp_path / "absent.db")
    try:
        status, _, body = _http_get(server.url("/metrics.json"))
    finally:
        server.close()

    assert status == 503
    assert "missing event store" in body


def test_metrics_feed_is_behind_the_dashboard_token(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    _seed_feed_store(db)

    server = _feeds_server(reliability_db=db, dashboard_token="s3cret")
    try:
        denied, _, _ = _http_get(server.url("/metrics.json"))
        allowed, _, body = _http_get(server.url("/metrics.json"), authorization="Bearer s3cret")
    finally:
        server.close()

    assert denied == 401
    assert allowed == 200
    assert json.loads(body)["log"]["total_events"] == 2


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


def test_state_at_feed_reports_absence_without_a_store() -> None:
    server = _feeds_server()
    try:
        status, _, body = _http_get(server.url("/state-at.json?seq=1"))
    finally:
        server.close()
    assert status == 404
    assert "--feeds-db" in body


def _seed_replayable_store(db: Path) -> None:
    """A full claim payload (replayable) then a release — for state reconstruction."""
    store = EventStore(db)
    store.append(
        EventKind.CLAIM,
        {
            "task_id": "T",
            "owner": "alice",
            "note": "",
            "claimed_at": 1.0,
            "lease_expires_at": 1_000_000_000_000.0,
            "status": "claimed",
            "data_ref": "",
            "worktree": "w",
            "paths": [],
            "epoch": 1,
        },
        ts=1.0,
    )
    store.append(EventKind.RELEASE, {"task_id": "T"}, ts=2.0)
    store.close()


def test_state_at_feed_reconstructs_state_at_a_seq(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    _seed_replayable_store(db)  # a claim then a release (2 events)

    server = _feeds_server(reliability_db=db)
    try:
        status, content_type, body = _http_get(server.url("/state-at.json?seq=1"))
    finally:
        server.close()

    assert status == 200
    assert content_type == "application/json"
    payload = json.loads(body)
    assert payload["as_of_seq"] == 1
    assert payload["log_end_seq"] == 2
    assert [c["task_id"] for c in payload["state"]["active_claims"]] == [
        "T"
    ]  # claimed, not yet released
    assert "presence/roster is not journalled" in payload["note"]


def test_state_at_feed_refuses_a_malformed_seq(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    _seed_feed_store(db)
    server = _feeds_server(reliability_db=db)
    try:
        status, _, body = _http_get(server.url("/state-at.json?seq=abc"))
    finally:
        server.close()
    assert status == 400
    assert "seq must be an integer" in body


def test_state_at_feed_fails_visible_on_a_missing_store(tmp_path: Path) -> None:
    server = _feeds_server(reliability_db=tmp_path / "absent.db")
    try:
        status, _, body = _http_get(server.url("/state-at.json?seq=1"))
    finally:
        server.close()
    assert status == 503
    assert "missing event store" in body


def test_state_at_feed_is_behind_the_dashboard_token(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    _seed_replayable_store(db)
    server = _feeds_server(reliability_db=db, dashboard_token="s3cret")
    try:
        denied, _, _ = _http_get(server.url("/state-at.json?seq=1"))
        allowed, _, _ = _http_get(server.url("/state-at.json?seq=1"), authorization="Bearer s3cret")
    finally:
        server.close()
    assert denied == 401
    assert allowed == 200


def test_merkle_proof_feed_reports_absence_without_a_store() -> None:
    server = _feeds_server()
    try:
        status, _, body = _http_get(server.url("/merkle-proof.json?seq=1"))
    finally:
        server.close()
    assert status == 404
    assert "--feeds-db" in body


def test_merkle_proof_feed_proves_inclusion_with_the_hub_down(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    _seed_feed_store(db)  # two events; merkle hashes leaves, no replay needed

    server = _feeds_server(reliability_db=db)
    try:
        status, content_type, body = _http_get(server.url("/merkle-proof.json?seq=1"))
    finally:
        server.close()

    assert status == 200
    assert content_type == "application/json"
    payload = json.loads(body)
    assert payload["present"] is True
    assert payload["seq"] == 1
    assert payload["tree_size"] == 2
    # The served proof verifies through the same client-side check the cockpit's
    # per-row verify button runs — the row is committed to the attested root.
    assert verify_inclusion(proof_from_json(payload)) is True


def test_merkle_proof_feed_reports_an_absent_seq_without_fabricating(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    _seed_feed_store(db)  # only seq 1..2 exist
    server = _feeds_server(reliability_db=db)
    try:
        status, _, body = _http_get(server.url("/merkle-proof.json?seq=99"))
    finally:
        server.close()
    assert status == 200
    payload = json.loads(body)
    assert payload["present"] is False
    assert "no event at that sequence" in payload["note"]


def test_merkle_proof_feed_refuses_a_malformed_seq(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    _seed_feed_store(db)
    server = _feeds_server(reliability_db=db)
    try:
        status, _, body = _http_get(server.url("/merkle-proof.json?seq=abc"))
    finally:
        server.close()
    assert status == 400
    assert "seq must be an integer" in body


def test_merkle_proof_feed_fails_visible_on_a_missing_store(tmp_path: Path) -> None:
    server = _feeds_server(reliability_db=tmp_path / "absent.db")
    try:
        status, _, body = _http_get(server.url("/merkle-proof.json?seq=1"))
    finally:
        server.close()
    assert status == 503
    assert "missing event store" in body


def test_merkle_proof_feed_is_behind_the_dashboard_token(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    _seed_feed_store(db)
    server = _feeds_server(reliability_db=db, dashboard_token="s3cret")
    try:
        denied, _, _ = _http_get(server.url("/merkle-proof.json?seq=1"))
        allowed, _, _ = _http_get(
            server.url("/merkle-proof.json?seq=1"), authorization="Bearer s3cret"
        )
    finally:
        server.close()
    assert denied == 401
    assert allowed == 200


def test_health_anomalies_feed_reports_absence_without_a_store() -> None:
    server = _feeds_server()
    try:
        status, _, body = _http_get(server.url("/health-anomalies.json"))
    finally:
        server.close()
    assert status == 404
    assert "--feeds-db" in body


def test_health_anomalies_feed_flags_anomalies_with_the_hub_down(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    store = EventStore(db)
    store.append(
        EventKind.CLAIM,
        {"task_id": "X", "owner": "bob", "status": "claimed", "paths": ["s"], "worktree": "w"},
        ts=1.0,
    )  # a claim that is its task's last event — orphaned
    store.close()

    server = _feeds_server(reliability_db=db)
    try:
        status, content_type, body = _http_get(server.url("/health-anomalies.json"))
    finally:
        server.close()

    assert status == 200
    assert content_type == "application/json"
    payload = json.loads(body)
    assert payload["present"] is True
    assert payload["anomaly_count"] >= 1
    assert [item["task_id"] for item in payload["orphaned"]] == ["X"]


def test_health_anomalies_feed_fails_visible_on_a_missing_store(tmp_path: Path) -> None:
    server = _feeds_server(reliability_db=tmp_path / "absent.db")
    try:
        status, _, body = _http_get(server.url("/health-anomalies.json"))
    finally:
        server.close()
    assert status == 503
    assert "missing event store" in body


def test_health_anomalies_feed_is_behind_the_dashboard_token(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    _seed_feed_store(db)
    server = _feeds_server(reliability_db=db, dashboard_token="s3cret")
    try:
        denied, _, _ = _http_get(server.url("/health-anomalies.json"))
        allowed, _, _ = _http_get(
            server.url("/health-anomalies.json"), authorization="Bearer s3cret"
        )
    finally:
        server.close()
    assert denied == 401
    assert allowed == 200


def _http_post(
    url: str,
    body: bytes | str,
    *,
    authorization: str | None = None,
    content_type: str = "application/json",
) -> tuple[int, str, str]:
    headers = {"Connection": "close", "Content-Type": content_type}
    if authorization is not None:
        headers["Authorization"] = authorization
    data = body.encode("utf-8") if isinstance(body, str) else body
    request = Request(url, data=data, headers=headers, method="POST")
    try:
        with urlopen(request, timeout=3) as response:  # nosec B310
            return (
                response.status,
                response.headers.get_content_type(),
                response.read().decode("utf-8"),
            )
    except HTTPError as exc:
        return exc.code, exc.headers.get_content_type(), exc.read().decode("utf-8")


def _stub_relay_class(outcome: RelayOutcome) -> type:
    """Return a drop-in OperatorRelay that yields ``outcome`` without a hub."""

    class _StubRelay:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def relay_message(self, to: str, text: str) -> RelayOutcome:
            return outcome

        async def relay_task(
            self, task_id: str, title: str, *, depends_on: object = ()
        ) -> RelayOutcome:
            return outcome

        async def relay_task_update(
            self, task_id: str, *, status: str | None = None, note: str | None = None
        ) -> RelayOutcome:
            return outcome

    return _StubRelay


def _operator_server(*, dashboard_token: str | None = None) -> DashboardServer:
    return start_dashboard_server(
        host="127.0.0.1",
        port=0,
        uri="ws://hub.invalid",
        name="DASH",
        token=None,
        ready_timeout=0.2,
        response_timeout=0.2,
        refresh_seconds=5,
        allow_non_loopback=False,
        operator=True,
        dashboard_token=dashboard_token,
    )


def test_operator_write_is_404_without_operator_mode() -> None:
    server = start_dashboard_server(
        host="127.0.0.1",
        port=0,
        uri="ws://hub.invalid",
        name="DASH",
        token=None,
        ready_timeout=0.2,
        response_timeout=0.2,
        refresh_seconds=5,
        allow_non_loopback=False,
    )
    try:
        status, _, _ = _http_post(server.url("/message"), json.dumps({"to": "x", "text": "hi"}))
    finally:
        server.close()

    assert status == 404


def test_operator_write_requires_bearer_when_token_set() -> None:
    server = _operator_server(dashboard_token="viewer")
    try:
        missing, _, _ = _http_post(server.url("/message"), json.dumps({"to": "x", "text": "hi"}))
        wrong, _, _ = _http_post(
            server.url("/message"),
            json.dumps({"to": "x", "text": "hi"}),
            authorization="Bearer nope",
        )
    finally:
        server.close()

    assert missing == 401
    assert wrong == 401


def test_operator_write_rejects_bad_bodies() -> None:
    server = _operator_server()
    try:
        non_json, _, _ = _http_post(server.url("/message"), "not json at all")
        missing_to, _, _ = _http_post(server.url("/message"), json.dumps({"text": "hi"}))
        empty_text, _, _ = _http_post(
            server.url("/message"), json.dumps({"to": "x", "text": "   "})
        )
        not_object, _, _ = _http_post(server.url("/message"), json.dumps(["to", "text"]))
        unknown_route, _, _ = _http_post(
            server.url("/other"), json.dumps({"to": "x", "text": "hi"})
        )
    finally:
        server.close()

    assert non_json == 400
    assert missing_to == 400
    assert empty_text == 400
    assert not_object == 400
    assert unknown_route == 404


@pytest.mark.parametrize(
    ("outcome", "expected_status"),
    [
        (RelayOutcome(DELIVERED, "delivered to a live recipient"), 200),
        (RelayOutcome(UNDELIVERED, "accepted; no live recipient (dead-lettered)"), 200),
        (RelayOutcome(DENIED, "no chat rule for team-b"), 403),
        (RelayOutcome(UNREACHABLE, "could not reach hub"), 503),
    ],
)
def test_operator_write_maps_relay_outcome_to_status(
    monkeypatch: pytest.MonkeyPatch, outcome: RelayOutcome, expected_status: int
) -> None:
    monkeypatch.setattr(dashboard_module, "OperatorRelay", _stub_relay_class(outcome))
    server = _operator_server()
    try:
        status, content_type, body = _http_post(
            server.url("/message"), json.dumps({"to": "SC-NEUROCORE", "text": "ship it"})
        )
    finally:
        server.close()

    assert status == expected_status
    assert content_type == "application/json"
    document = json.loads(body)
    assert document["action"] == "message"
    assert document["to"] == "SC-NEUROCORE"
    assert document["status"] == outcome.status
    assert document["ok"] is outcome.ok


def test_operator_write_is_rate_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dashboard_module, "OPERATOR_RATE_MAX", 1)
    monkeypatch.setattr(
        dashboard_module,
        "OperatorRelay",
        _stub_relay_class(RelayOutcome(DELIVERED, "delivered")),
    )
    server = _operator_server()
    try:
        first, _, _ = _http_post(server.url("/message"), json.dumps({"to": "x", "text": "hi"}))
        second, _, body = _http_post(server.url("/message"), json.dumps({"to": "x", "text": "hi"}))
    finally:
        server.close()

    assert first == 200
    assert second == 429
    assert "rate limit" in body


def test_operator_task_rejects_bad_bodies() -> None:
    server = _operator_server()
    try:
        missing_id, _, _ = _http_post(server.url("/task"), json.dumps({"title": "Ship"}))
        empty_title, _, _ = _http_post(
            server.url("/task"), json.dumps({"id": "T-1", "title": "  "})
        )
        bad_deps, _, _ = _http_post(
            server.url("/task"),
            json.dumps({"id": "T-1", "title": "Ship", "depends_on": [1, 2]}),
        )
    finally:
        server.close()

    assert missing_id == 400
    assert empty_title == 400
    assert bad_deps == 400


def test_operator_task_update_rejects_bad_bodies() -> None:
    server = _operator_server()
    try:
        missing_id, _, _ = _http_post(server.url("/task/update"), json.dumps({"status": "done"}))
        neither, _, _ = _http_post(server.url("/task/update"), json.dumps({"id": "T-1"}))
        bad_status, _, _ = _http_post(
            server.url("/task/update"), json.dumps({"id": "T-1", "status": 7})
        )
        bad_note_type, _, _ = _http_post(
            server.url("/task/update"), json.dumps({"id": "T-1", "note": 7})
        )
        empty_note, _, _ = _http_post(
            server.url("/task/update"), json.dumps({"id": "T-1", "note": "   "})
        )
    finally:
        server.close()

    assert missing_id == 400
    assert neither == 400
    assert bad_status == 400
    assert bad_note_type == 400  # a present note that is not a string
    assert empty_note == 400  # a present note that is blank


@pytest.mark.parametrize(
    ("outcome", "expected_status"),
    [
        (RelayOutcome(ACCEPTED, "task 'T-1' declared on the board"), 200),
        (RelayOutcome(DENIED, "no board rule for team-b"), 403),
        (RelayOutcome(REJECTED, "Task title is required."), 409),
        (RelayOutcome(UNREACHABLE, "could not reach hub"), 503),
    ],
)
def test_operator_task_maps_relay_outcome_to_status(
    monkeypatch: pytest.MonkeyPatch, outcome: RelayOutcome, expected_status: int
) -> None:
    monkeypatch.setattr(dashboard_module, "OperatorRelay", _stub_relay_class(outcome))
    server = _operator_server()
    try:
        status, content_type, body = _http_post(
            server.url("/task"),
            json.dumps({"id": "T-1", "title": "Ship", "depends_on": ["T-0"]}),
        )
    finally:
        server.close()

    assert status == expected_status
    assert content_type == "application/json"
    document = json.loads(body)
    assert document["action"] == "task"
    assert document["id"] == "T-1"
    assert document["status"] == outcome.status
    assert document["ok"] is outcome.ok


@pytest.mark.parametrize(
    ("outcome", "expected_status"),
    [
        (RelayOutcome(ACCEPTED, "task 'T-1' update applied on the board"), 200),
        (RelayOutcome(DENIED, "no board rule for team-b"), 403),
        (RelayOutcome(REJECTED, "Unknown ledger status 'nope'."), 409),
        (RelayOutcome(UNREACHABLE, "could not reach hub"), 503),
    ],
)
def test_operator_task_update_maps_relay_outcome_to_status(
    monkeypatch: pytest.MonkeyPatch, outcome: RelayOutcome, expected_status: int
) -> None:
    monkeypatch.setattr(dashboard_module, "OperatorRelay", _stub_relay_class(outcome))
    server = _operator_server()
    try:
        status, content_type, body = _http_post(
            server.url("/task/update"),
            json.dumps({"id": "T-1", "status": "done", "note": "shipped"}),
        )
    finally:
        server.close()

    assert status == expected_status
    assert content_type == "application/json"
    document = json.loads(body)
    assert document["action"] == "task_update"
    assert document["id"] == "T-1"
    assert document["status"] == outcome.status
    assert document["ok"] is outcome.ok


def _raising_relay_class(exc: Exception) -> type:
    """Return a drop-in OperatorRelay whose relay coroutine raises ``exc``."""

    class _RaisingRelay:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def relay_message(self, to: str, text: str) -> RelayOutcome:
            raise exc

    return _RaisingRelay


def test_operator_write_maps_a_relay_exception_to_503(monkeypatch: pytest.MonkeyPatch) -> None:
    # A relay that dies mid-flight (a dropped socket, a runtime fault) must
    # surface as a fail-visible 503, never a 500 stack trace on a write surface.
    monkeypatch.setattr(
        dashboard_module, "OperatorRelay", _raising_relay_class(OSError("connection reset"))
    )
    server = _operator_server()
    try:
        status, _, body = _http_post(server.url("/message"), json.dumps({"to": "x", "text": "hi"}))
    finally:
        server.close()

    assert status == 503
    assert "operator relay failed" in body
    assert "connection reset" in body


def test_operator_write_refuses_an_oversize_body() -> None:
    # A body past the 64 KiB ceiling is refused before it is read as JSON, so a
    # write route cannot be used to feed the process an unbounded payload.
    server = _operator_server()
    oversize = json.dumps({"to": "x", "text": "z" * (64 * 1024 + 16)})
    try:
        status, _, body = _http_post(server.url("/message"), oversize)
    finally:
        server.close()

    assert status == 400
    assert "within the size limit" in body


def test_operator_write_refuses_a_non_numeric_content_length() -> None:
    # A hand-crafted request whose Content-Length is not a number must be
    # refused with a 400, not crash the handler — urllib always sends a valid
    # length, so this defence is exercised over a raw socket.
    server = _operator_server()
    host = str(server.server.server_address[0])
    port = int(server.server.server_address[1])
    raw = (
        "POST /message HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Content-Type: application/json\r\n"
        "Content-Length: not-a-number\r\n"
        "Connection: close\r\n"
        "\r\n"
    ).encode("ascii")
    try:
        with socket.create_connection((host, port), timeout=3) as connection:
            connection.sendall(raw)
            response = b""
            while True:
                chunk = connection.recv(4096)
                if not chunk:
                    break
                response += chunk
    finally:
        server.close()

    status_line = response.split(b"\r\n", 1)[0].decode("ascii")
    assert "400" in status_line


def test_dashboard_parser_wires_operator_flags() -> None:
    parser = cli.build_parser()
    args = parser.parse_args(["dashboard", "--operator", "--operator-name", "operator:CEO"])

    assert args.operator is True
    assert args.operator_name == "operator:CEO"
    default = parser.parse_args(["dashboard"])
    assert default.operator is False
    assert default.operator_name is None
