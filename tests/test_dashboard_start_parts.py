# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — what the dashboard start binds before it serves
"""What one dashboard start binds: its access, its files, its host monitor and its thread."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from synapse_channel.dashboard import DashboardServer, _DashboardHandler, start_dashboard_server

_OPERATOR_TOKEN = "operator-token-that-is-at-least-32-bytes"


def _start(
    *,
    dashboard_token: str | None = None,
    dashboard_access_file: Path | None = None,
    operator: bool = False,
    operator_name: str | None = None,
    host_session_tmux_socket: str | None = None,
    host_session_context_root: str | None = None,
    a2a_state_file: str | None = None,
    reliability_db: str | None = None,
    reliability_db_key_file: str | None = None,
    attention_store: str | None = None,
    federation_store: str | None = None,
    cockpit_dist: str | None = None,
) -> DashboardServer:
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
        dashboard_token=dashboard_token,
        dashboard_access_file=dashboard_access_file,
        operator=operator,
        operator_name=operator_name,
        host_session_tmux_socket=host_session_tmux_socket,
        host_session_context_root=host_session_context_root,
        a2a_state_file=a2a_state_file,
        reliability_db=reliability_db,
        reliability_db_key_file=reliability_db_key_file,
        attention_store=attention_store,
        federation_store=federation_store,
        cockpit_dist=cockpit_dist,
    )


def _handler(server: DashboardServer) -> type[_DashboardHandler]:
    return cast("type[_DashboardHandler]", server.server.RequestHandlerClass)


def _access_file(tmp_path: Path) -> Path:
    token = tmp_path / "operator.token"
    token.write_text(_OPERATOR_TOKEN + "\n", encoding="utf-8")
    token.chmod(0o600)
    principal = {
        "id": "operator",
        "role": "operator",
        "token_file": token.name,
        "operator_name": "operator:studio/operator",
    }
    policy = tmp_path / "dashboard-access.json"
    policy.write_text(json.dumps({"version": 1, "principals": [principal]}), encoding="utf-8")
    policy.chmod(0o600)
    return policy


def test_a_start_from_an_access_file_keeps_no_bearer_of_its_own(tmp_path: Path) -> None:
    server = _start(dashboard_access_file=_access_file(tmp_path), operator=True)
    try:
        assert server.dashboard_token is None
        assert server.dashboard_token_generated is False
        principal = _handler(server).access_policy.resolve_credential(f"Bearer {_OPERATOR_TOKEN}")
        assert principal is not None
        assert principal.operator_name == "operator:studio/operator"
    finally:
        server.close()


def test_an_armed_bearer_writes_as_the_operator_of_this_dashboard_unless_named() -> None:
    for operator_name, expected in (
        (None, "operator:SYNAPSE-CHANNEL/dashboard"),
        ("operator:CEO", "operator:CEO"),
    ):
        server = _start(dashboard_token=_OPERATOR_TOKEN, operator=True, operator_name=operator_name)
        try:
            policy = _handler(server).access_policy
            principal = policy.resolve_credential(f"Bearer {_OPERATOR_TOKEN}")
            assert principal is not None
            assert principal.operator_name == expected
            assert server.dashboard_token == _OPERATOR_TOKEN
            assert server.dashboard_token_generated is False
        finally:
            server.close()


def test_the_host_monitor_keeps_the_socket_and_the_resolved_context_root(tmp_path: Path) -> None:
    (tmp_path / "root").mkdir()
    server = _start(
        host_session_tmux_socket=str(tmp_path / "tmux.sock"),
        host_session_context_root=str(tmp_path / "other" / ".." / "root"),
    )
    try:
        monitor = _handler(server).host_session_monitor
        assert monitor.tmux_socket == str(tmp_path / "tmux.sock")
        assert monitor.context_root == (tmp_path / "root").resolve()
        assert monitor.pids == ()
    finally:
        server.close()
    bare = _start()
    try:
        assert _handler(bare).host_session_monitor.context_root is None
        assert _handler(bare).host_session_monitor.tmux_socket is None
    finally:
        bare.close()


def test_each_named_file_becomes_a_path_and_an_unnamed_one_stays_absent(tmp_path: Path) -> None:
    names = {
        "a2a_state_file": tmp_path / "a2a.json",
        "reliability_db": tmp_path / "hub.db",
        "reliability_db_key_file": tmp_path / "hub.key",
        "attention_store": tmp_path / "attention.json",
        "federation_store": tmp_path / "federation.json",
        "cockpit_dist": tmp_path / "dist",
    }
    server = _start(
        a2a_state_file=str(names["a2a_state_file"]),
        reliability_db=str(names["reliability_db"]),
        reliability_db_key_file=str(names["reliability_db_key_file"]),
        attention_store=str(names["attention_store"]),
        federation_store=str(names["federation_store"]),
        cockpit_dist=str(names["cockpit_dist"]),
    )
    try:
        handler = _handler(server)
        for key, value in names.items():
            assert getattr(handler, key) == value, key
            assert isinstance(getattr(handler, key), Path), key
    finally:
        server.close()
    bare = _start()
    try:
        for key in names:
            assert getattr(_handler(bare), key) is None, key
    finally:
        bare.close()


def test_the_server_serves_on_a_named_daemon_thread() -> None:
    server = _start()
    try:
        assert server.thread.daemon is True
        assert server.thread.name == "synapse-dashboard"
        assert server.thread.is_alive()
    finally:
        server.close()
    assert not server.thread.is_alive()
