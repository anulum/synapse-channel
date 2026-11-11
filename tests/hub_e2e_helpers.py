# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL - end-to-end hub test helpers

from __future__ import annotations

import asyncio
import contextlib
import errno
import json
import socket
import sqlite3
import sys
import threading
import traceback
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, cast

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.x509.oid import NameOID
from websockets.asyncio.connection import Connection

from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.federation import FederationBundle, FederationPeer, ScopeGrant
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.multihub_serving import MultiHubServingGrant, MultiHubServingPolicy
from synapse_channel.core.tls import (
    MTLSPeerTrustBundle,
    MTLSTrustedPeer,
    certificate_sha256_pin_from_der,
)

_MULTIHUB_DOMAIN = "test-follower-domain"
_MULTIHUB_NAMESPACE = "SYNAPSE-CHANNEL"
_MULTIHUB_KEY_ID = "test-follower-key"


def _free_port() -> int:
    """Return a loopback port free on IPv4 and, where IPv6 exists, on ``::1`` too.

    A hub bound to ``localhost`` listens on both families. A port that is free on
    127.0.0.1 can still be taken on ::1 by another process, which then fails the
    hub's bind with "address already in use", so both are checked.
    """
    for _ in range(64):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as ipv4:
            ipv4.bind(("127.0.0.1", 0))
            port = int(ipv4.getsockname()[1])
            try:
                with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as ipv6:
                    ipv6.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                    ipv6.bind(("::1", port))
            except OSError as exc:
                if exc.errno == errno.EADDRINUSE:
                    continue
            return port
    raise RuntimeError("no loopback port is free on both IPv4 and IPv6")


async def _await_listening(port: int, timeout: float = 3.0) -> None:
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        try:
            reader, writer = await asyncio.open_connection("localhost", port)
        except OSError:
            await asyncio.sleep(0.02)
            continue
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
        return
    raise TimeoutError(f"hub did not start listening on {port}")


async def http_get(
    uri: str,
    path: str,
    *,
    authorization: str | None = None,
    timeout: float = 3.0,
    host: str = "localhost",
) -> tuple[int, dict[str, str], str]:
    port = int(uri.rsplit(":", 1)[1])
    reader, writer = await asyncio.open_connection("localhost", port)
    headers = [f"GET {path} HTTP/1.1", f"Host: {host}", "Connection: close"]
    if authorization is not None:
        headers.append(f"Authorization: {authorization}")
    writer.write(("\r\n".join(headers) + "\r\n\r\n").encode("utf-8"))
    await writer.drain()
    raw = await asyncio.wait_for(reader.read(), timeout=timeout)
    writer.close()
    with contextlib.suppress(Exception):
        await writer.wait_closed()
    head, _, body = raw.partition(b"\r\n\r\n")
    status = int(head.split(b"\r\n", 1)[0].split()[1])
    parsed_headers: dict[str, str] = {}
    for line in head.split(b"\r\n")[1:]:
        key, _, value = line.partition(b":")
        parsed_headers[key.decode("ascii")] = value.strip().decode("ascii")
    return status, parsed_headers, body.decode("utf-8")


@contextlib.asynccontextmanager
async def running_hub(hub: SynapseHub | None = None) -> AsyncIterator[tuple[SynapseHub, str]]:
    """Start a real hub on its kernel-assigned socket and join it on exit.

    Parameters
    ----------
    hub : SynapseHub or None, optional
        Concrete hub to serve; otherwise construct an isolated test hub.

    Yields
    ------
    tuple[SynapseHub, str]
        The serving hub and its actual loopback WebSocket address.

    Raises
    ------
    TimeoutError
        If the hub does not signal bind readiness within its normal deadline.
    """
    actual = hub if hub is not None else SynapseHub(hub_id="syn-test")
    task = asyncio.create_task(actual.serve("127.0.0.1", 0))
    try:
        host, port = await actual.wait_until_serving()
        yield actual, f"ws://{host}:{port}"
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def authorised_multihub_serving_policy(*senders: str) -> MultiHubServingPolicy:
    """Build a serving policy that trusts the named test peer identities.

    The socket harness is plaintext, so the policy's certificate source injects
    a valid DER certificate. Production keeps reading the certificate from the
    live mutual-TLS connection.

    Parameters
    ----------
    senders : str
        Peer identities allowed to pull the test hub's event log.

    Returns
    -------
    MultiHubServingPolicy
        A complete federation and mutual-TLS policy for the named peers.
    """
    key = ed25519.Ed25519PrivateKey.generate()
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "synapse-test-peer")])
    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=1))
        .sign(key, algorithm=None)
    )
    der = certificate.public_bytes(serialization.Encoding.DER)
    pin = certificate_sha256_pin_from_der(der)
    federation = FederationBundle(
        [
            FederationPeer(
                domain_id=_MULTIHUB_DOMAIN,
                namespaces=frozenset({_MULTIHUB_NAMESPACE}),
                certificate_pins=frozenset({pin}),
                signing_key_ids=frozenset({_MULTIHUB_KEY_ID}),
                scope_grants=(ScopeGrant("read", _MULTIHUB_NAMESPACE),),
            )
        ]
    )
    mtls = MTLSPeerTrustBundle(
        peers={
            _MULTIHUB_DOMAIN: MTLSTrustedPeer(
                peer_id=_MULTIHUB_DOMAIN,
                certificate_pins=frozenset({pin}),
                signing_key_ids=frozenset({_MULTIHUB_KEY_ID}),
                projects=frozenset({_MULTIHUB_NAMESPACE}),
            )
        }
    )
    grants = {
        sender: MultiHubServingGrant(
            domain_id=_MULTIHUB_DOMAIN,
            namespace=_MULTIHUB_NAMESPACE,
            signing_key_id=_MULTIHUB_KEY_ID,
        )
        for sender in senders
    }
    return MultiHubServingPolicy(
        federation=federation,
        mtls=mtls,
        grants=grants,
        clock=lambda: 0.0,
        cert_source=lambda _websocket: der,
    )


