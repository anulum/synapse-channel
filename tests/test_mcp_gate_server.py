# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — real TLS journeys through the MCP bearer gate
"""A real gate in front of a real pinned TLS listener: the faults of MG-12 as journeys."""

from __future__ import annotations

import asyncio
import hashlib
import ssl
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from mcp_gate_helpers import owner_file, seat_token
from mcp_http_helpers import tls_files
from synapse_channel.mcp import gate_server
from synapse_channel.mcp.gate import GateState
from synapse_channel.mcp.gate_config import GateConfig, GateSeat, new_bearer

FUTURE = int(datetime(2099, 1, 1, tzinfo=UTC).timestamp())


@dataclass
class Listener:
    """A stand-in for the HTTP MCP listener that records every request it reads."""

    received: list[bytes] = field(default_factory=list)
    connections: int = 0
    port: int = 0

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.connections += 1
        try:
            head = await reader.readuntil(b"\r\n\r\n")
        except (asyncio.IncompleteReadError, ConnectionError):
            writer.close()
            return
        length = 0
        for line in head.split(b"\r\n"):
            if line.lower().startswith(b"content-length:"):
                length = int(line.split(b":", 1)[1])
        body = await reader.readexactly(length)
        self.received.append(head + body)
        path = head.split(b" ", 2)[1]
        writer.write(RESPONSES.get(path, _ok(head)))
        await writer.drain()
        writer.close()


def _ok(head: bytes) -> bytes:
    session = b"" if b"mcp-session-id:" in head.lower() else b"Mcp-Session-Id: sess-1\r\n"
    body = b'{"ok":true}'
    return (
        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nWWW-Authenticate: Bearer\r\n"
        + session
        + b"Content-Length: "
        + str(len(body)).encode()
        + b"\r\n\r\n"
        + body
    )


RESPONSES = {
    b"/sse": b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n\r\ndata: one\n\n",
    b"/chunked": b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n4\r\nabcd\r\n0\r\n\r\n",
    b"/badchunk": b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\nzz\r\n",
    b"/raw": b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n\r\nraw body",
    b"/rawbig": b"HTTP/1.1 200 OK\r\n\r\n" + b"x" * 300,
    b"/big": b"HTTP/1.1 200 OK\r\nContent-Length: 300\r\n\r\n" + b"x" * 300,
    b"/denied": b"HTTP/1.1 401 Unauthorized\r\nContent-Length: 0\r\n\r\n",
    b"/garbage": b"garbage\r\n\r\n",
}


@dataclass
class Rig:
    """A running listener and gate, with each seat's stable bearer."""

    listener: Listener
    state: GateState
    port: int
    client: ssl.SSLContext
    bearers: dict[str, str]
    tokens: dict[str, str]

    async def call(self, raw: bytes) -> bytes:
        reader, writer = await asyncio.open_connection(
            "127.0.0.1", self.port, ssl=self.client, server_hostname="127.0.0.1"
        )
        writer.write(raw)
        await writer.drain()
        try:
            return await asyncio.wait_for(reader.read(), 10)
        finally:
            writer.close()

    async def post(self, seat: str | None, *headers: str, path: str = "/mcp") -> bytes:
        lines = [f"POST {path} HTTP/1.1", "Host: attacker", *headers, "Content-Length: 7"]
        if seat is not None:
            lines.insert(1, f"Authorization: Bearer {self.bearers[seat]}")
        return await self.call(("\r\n".join(lines) + "\r\n\r\n" + '{"a":1}').encode())


def status(response: bytes) -> int:
    return int(response.split(b" ", 2)[1])


@asynccontextmanager
async def rig(
    tmp_path: Path, *, pin: str | None = None, upstream_port: int | None = None
) -> AsyncIterator[Rig]:
    upstream_cert, upstream_key, _ = tls_files(_directory(tmp_path / "upstream"))
    gate_cert, gate_key, client = tls_files(_directory(tmp_path / "gate"))
    upstream_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    upstream_context.load_cert_chain(upstream_cert, upstream_key)
    listener = Listener()
    upstream = await asyncio.start_server(listener.handle, "127.0.0.1", 0, ssl=upstream_context)
    listener.port = upstream.sockets[0].getsockname()[1]
    der = ssl.PEM_cert_to_DER_cert(upstream_cert.read_text(encoding="ascii"))

    seats, bearers, tokens = [], {}, {}
    for name in ("PROJ/a", "PROJ/b"):
        tag = name.split("/")[1]
        seat = GateSeat(
            name, tmp_path / f"{tag}.bearer", tmp_path / f"{tag}.token", frozenset({"synapse_send"})
        )
        bearers[name] = new_bearer(seat.bearer_file)
        tokens[name] = seat_token(FUTURE)
        owner_file(seat.token_file, tokens[name])
        seats.append(seat)
    config = GateConfig(
        listen_host="127.0.0.1",
        listen_port=0,
        upstream_host="127.0.0.1",
        upstream_port=listener.port if upstream_port is None else upstream_port,
        upstream_cert_sha256=pin or hashlib.sha256(der).hexdigest(),
        public_host="gate.example",
        tls_cert_file=gate_cert,
        tls_key_file=gate_key,
        grant_file=tmp_path / "grants.json",
        seats=tuple(seats),
    )
    state = GateState(config)
    ready: asyncio.Future[int] = asyncio.get_running_loop().create_future()

    async def started(port: int) -> None:
        ready.set_result(port)

    task = asyncio.create_task(gate_server.serve_gate(state, started=started))
    try:
        port = await asyncio.wait_for(ready, 10)
        yield Rig(listener, state, port, client, bearers, tokens)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        upstream.close()
        await upstream.wait_closed()


