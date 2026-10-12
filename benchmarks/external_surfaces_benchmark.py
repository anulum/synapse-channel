# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — measure the A2A edge, the dashboard edge and a pane wake
"""Measure three external surfaces on loopback through their public entry points.

The A2A HTTP edge and the dashboard HTTP edge are served by the product's own
servers and reached over real TCP connections, one per request, each read to
its end. A pane wake is injected into an owned tmux session that runs a
harmless composer. A request whose connection is reset before its answer is
complete is counted, not timed. These serial shared-host measurements are
regression evidence, not isolated throughput claims.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import math
import os
import platform
import shutil
import socket
import statistics
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from synapse_channel.a2a_http import make_a2a_http_server
from synapse_channel.a2a_server import A2ABridge
from synapse_channel.a2a_store import A2ATaskStore
from synapse_channel.agent_tmux import AgentTmuxConfig, inject_wake, start_session
from synapse_channel.core.hub import SynapseHub
from synapse_channel.dashboard import start_dashboard_server

DEFAULT_RESULTS = Path(__file__).resolve().parent / "results/external_surfaces_benchmark.json"
DEFAULT_REQUESTS = 200
DEFAULT_WAKES = 5
_COMPOSER = (
    "#!/bin/sh\nprintf '\\033[999B› \\n'\n"
    "while IFS= read -r line; do printf '%s\\n› \\n' \"$line\"; done\n"
)


class _Agent:
    """Retain what the bridge forwards, as a connected worker would receive it."""

    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    async def chat(
        self, message: str, *, target: str = "all", metadata: dict[str, object] | None = None
    ) -> None:
        """Record one forwarded chat."""
        self.messages.append((target, message))


def _exchange(port: int, request: bytes) -> tuple[int, float]:
    """Send one request on a new connection and read the answer to its end.

    Returns the status and the seconds from connecting to the end of the
    answer; status 0 means the connection was reset before the answer ended.
    """
    start = time.perf_counter()
    answer = b""
    with socket.create_connection(("127.0.0.1", port), timeout=10.0) as sock:
        try:
            sock.sendall(request)
            while part := sock.recv(65536):
                answer += part
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            return 0, time.perf_counter() - start
    return int(answer.split(b" ", 2)[1]), time.perf_counter() - start


def measure_requests(port: int, requests: list[bytes], expected: int) -> dict[str, Any]:
    """Run every request in order and summarise the complete answers.

    Parameters
    ----------
    port
        Loopback port of the server under measurement.
    requests
        The raw requests, each sent on a connection of its own.
    expected
        The status a complete answer must carry to be timed.

    Returns
    -------
    dict[str, Any]
        Counts of requests, complete answers and resets, and the distribution
        of the complete answers when there is one.
    """
    outcomes = [_exchange(port, request) for request in requests]
    seconds = sorted(elapsed for status, elapsed in outcomes if status == expected)
    result: dict[str, Any] = {
        "requests": len(outcomes),
        "complete": len(seconds),
        "reset": sum(status == 0 for status, _ in outcomes),
    }
    if seconds:
        result.update(
            min_us=seconds[0] * 1e6,
            p50_us=statistics.median(seconds) * 1e6,
            p95_us=seconds[math.ceil(len(seconds) * 0.95) - 1] * 1e6,
            max_us=seconds[-1] * 1e6,
        )
    return result


def _post(port: int, host: str, index: int) -> bytes:
    body = json.dumps(
        {
            "message": {
                "messageId": f"measured-{host}-{index}",
                "role": "ROLE_USER",
                "parts": [{"text": "measured message"}],
            }
        }
    ).encode("utf-8")
    head = (
        "POST /message:send HTTP/1.1\r\n"
        f"Host: {host}\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n\r\n"
    )
    return head.encode("ascii") + body


def measure_a2a(requests: int) -> dict[str, Any]:
    """Measure the agent card, an accepted message and a refusal before the body.

    Parameters
    ----------
    requests
        Positive count of requests of each of the three kinds.

    Returns
    -------
    dict[str, Any]
        One summary per kind and the number of messages the worker received.
    """
    agent = _Agent()
    card: dict[str, Any] = {
        "name": "SYNAPSE CHANNEL",
        "description": "measured bridge",
        "supportedInterfaces": [
            {"url": "http://127.0.0.1", "protocolBinding": "HTTP+JSON", "protocolVersion": "1.0"}
        ],
        "version": "0.0",
        "capabilities": {
            "streaming": False,
            "pushNotifications": False,
            "extendedAgentCard": False,
        },
        "defaultInputModes": ["text/plain", "application/json"],
        "defaultOutputModes": ["text/plain", "application/json"],
        "skills": [],
    }
    bridge = A2ABridge(agent=agent, agent_card=card, target="WORKER", store=A2ATaskStore())
    server = make_a2a_http_server(bridge=bridge, host="127.0.0.1", port=0)
    port = int(server.server_address[1])
    authority = f"127.0.0.1:{port}"
    bridge.allowed_authorities = (authority,)
    card["supportedInterfaces"][0]["url"] = f"http://{authority}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        get = (
            "GET /.well-known/agent-card.json HTTP/1.1\r\n"
            f"Host: {authority}\r\nConnection: close\r\n\r\n"
        ).encode("ascii")
        return {
            "agent_card": measure_requests(port, [get] * requests, 200),
            "message_send": measure_requests(
                port, [_post(port, authority, index) for index in range(requests)], 200
            ),
            "host_refusal": measure_requests(
                port, [_post(port, "refused.invalid", index) for index in range(requests)], 403
            ),
            "messages_received": len(agent.messages),
        }
    finally:
        server.shutdown()
        thread.join(timeout=5.0)
        server.server_close()


async def measure_dashboard(requests: int) -> dict[str, Any]:
    """Measure the snapshot document and the classic page against a live hub.

    Parameters
    ----------
    requests
        Positive count of requests of each of the two kinds.

    Returns
    -------
    dict[str, Any]
        One summary per kind.
    """
    hub = SynapseHub()
    serving = asyncio.create_task(hub.serve("127.0.0.1", 0))
    try:
        host, hub_port = await hub.wait_until_serving()
        server = await asyncio.to_thread(
            start_dashboard_server,
            host="127.0.0.1",
            port=0,
            uri=f"ws://{host}:{hub_port}",
            name="BENCHMARK/dashboard",
            token=None,
            ready_timeout=5.0,
            response_timeout=5.0,
            refresh_seconds=5,
            allow_non_loopback=False,
        )
        try:

            def get(path: str) -> bytes:
                return (
                    f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{server.port}\r\n"
                    f"Authorization: Bearer {server.dashboard_token}\r\n"
                    "Connection: close\r\n\r\n"
                ).encode("ascii")

            return {
                kind: await asyncio.to_thread(
                    measure_requests, server.port, [get(path)] * requests, 200
                )
                for kind, path in (
                    ("snapshot_json", "/snapshot.json"),
                    ("classic_page", "/classic"),
                )
            }
        finally:
            await asyncio.to_thread(server.close)
    finally:
        serving.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await serving
        hub.close()


def _wake_once(root: Path, tmux: str, index: int) -> tuple[bool, float]:
    """Start one owned composer, wait until it is idle, and time one wake into it."""
    provider = root / "codex-fixture"
    provider.write_text(_COMPOSER, encoding="utf-8")
    provider.chmod(0o700)
    config = AgentTmuxConfig(
        identity="BENCHMARK/pane",
        session=f"synapse-bench-{uuid.uuid4().hex[:12]}",
        cwd=root,
        agent_command=(str(provider),),
        tmux_bin=tmux,
        registry_dir=root / f"registry-{index}",
    )
    try:
        start_session(config)
        deadline = time.monotonic() + 3.0
        idle = False
        while not idle and time.monotonic() < deadline:
            capture = subprocess.run(  # noqa: S603 - fixed argv on an owned session
                [tmux, "capture-pane", "-p", "-t", config.session],
                capture_output=True,
                text=True,
                check=False,
            )
            idle = "›" in capture.stdout
            time.sleep(0.02)
        start = time.perf_counter()
        result = inject_wake(config)
        return idle and result.injected, time.perf_counter() - start
    finally:
        subprocess.run(  # noqa: S603 - fixed argv on an owned session
            [tmux, "kill-session", "-t", config.session],
            capture_output=True,
            text=True,
            check=False,
        )


def measure_pane(wakes: int, tmux: str) -> dict[str, Any]:
    """Measure wakes into owned tmux composers, or say why none was measured.

    Parameters
    ----------
    wakes
        Positive count of wakes, each into a new owned session.
    tmux
        Name or path of the tmux executable.

    Returns
    -------
    dict[str, Any]
        The summary of injected wakes, or ``measured: False`` with the reason.
    """
    found = shutil.which(tmux)
    if found is None:
        return {"measured": False, "reason": f"{tmux} is not installed"}
    with tempfile.TemporaryDirectory(prefix="external-surfaces-") as directory:
        outcomes = [_wake_once(Path(directory), found, index) for index in range(wakes)]
    seconds = sorted(elapsed for injected, elapsed in outcomes if injected)
    result: dict[str, Any] = {"measured": True, "wakes": wakes, "injected": len(seconds)}
    if seconds:
        result.update(
            min_us=seconds[0] * 1e6,
            p50_us=statistics.median(seconds) * 1e6,
            max_us=seconds[-1] * 1e6,
        )
    return result


def profile(
    *, requests: int = DEFAULT_REQUESTS, wakes: int = DEFAULT_WAKES, tmux: str = "tmux"
) -> dict[str, Any]:
    """Measure the three surfaces one after another.

    Parameters
    ----------
    requests
        Positive count of HTTP requests of each kind.
    wakes
        Positive count of pane wakes.
    tmux
        Name or path of the tmux executable.

    Returns
    -------
    dict[str, Any]
        The summaries of the A2A edge, the dashboard edge and the pane wake.
    """
    return {
        "requests": requests,
        "a2a": measure_a2a(requests),
        "dashboard": asyncio.run(measure_dashboard(requests)),
        "pane": measure_pane(wakes, tmux),
    }


def _positive(value: str) -> int:
    count = int(value)
    if count < 1:
        raise argparse.ArgumentTypeError("count must be positive")
    return count


def main(argv: list[str] | None = None) -> int:
    """Run the three workloads and retain their measured results.

    Parameters
    ----------
    argv
        Command arguments; None reads the process arguments.

    Returns
    -------
    int
        Zero after the workloads finish and their evidence is written.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requests", type=_positive, default=DEFAULT_REQUESTS)
    parser.add_argument("--wakes", type=_positive, default=DEFAULT_WAKES)
    parser.add_argument("--tmux", default="tmux")
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    args = parser.parse_args(argv)
    result = profile(requests=args.requests, wakes=args.wakes, tmux=args.tmux)
    result["host"] = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "cpu": platform.processor(),
        "isolation": "shared host, serial loopback",
        "pid": os.getpid(),
    }
    args.results.parent.mkdir(parents=True, exist_ok=True)
    args.results.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