class Recorder:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def __call__(self, data: dict[str, Any]) -> None:
        self.messages.append(data)

    async def wait_for(
        self, predicate: Callable[[dict[str, Any]], bool], timeout: float = 3.0
    ) -> dict[str, Any]:
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            for message in list(self.messages):
                if predicate(message):
                    return message
            await asyncio.sleep(0.01)
        raise TimeoutError("expected message did not arrive")


@dataclass
class AgentHandle:
    agent: SynapseAgent
    recorder: Recorder
    task: asyncio.Task[None]

    async def close(self) -> None:
        """Stop the agent and wait for the hub-side disconnect path to run.

        Closing the WebSocket first makes the hub handler leave its receive
        loop and run ``unregister`` on the same event loop before this returns.
        A short yield still follows so deferred hub cleanup is scheduled.
        """
        self.agent.running = False
        connection = self.agent.connection
        if connection is not None:
            with contextlib.suppress(Exception):
                await connection.close()
        self.task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self.task
        # Let the hub connection handler finish unregister/capability drop.
        await asyncio.sleep(0)


async def connect_agent(
    name: str,
    uri: str,
    *,
    wait_presence: bool = True,
    token: str | None = None,
    takeover: bool = False,
    wake_capability: str | None = None,
) -> AgentHandle:
    recorder = Recorder()
    if wake_capability is None:
        agent = SynapseAgent(
            name,
            recorder,
            uri=uri,
            heartbeat_interval=60.0,
            verbose=False,
            token=token,
            takeover=takeover,
        )
    else:
        agent = SynapseAgent(
            name,
            recorder,
            uri=uri,
            heartbeat_interval=60.0,
            verbose=False,
            token=token,
            takeover=takeover,
            wake_capability=wake_capability,
        )
    task = asyncio.create_task(agent.connect())
    handle = AgentHandle(agent=agent, recorder=recorder, task=task)
    watchdog = threading.Timer(
        WELCOME_STALL_REPORT_SECONDS,
        _report_overdue_welcome,
        args=(name, threading.get_ident()),
    )
    watchdog.daemon = True
    watchdog.start()
    try:
        ready = await agent.wait_until_ready(3.0)
    finally:
        watchdog.cancel()
    if not ready:
        await handle.close()
        raise TimeoutError(f"agent {name} did not receive hub welcome")
    if wait_presence:
        await recorder.wait_for(
            lambda m: m.get("type") == "presence_update" and m.get("agent") == name
        )
    return handle


WELCOME_STALL_REPORT_SECONDS = 2.0
"""After this long without a hub welcome, print where every thread is."""


def _report_overdue_welcome(name: str, loop_thread: int) -> None:
    """Print every thread's stack while a hub welcome is overdue.

    The hub and the client share the test's event loop, so a welcome that does
    not arrive in time usually means that loop is blocked. The report is printed
    while the delay is still happening, from a timer thread, so it shows the
    blocking frame itself rather than the state after it ended.
    """
    frames = sys._current_frames()
    lines = [
        f"[connect_agent {name}] no hub welcome after {WELCOME_STALL_REPORT_SECONDS:g} s; "
        f"sqlite {sqlite3.sqlite_version}; thread stacks follow, the event loop first",
    ]
    for ident in sorted(frames, key=lambda thread: thread != loop_thread):
        role = "event loop" if ident == loop_thread else f"thread {ident}"
        lines.append(f"--- {role}\n" + "".join(traceback.format_stack(frames[ident])))
    print("\n".join(lines), file=sys.stderr, flush=True)


async def close_agents(*handles: AgentHandle) -> None:
    for handle in reversed(handles):
        await handle.close()


async def read_json(websocket: Connection, timeout: float = 3.0) -> dict[str, Any]:
    raw = await asyncio.wait_for(websocket.recv(), timeout=timeout)
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    return cast(dict[str, Any], json.loads(raw))


async def send_json(websocket: Connection, **payload: Any) -> None:
    await websocket.send(json.dumps(payload))


async def read_until_type(
    websocket: Connection, message_type: str, *, limit: int = 20, timeout: float = 3.0
) -> dict[str, Any]:
    for _ in range(limit):
        message = await read_json(websocket, timeout=timeout)
        if message.get("type") == message_type:
            return message
    raise TimeoutError(f"message type {message_type!r} did not arrive")


async def collect_available(websocket: Connection, duration: float = 0.15) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    loop = asyncio.get_event_loop()
    deadline = loop.time() + duration
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            return messages
        try:
            messages.append(await read_json(websocket, timeout=remaining))
        except (TimeoutError, asyncio.TimeoutError):
            return messages
