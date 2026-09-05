# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real host observation HTTP access tests
"""Prove explicit grants, safe defaults and live revocation through real HTTP."""

import asyncio
import json
import os
import shutil
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from http import HTTPStatus
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from _platform_caps import PROC_AVAILABLE, requires_posix_mode_bits, requires_proc
from hub_e2e_helpers import close_agents, connect_agent, running_hub
from synapse_channel.dashboard import DashboardServer, start_dashboard_server
from synapse_channel.dashboard_access import compatibility_access_policy
from synapse_channel.dashboard_host_sessions import host_session_response, load_host_grants
from synapse_channel.host_sessions import HostSessionMonitor

TOKEN = "disposable-host-observation-test-token"


def request(
    server: DashboardServer, path: str, token: str | None = TOKEN
) -> tuple[int, dict[str, str], bytes]:
    headers = {} if token is None else {"Authorization": f"Bearer {token}"}
    try:
        response = urlopen(Request(server.url(path), headers=headers), timeout=5)
    except HTTPError as error:
        response = error
    with response:
        return int(response.status), dict(response.headers), bytes(response.read())


def serve(path: Path | None) -> DashboardServer:
    return start_dashboard_server(
        host="127.0.0.1",
        port=0,
        uri="ws://127.0.0.1:1",
        name="host-test",
        token=None,
        ready_timeout=0.01,
        response_timeout=0.01,
        refresh_seconds=2,
        allow_non_loopback=False,
        dashboard_token=TOKEN,
        host_sessions_access_file=path,
        host_session_pids=(os.getpid(),),
        host_session_tmux_socket="/nonexistent/synapse-monitor-test.sock",
    )


def policy(path: Path, observers: dict[str, dict[str, bool]]) -> None:
    path.write_text(json.dumps({"version": 1, "observers": observers}))
    path.chmod(0o600)


def test_disabled_does_not_expose_host_metadata() -> None:
    server = serve(None)
    try:
        for token in (None, TOKEN):
            assert request(server, "/host-sessions.json", token)[0] == 404
    finally:
        server.close()


def test_real_http_explicit_grants_and_live_revocation(tmp_path: Path) -> None:
    path = tmp_path / "grants.json"
    policy(path, {})
    server = serve(path)
    try:
        assert request(server, "/host-sessions.json", None)[0] == 401
        assert request(server, "/host-sessions.json")[0] == 403
        policy(path, {"compatibility": {"paths": False, "context": False}})
        status, headers, body = request(server, "/host-sessions-access.json")
        assert status == 200 and headers["Cache-Control"] == "no-store"
        assert json.loads(body) == {"version": 1, "observe": True, "paths": False, "context": False}
        status, headers, body = request(server, "/host-sessions.json")
        assert status == 200 and headers["Vary"] == "Authorization"
        document = json.loads(body)
        if PROC_AVAILABLE:
            assert document["rows"][0]["pid"] == os.getpid()
            assert document["rows"][0]["cwd"] is None
            assert document["rows"][0]["cwd_status"] == "not_requested"
            assert document["rows"][0]["context_status"] == "not_requested"
        else:
            assert document["process_status"] == "unavailable"
            assert document["rows"] == []
        assert TOKEN.encode() not in body
        policy(path, {"compatibility": {"paths": True, "context": True}})
        status, _, body = request(server, "/host-sessions.json")
        assert status == 200
        document = json.loads(body)
        if PROC_AVAILABLE:
            disclosed = document["rows"][0]
            assert disclosed["cwd"] == os.getcwd() and disclosed["cwd_status"] == "observed"
            assert disclosed["context_id"] is None and disclosed["context_status"] == "unsupported"
        else:
            assert document["process_status"] == "unavailable"
            assert document["rows"] == []
        policy(path, {})
        assert request(server, "/host-sessions.json")[0] == 403
    finally:
        server.close()