def _directory(path: Path) -> Path:
    path.mkdir(parents=True)
    return path


# --- the bearer, the seat token and the headers ---------------------------------------------


async def test_a_bearer_request_reaches_the_listener_with_its_seat_token_only(
    tmp_path: Path,
) -> None:
    async with rig(tmp_path) as gate:
        response = await gate.post(
            "PROJ/a", "X-Forwarded-For: 1.2.3.4", "Cookie: c=1", "Accept: application/json"
        )
        assert status(response) == 200
        assert response.endswith(b'{"ok":true}')
        assert b"WWW-Authenticate" not in response and b"Connection: close" in response
        (sent,) = gate.listener.received
        assert f"Authorization: Bearer {gate.tokens['PROJ/a']}".encode() in sent
        assert b"Host: gate.example" in sent and b"accept: application/json" in sent
        for dropped in (
            b"x-forwarded-for",
            b"cookie",
            b"attacker",
            gate.bearers["PROJ/a"].encode(),
        ):
            assert dropped not in sent.lower()
        assert sent.endswith(b'Content-Length: 7\r\nConnection: close\r\n\r\n{"a":1}')


async def test_the_original_bypass_a_session_without_a_bearer_is_refused(
    tmp_path: Path,
) -> None:
    async with rig(tmp_path) as gate:
        opened = await gate.post("PROJ/a")
        assert b"Mcp-Session-Id: sess-1" in opened
        assert status(await gate.post(None, "Mcp-Session-Id: sess-1")) == 401
        assert status(await gate.post("PROJ/b", "Mcp-Session-Id: sess-1")) == 401
        assert len(gate.listener.received) == 1
        deleted = await gate.call(
            (
                f"DELETE /mcp HTTP/1.1\r\nAuthorization: Bearer {gate.bearers['PROJ/a']}\r\n"
                "Mcp-Session-Id: sess-1\r\n\r\n"
            ).encode()
        )
        assert status(deleted) == 200
        # once deleted, the session is the listener's to judge again
        assert status(await gate.post("PROJ/b", "Mcp-Session-Id: sess-1")) == 200


async def test_a_squatting_listener_receives_no_token(tmp_path: Path) -> None:
    async with rig(tmp_path, pin="0" * 64) as gate:
        assert status(await gate.post("PROJ/a")) == 502
        assert gate.listener.received == []


async def test_a_missing_listener_is_a_bad_gateway(tmp_path: Path) -> None:
    async with rig(tmp_path, upstream_port=1) as gate:
        assert status(await gate.post("PROJ/a")) == 502


async def test_an_expired_seat_token_is_unavailable_and_nothing_is_forwarded(
    tmp_path: Path,
) -> None:
    async with rig(tmp_path) as gate:
        owner_file(gate.state.config.seats[0].token_file, seat_token(1))
        assert status(await gate.post("PROJ/a")) == 503
        assert gate.listener.received == []


async def test_a_listener_refusing_a_fresh_token_is_unavailable(tmp_path: Path) -> None:
    async with rig(tmp_path) as gate:
        assert status(await gate.post("PROJ/a", path="/denied")) == 503


# --- request framing ------------------------------------------------------------------------


async def test_smuggling_shapes_are_refused_before_the_listener(tmp_path: Path) -> None:
    async with rig(tmp_path) as gate:
        bearer = f"Authorization: Bearer {gate.bearers['PROJ/a']}"
        for raw in (
            f"POST /mcp HTTP/1.1\r\n{bearer}\r\nContent-Length: 3\r\n"
            "Transfer-Encoding: chunked\r\n\r\n0\r\n\r\n",
            f"POST http://evil/mcp HTTP/1.1\r\n{bearer}\r\n\r\n",
            f"POST /mcp HTTP/1.1\r\n{bearer}\r\nTransfer-Encoding: chunked\r\n\r\nzz\r\n",
            f"POST /mcp HTTP/1.1\r\n{bearer}\r\nTransfer-Encoding: chunked\r\n\r\n2\r\nabXY",
            f"POST /mcp HTTP/1.1\r\n{bearer}\r\nTransfer-Encoding: chunked\r\n\r\n0x2\r\nab\r\n",
            f"POST /mcp HTTP/1.1\r\n{bearer}\r\nTransfer-Encoding: chunked\r\n\r\n1_0\r\n",
            f"POST /mcp HTTP/1.1\r\n{bearer}\r\nTransfer-Encoding: chunked\r\n\r\n 2\r\nab\r\n",
            f"POST /mcp HTTP/1.1\r\n{bearer}\r\nTransfer-Encoding: chunked\r\n\r\n123456789\r\n",
        ):
            assert status(await gate.call(raw.encode())) == 400
        assert gate.listener.received == []


