# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — built-wheel WebSocket hub and metrics compatibility smoke
"""Exercise the installed package against one selected ``websockets`` release.

The compatibility workflow runs this file outside the source tree after installing
the built wheel into a clean virtual environment.  The smoke deliberately crosses
both library seams that have changed between ``websockets`` releases: a real
WebSocket upgrade and the same server's HTTP ``/health`` and ``/metrics`` hooks.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import socket
from importlib import metadata
from pathlib import Path
from typing import Any

from websockets.asyncio.client import connect

import synapse_channel
from synapse_channel.core.hub import SynapseHub


def assert_package_outside(package_file: str, forbidden_root: Path) -> None:
    """Reject a smoke that accidentally imported the checkout instead of the wheel."""
    package_path = Path(package_file).resolve()
    root = forbidden_root.resolve()
    if package_path == root or root in package_path.parents:
        raise RuntimeError(f"synapse_channel imported from forbidden source root: {package_path}")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def _await_listening(port: int, *, timeout: float = 3.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        try:
            status, _headers, _body = await _http_get(port, "/health")
        except OSError:
            await asyncio.sleep(0.02)
            continue
        if status == 200:
            return
        await asyncio.sleep(0.02)
    raise TimeoutError(f"hub did not listen on port {port}")


async def _http_get(port: int, path: str) -> tuple[int, dict[str, str], bytes]:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    request = f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n"
    writer.write(request.encode("ascii"))
    await writer.drain()
    raw = await asyncio.wait_for(reader.read(), timeout=3.0)
    writer.close()
    with contextlib.suppress(Exception):
        await writer.wait_closed()

    head, separator, body = raw.partition(b"\r\n\r\n")
    if not separator:
        raise RuntimeError(f"HTTP response has no header terminator: {raw[:200]!r}")
    lines = head.split(b"\r\n")
    status = int(lines[0].split()[1])
    headers: dict[str, str] = {}
    for line in lines[1:]:
        key, marker, value = line.partition(b":")
        if marker:
            headers[key.decode("ascii").lower()] = value.strip().decode("ascii")
    return status, headers, body


async def _read_until_type(websocket: Any, expected: str) -> dict[str, Any]:
    for _ in range(20):
        raw = await asyncio.wait_for(websocket.recv(), timeout=3.0)
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        message = json.loads(raw)
        if isinstance(message, dict) and message.get("type") == expected:
            return message
    raise TimeoutError(f"hub did not emit {expected!r}")


async def run_smoke() -> dict[str, object]:
    """Run one live hub, WebSocket registration, state query, and HTTP probe smoke."""
    port = _free_port()
    hub = SynapseHub(hub_id="websockets-compat", enable_metrics=True)
    server = asyncio.create_task(hub.serve("127.0.0.1", port))
    try:
        await _await_listening(port)
        async with connect(f"ws://127.0.0.1:{port}", open_timeout=3.0) as websocket:
            await websocket.send(
                json.dumps(
                    {
                        "sender": "compat-smoke",
                        "target": "System",
                        "type": "heartbeat",
                        "payload": "online",
                    }
                )
            )
            welcome = await _read_until_type(websocket, "welcome")
            if welcome.get("hub_id") != "websockets-compat":
                raise RuntimeError(f"unexpected welcome frame: {welcome!r}")

            await websocket.send(json.dumps({"sender": "compat-smoke", "type": "state_request"}))
            state = await _read_until_type(websocket, "state_snapshot")
            if state.get("target") != "compat-smoke":
                raise RuntimeError(f"unexpected state frame: {state!r}")

        health_status, health_headers, health_body = await _http_get(port, "/health")
        if health_status != 200 or health_headers.get("content-type") != "application/json":
            raise RuntimeError(
                f"health probe failed: status={health_status}, headers={health_headers!r}"
            )
        health = json.loads(health_body)
        if health.get("status") != "ok" or health.get("hub_id") != "websockets-compat":
            raise RuntimeError(f"unexpected health document: {health!r}")

        metrics_status, metrics_headers, metrics_body = await _http_get(port, "/metrics")
        if metrics_status != 200 or not metrics_headers.get("content-type", "").startswith(
            "text/plain"
        ):
            raise RuntimeError(
                f"metrics probe failed: status={metrics_status}, headers={metrics_headers!r}"
            )
        if b"# TYPE synapse_up gauge" not in metrics_body or b"synapse_up 1" not in metrics_body:
            raise RuntimeError("metrics response omitted the live hub gauge")

        return {
            "hub": "ok",
            "health": "ok",
            "metrics": "ok",
            "synapse_channel": metadata.version("synapse-channel"),
            "websockets": metadata.version("websockets"),
        }
    finally:
        server.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await server


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--forbid-package-root",
        type=Path,
        help="fail if synapse_channel resolves below this source-tree path",
    )
    parser.add_argument(
        "--expected-websockets",
        default="",
        help="require this exact installed websockets version when non-empty",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    package_file = synapse_channel.__file__
    if package_file is None:
        raise RuntimeError("synapse_channel has no import path")
    if args.forbid_package_root is not None:
        assert_package_outside(package_file, args.forbid_package_root)

    installed_websockets = metadata.version("websockets")
    if args.expected_websockets and installed_websockets != args.expected_websockets:
        raise RuntimeError(
            f"expected websockets {args.expected_websockets}, installed {installed_websockets}"
        )
    print(json.dumps(asyncio.run(run_smoke()), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