@requires_posix_mode_bits
def test_world_readable_grants_are_revoked(tmp_path: Path) -> None:
    path = tmp_path / "grants.json"
    policy(path, {"compatibility": {"paths": False, "context": False}})
    server = serve(path)
    try:
        assert request(server, "/host-sessions-access.json")[0] == 200
        path.chmod(0o644)
        assert request(server, "/host-sessions-access.json")[0] == 403
        assert request(server, "/host-sessions.json")[0] == 403
    finally:
        server.close()


@pytest.mark.parametrize(
    "payload",
    [
        "[]",
        '{"version":1}',
        '{"version":1,"observers":[]}',
        json.dumps({"version": 1, "observers": {str(i): {} for i in range(65)}}),
        '{"version":true,"observers":{}}',
        '{"version":1,"version":1,"observers":{}}',
        '{"version":2,"observers":{}}',
        '{"version":1,"observers":{"compatibility":{"paths":1,"context":false}}}',
        '{"version":1,"observers":{"compatibility":{"paths":false}}}',
        '{"version":1,"observers":{"bad id":{"paths":false,"context":false}}}',
    ],
)
def test_invalid_host_policy_rejected_before_bind(tmp_path: Path, payload: str) -> None:
    path = tmp_path / "grants.json"
    path.write_text(payload)
    path.chmod(0o600)
    with pytest.raises(ValueError):
        load_host_grants(path)


@pytest.mark.parametrize("change", ["revoke", "reduce", "remove", "invalid"])
def test_grant_change_during_collection_discards_observation(tmp_path: Path, change: str) -> None:
    path = tmp_path / "grants.json"
    policy(path, {"compatibility": {"paths": True, "context": False}})

    def coordination() -> tuple[tuple[str, ...], tuple[tuple[str, str], ...]]:
        if change == "revoke":
            policy(path, {})
        elif change == "reduce":
            policy(path, {"compatibility": {"paths": False, "context": False}})
        elif change == "remove":
            path.unlink()
        else:
            path.write_text("invalid json")
        return (), ()

    monitor = HostSessionMonitor(
        pids=(os.getpid(),), tmux_socket=str(tmp_path / "absent"), coordination=coordination
    )
    access = compatibility_access_policy(
        dashboard_token=TOKEN,
        token_protects_reads=True,
        operator_armed=False,
        operator_name="unused",
    )
    decision = host_session_response(
        "/host-sessions.json", f"Bearer {TOKEN}", access, path, monitor
    )
    assert decision.status == HTTPStatus.FORBIDDEN
    assert os.getcwd().encode() not in decision.body
    assert b'"rows"' not in decision.body


def test_busy_observation_returns_retryable_response_and_recovers(tmp_path: Path) -> None:
    path = tmp_path / "grants.json"
    policy(path, {"compatibility": {"paths": False, "context": False}})
    entered = threading.Event()
    release = threading.Event()

    def coordination() -> tuple[tuple[str, ...], tuple[tuple[str, str], ...]]:
        entered.set()
        assert release.wait(timeout=2)
        return (), ()

    monitor = HostSessionMonitor(
        pids=(os.getpid(),), tmux_socket=str(tmp_path / "absent"), coordination=coordination
    )
    access = compatibility_access_policy(
        dashboard_token=TOKEN,
        token_protects_reads=True,
        operator_armed=False,
        operator_name="unused",
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(monitor.snapshot)
        try:
            assert entered.wait(timeout=1)
            busy = host_session_response(
                "/host-sessions.json", f"Bearer {TOKEN}", access, path, monitor
            )
            assert busy.status == HTTPStatus.SERVICE_UNAVAILABLE
            assert b'"rows"' not in busy.body
        finally:
            release.set()
        observation = pending.result(timeout=2)
    recovered = host_session_response(
        "/host-sessions.json", f"Bearer {TOKEN}", access, path, monitor
    )
    assert recovered.status == HTTPStatus.OK
    assert json.loads(recovered.body)["observation_id"] == observation.observation_id


def test_connected_terminal_reads_the_same_http_observation(tmp_path: Path) -> None:
    path = tmp_path / "grants.json"
    policy(path, {"compatibility": {"paths": False, "context": False}})
    token = tmp_path / "bearer"
    token.write_text(TOKEN)
    token.chmod(0o600)
    server = serve(path)
    try:
        port = str(server.server.server_address[1])
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "synapse_channel.cli",
                "pid-monitor",
                "--dashboard-port",
                port,
                "--token-file",
                str(token),
                "--json",
            ],
            text=True,
            capture_output=True,
            timeout=5,
        )
        assert result.returncode == 0, result.stderr
        terminal = json.loads(result.stdout)
        status, _, raw = request(server, "/host-sessions.json")
        assert status == 200
        browser = json.loads(raw)
        assert terminal["observation_id"] == browser["observation_id"]
        assert terminal["rows"] == browser["rows"]
    finally:
        server.close()