async def test_a_chunked_request_is_forwarded_with_its_exact_length(tmp_path: Path) -> None:
    async with rig(tmp_path) as gate:
        raw = (
            f"POST /mcp HTTP/1.1\r\nAuthorization: Bearer {gate.bearers['PROJ/a']}\r\n"
            'Transfer-Encoding: chunked\r\n\r\n4\r\n{"a"\r\n3\r\n:1}\r\n0\r\nX-Trailer: y\r\n\r\n'
        )
        assert status(await gate.call(raw.encode())) == 200
        (sent,) = gate.listener.received
        assert sent.endswith(b'Content-Length: 7\r\nConnection: close\r\n\r\n{"a":1}')
        assert b"chunked" not in sent.lower()


async def test_oversized_heads_and_bodies_are_refused(tmp_path: Path) -> None:
    async with rig(tmp_path) as gate:
        huge = "X-Pad: " + "p" * 70000
        assert status(await gate.call(f"POST /mcp HTTP/1.1\r\n{huge}\r\n\r\n".encode())) == 431
        raw = (
            f"POST /mcp HTTP/1.1\r\nAuthorization: Bearer {gate.bearers['PROJ/a']}\r\n"
            f"Transfer-Encoding: chunked\r\n\r\n{2 << 20:x}\r\n"
        )
        assert status(await gate.call(raw.encode())) == 413


async def test_a_slow_loris_head_or_body_loses_its_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gate_server, "HEAD_DEADLINE", 0.3)
    monkeypatch.setattr(gate_server, "BODY_DEADLINE", 0.3)
    async with rig(tmp_path) as gate:
        assert await gate.call(b"POST /mcp HTTP/1.1\r\nHost: x") == b""
        partial = "POST /mcp HTTP/1.1\r\nContent-Length: 10\r\nAuthorization: Bearer x\r\n\r\nabc"
        assert await gate.call(partial.encode()) == b""
        assert gate.state.open_connections == 0


async def test_a_connection_that_ends_early_is_dropped_quietly(tmp_path: Path) -> None:
    async with rig(tmp_path) as gate:
        for raw in (b"", b"POST /mcp HTTP/1.1\r\nHost: x"):
            reader, writer = await asyncio.open_connection(
                "127.0.0.1", gate.port, ssl=gate.client, server_hostname="127.0.0.1"
            )
            writer.write(raw)
            await writer.drain()
            writer.close()
            await writer.wait_closed()
        body = "POST /mcp HTTP/1.1\r\nContent-Length: 10\r\n\r\nabc"
        reader, writer = await asyncio.open_connection(
            "127.0.0.1", gate.port, ssl=gate.client, server_hostname="127.0.0.1"
        )
        writer.write(body.encode())
        await writer.drain()
        writer.close()
        await asyncio.sleep(0.2)
        assert status(await gate.post("PROJ/a")) == 200


async def test_connections_past_the_limit_are_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gate_server, "MAX_CONNECTIONS", 0)
    async with rig(tmp_path) as gate:
        assert await gate.post("PROJ/a") == b""


# --- responses ----------------------------------------------------------------------------


async def test_responses_are_relayed_and_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gate_server, "MAX_RESPONSE", 100)
    async with rig(tmp_path) as gate:
        streamed = await gate.post("PROJ/a", path="/sse")
        assert status(streamed) == 200 and streamed.endswith(b"data: one\n\n")
        chunked = await gate.post("PROJ/a", path="/chunked")
        assert chunked.endswith(b"Content-Length: 4\r\nConnection: close\r\n\r\nabcd")
        raw = await gate.post("PROJ/a", path="/raw")
        assert raw.endswith(b"Content-Length: 8\r\nConnection: close\r\n\r\nraw body")
        for path in ("/big", "/rawbig", "/badchunk", "/garbage"):
            assert status(await gate.post("PROJ/a", path=path)) == 502, path


async def test_the_gate_serves_without_a_start_callback(tmp_path: Path) -> None:
    async with rig(tmp_path) as gate:
        state = GateState(replace(gate.state.config, listen_port=0))
        task = asyncio.create_task(gate_server.serve_gate(state))
        await asyncio.sleep(0.2)
        assert not task.done()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def test_a_connection_without_tls_has_no_peer_certificate() -> None:
    class Plain:
        def get_extra_info(self, name: str) -> None:
            return None

    assert gate_server.peer_certificate(cast(asyncio.StreamWriter, Plain())) is None
