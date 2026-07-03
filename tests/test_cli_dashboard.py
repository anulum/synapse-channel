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
from pathlib import Path
from typing import cast
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

import synapse_channel.dashboard as dashboard_module
from hub_e2e_helpers import AgentHandle, close_agents, connect_agent, running_hub
from synapse_channel import cli
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.dashboard import (
    DashboardServer,
    DashboardSnapshot,
    fetch_dashboard_snapshot,
    render_dashboard_html,
    start_dashboard_server,
    validate_dashboard_bind,
)


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