@pytest.mark.parametrize("large", [False, True], ids=["presence-not-waiter", "response-limit"])
@requires_proc
@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux is not installed")
async def test_real_hub_claim_observations(tmp_path: Path, large: bool) -> None:
    socket = str(tmp_path / "tmux.sock")
    tmux = ["tmux", "-S", socket]
    subprocess.run(
        tmux
        + [
            "new-session",
            "-d",
            "-s",
            "fixture",
            "-e",
            "SYN_PROJECT=MONITOR-TEST",
            "-e",
            "SYN_IDENTITY=MONITOR-TEST/fixture",
            "sleep 120",
        ],
        check=True,
        timeout=5,
    )
    try:
        pid = int(
            subprocess.check_output(
                tmux + ["display-message", "-p", "-t", "fixture", "#{pane_pid}"],
                text=True,
                timeout=5,
            )
        )
        pids: tuple[int, ...] = (pid,)
        if large:
            second = int(
                subprocess.check_output(
                    tmux
                    + [
                        "split-window",
                        "-d",
                        "-t",
                        "fixture",
                        "-P",
                        "-F",
                        "#{pane_pid}",
                        "sleep 120",
                    ],
                    text=True,
                    timeout=5,
                )
            )
            pids += (second,)
        path = tmp_path / "grants.json"
        policy(path, {"compatibility": {"paths": False, "context": False}})
        async with running_hub() as (_, uri):
            agent = await connect_agent("MONITOR-TEST/fixture", uri)
            waiter = await connect_agent("MONITOR-TEST/fixture-rx", uri)
            server = start_dashboard_server(
                host="127.0.0.1",
                port=0,
                uri=uri,
                name="host-test-dashboard",
                token=None,
                ready_timeout=1,
                response_timeout=1,
                refresh_seconds=2,
                allow_non_loopback=False,
                dashboard_token=TOKEN,
                host_sessions_access_file=path,
                host_session_pids=pids,
                host_session_tmux_socket=socket,
            )
            try:
                await agent.agent.claim("host-observation-task", paths=["disposable-fixture"])
                await agent.recorder.wait_for(
                    lambda message: message.get("type") == "claim_granted"
                )
                if large:
                    for index in range(24):
                        task_id = f"{index}-" + "界" * 4090
                        await agent.agent.claim(task_id)

                        def claimed(message: dict[str, object], task: str = task_id) -> bool:
                            return (
                                message.get("type") == "claim_granted"
                                and message.get("task_id") == task
                            )

                        await agent.recorder.wait_for(claimed)
                status, _, body = await asyncio.to_thread(request, server, "/host-sessions.json")
                if large:
                    assert status == 503, body[:200]
                    assert body == b"host observation exceeds limit"
                    return
                assert status == 200
                observation = json.loads(body)
                row = observation["rows"][0]
                assert observation["coordination_status"] == "complete"
                assert row["presence"] is True
                assert row["waiters"] == ["MONITOR-TEST/fixture-rx"]
                assert row["claims"] == ["host-observation-task"]
            finally:
                await asyncio.to_thread(server.close)
                await close_agents(agent, waiter)
    finally:
        subprocess.run(tmux + ["kill-server"], check=False, timeout=5)
