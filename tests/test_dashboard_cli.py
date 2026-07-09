# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — dashboard CLI tests

"""Tests for the dashboard CLI parser and dispatcher."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from dashboard_helpers import _http_get
from synapse_channel import cli
from synapse_channel.dashboard import DashboardServer
from synapse_channel.dashboard_bind import validate_dashboard_bind


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


def test_dashboard_non_loopback_gets_generated_dashboard_token() -> None:
    from synapse_channel.dashboard import start_dashboard_server

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
    from synapse_channel.dashboard import start_dashboard_server

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


def test_dashboard_url_brackets_an_ipv6_host() -> None:
    """An IPv6 bind renders a bracketed URL that a browser accepts."""
    server = _FakeDashboardServer(token=None, generated=False)
    del server  # the URL shaping lives on the real DashboardServer
    import types

    shaped = cast("DashboardServer", types.SimpleNamespace(host="::1", port=8765))
    assert DashboardServer.url(shaped, "/snapshot.json") == "http://[::1]:8765/snapshot.json"
    plain = cast("DashboardServer", types.SimpleNamespace(host="127.0.0.1", port=8765))
    assert DashboardServer.url(plain, "/") == "http://127.0.0.1:8765/"


def test_cmd_dashboard_serves_until_interrupted_and_closes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The dispatcher prints the URLs, blocks, and closes the server on Ctrl-C."""
    server = _FakeDashboardServer(token=None, generated=False)
    assert _run_dispatcher(monkeypatch, server) == 0
    out = capsys.readouterr().out
    assert "studio (command centre): http://127.0.0.1:8765/" in out
    assert "classic hub HTML: http://127.0.0.1:8765/classic" in out
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


def test_dashboard_parser_wires_observed_peer_flags() -> None:
    parser = cli.build_parser(command="dashboard")

    args = parser.parse_args(
        [
            "dashboard",
            "--observed-peer",
            "east=ws://127.0.0.1:8877",
            "--observed-token",
            "secret",
            "--observed-timeout",
            "3.5",
        ]
    )

    assert args.observed_peers[0].hub_id == "east"
    assert args.observed_peers[0].uri == "ws://127.0.0.1:8877"
    assert args.observed_token == "secret"
    assert args.observed_timeout == 3.5


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


def test_cmd_dashboard_announces_operator_write_routes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # With --operator the dispatcher must print the three write routes, so an
    # operator sees exactly which mutating endpoints the armed dashboard exposes.
    from synapse_channel import cli_dashboard

    server = _FakeDashboardServer(token=None, generated=False)
    monkeypatch.setattr(cli_dashboard, "start_dashboard_server", lambda **_: server)

    def interrupt(_: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("synapse_channel.cli_dashboard.time.sleep", interrupt)
    args = _dashboard_args(operator=True)
    handler = args.func  # type: ignore[attr-defined]

    assert int(handler(args)) == 0
    out = capsys.readouterr().out
    assert "operator write: POST http://127.0.0.1:8765/message" in out
    assert "operator task: POST http://127.0.0.1:8765/task" in out
    assert "operator task update: POST http://127.0.0.1:8765/task/update" in out


def test_dashboard_parser_wires_operator_flags() -> None:
    parser = cli.build_parser()
    args = parser.parse_args(["dashboard", "--operator", "--operator-name", "operator:CEO"])

    assert args.operator is True
    assert args.operator_name == "operator:CEO"
    default = parser.parse_args(["dashboard"])
    assert default.operator is False
    assert default.operator_name is None


def test_cmd_dashboard_refuses_a_stray_observed_pin(capsys: pytest.CaptureFixture[str]) -> None:
    """A pin without a matching --observed-peer exits 2 instead of starting the server."""
    args = cli.build_parser().parse_args(
        ["dashboard", "--observed-pin", "ghost=sha256:" + "a" * 64]
    )
    from synapse_channel import cli_dashboard

    assert cli_dashboard._cmd_dashboard(args) == 2
    assert "does not fetch" in capsys.readouterr().out + capsys.readouterr().err
