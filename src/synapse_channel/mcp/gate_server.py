# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — the network side of the MCP bearer gate
"""Accept TLS connections, enforce the gate's deadlines and forward one request each."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import ssl
from collections.abc import Awaitable, Callable

from synapse_channel.mcp.gate import (
    BODY_DEADLINE,
    HEAD_DEADLINE,
    MAX_BODY,
    MAX_CONNECTIONS,
    MAX_HEAD,
    MAX_RESPONSE,
    STREAM_IDLE,
    UPSTREAM_DEADLINE,
    GateState,
    Refusal,
    Request,
    check_pin,
    client_response_head,
    error_response,
    parse_request,
    pinned_upstream_context,
    presented_bearer,
    resolve_seat,
    response_status,
    response_values,
    upstream_head,
)
from synapse_channel.mcp.gate_config import SeatTokenUnavailable, read_seat_token

LOG = logging.getLogger("synapse.mcp.gate")
_STREAM_CHUNK = 65536


async def read_head(reader: asyncio.StreamReader) -> bytes:
    """Read one head up to its blank line; refuse an oversized or unterminated one."""
    try:
        data = await reader.readuntil(b"\r\n\r\n")
    except asyncio.LimitOverrunError:
        raise Refusal(431, "head too large") from None
    except asyncio.IncompleteReadError as exc:
        if exc.partial:
            raise Refusal(400, "head not terminated") from None
        return b""
    return data[:-4]


async def read_chunked(reader: asyncio.StreamReader, limit: int) -> bytes:
    """De-chunk a body of at most ``limit`` bytes; refuse a malformed one."""
    parts: list[bytes] = []
    total = 0
    while True:
        line = await reader.readuntil(b"\r\n")
        try:
            size = int(line[:-2].split(b";", 1)[0], 16)
        except ValueError:
            raise Refusal(400, "chunk size invalid") from None
        total += size
        if size < 0 or total > limit:
            raise Refusal(413, "body too large")
        if size == 0:
            while await reader.readuntil(b"\r\n") != b"\r\n":
                pass
            return b"".join(parts)
        chunk = await reader.readexactly(size + 2)
        if chunk[-2:] != b"\r\n":
            raise Refusal(400, "chunk invalid")
        parts.append(chunk[:-2])


async def read_body(reader: asyncio.StreamReader, request: Request) -> bytes:
    """Read the request body within its total deadline."""
    try:
        if request.length is None:
            return await asyncio.wait_for(read_chunked(reader, MAX_BODY), BODY_DEADLINE)
        return await asyncio.wait_for(reader.readexactly(request.length), BODY_DEADLINE)
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError):
        raise Refusal(400, "body incomplete") from None


async def read_response_body(reader: asyncio.StreamReader, head: bytes) -> bytes:
    """Read a buffered upstream body of at most :data:`MAX_RESPONSE` bytes."""
    lengths = response_values(head, b"content-length")
    if lengths:
        if not lengths[0].isdigit() or int(lengths[0]) > MAX_RESPONSE:
            raise Refusal(502, "upstream response too large")
        return await reader.readexactly(int(lengths[0]))
    if b"chunked" in b",".join(response_values(head, b"transfer-encoding")).lower():
        try:
            return await read_chunked(reader, MAX_RESPONSE)
        except Refusal:
            raise Refusal(502, "upstream response invalid or too large") from None
    body = b""
    while len(body) <= MAX_RESPONSE:
        more = await reader.read(MAX_RESPONSE + 1 - len(body))
        if not more:
            return body
        body += more
    raise Refusal(502, "upstream response too large")


async def stream(source: asyncio.StreamReader, target: asyncio.StreamWriter) -> None:
    """Copy an event stream until it ends or stays silent for :data:`STREAM_IDLE`."""
    while True:
        piece = await asyncio.wait_for(source.read(_STREAM_CHUNK), STREAM_IDLE)
        if not piece:
            return
        target.write(piece)
        await target.drain()


def peer_certificate(writer: asyncio.StreamWriter) -> bytes | None:
    """Return the DER certificate the peer presented, or None without TLS."""
    tls = writer.get_extra_info("ssl_object")
    return tls.getpeercert(binary_form=True) if isinstance(tls, ssl.SSLObject) else None


async def open_upstream(state: GateState) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Connect to the listener and refuse it unless its certificate is the pinned one."""
    config = state.config
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(
            config.upstream_host,
            config.upstream_port,
            ssl=pinned_upstream_context(),
            server_hostname=config.upstream_host,
            limit=MAX_HEAD + 4,
        ),
        UPSTREAM_DEADLINE,
    )
    try:
        check_pin(peer_certificate(writer), config.upstream_cert_sha256)
    except Refusal:
        writer.close()
        raise
    return reader, writer


async def exchange(
    state: GateState, request: Request, body: bytes, client: asyncio.StreamWriter
) -> str:
    """Forward one authorised request and relay its response; return the seat name."""
    seat = resolve_seat(state.config, presented_bearer(request), now=state.clock())
    session = request.values(b"mcp-session-id")
    if session:
        state.sessions.check(session[0], seat.seat)
    try:
        token = read_seat_token(seat.token_file, now=state.clock())
    except SeatTokenUnavailable as exc:
        LOG.warning("seat token of %s unavailable: %s", seat.seat, exc)
        raise Refusal(503, "seat token unavailable") from None
    try:
        reader, writer = await open_upstream(state)
    except (TimeoutError, OSError):
        LOG.error("the listener at %s:%d is unavailable", *_upstream_address(state))
        raise Refusal(502, "upstream unavailable") from None
    try:
        writer.write(upstream_head(request, token, state.config.public_host, len(body)) + body)
        await writer.drain()
        head = await asyncio.wait_for(read_head(reader), UPSTREAM_DEADLINE)
        status = response_status(head)
        if status == 401:
            LOG.error("the listener refused the fresh token of %s; check the renewal", seat.seat)
            raise Refusal(503, "seat token refused upstream")
        opened = response_values(head, b"mcp-session-id")
        if opened:
            state.sessions.bind(opened[0], seat.seat)
        if session and request.method == b"DELETE" and 200 <= status < 300:
            state.sessions.forget(session[0])
        if b"text/event-stream" in b",".join(response_values(head, b"content-type")).lower():
            client.write(client_response_head(head, length=None))
            await stream(reader, client)
        else:
            reply = await asyncio.wait_for(read_response_body(reader, head), UPSTREAM_DEADLINE)
            client.write(client_response_head(head, length=len(reply)) + reply)
        await client.drain()
        return f"{seat.seat} {status}"
    finally:
        writer.close()


def _upstream_address(state: GateState) -> tuple[str, int]:
    return state.config.upstream_host, state.config.upstream_port


async def _drain(reader: asyncio.StreamReader) -> None:
    """Read what the client still sends, briefly and boundedly, so the close is clean."""
    with contextlib.suppress(OSError, asyncio.TimeoutError, asyncio.IncompleteReadError):
        await asyncio.wait_for(reader.read(MAX_BODY), 1.0)


async def handle(
    state: GateState, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> None:
    """Serve one connection: one request, then close."""
    if state.open_connections >= MAX_CONNECTIONS:
        LOG.warning("refused: too many connections")
        writer.close()
        return
    state.open_connections += 1
    try:
        try:
            head = await asyncio.wait_for(read_head(reader), HEAD_DEADLINE)
            if not head:
                return
            request = parse_request(head)
            body = await read_body(reader, request)
            outcome = await exchange(state, request, body, writer)
            path = request.target.split(b"?", 1)[0].decode("ascii", "replace")[:120]
            LOG.info("%s %s %s", request.method.decode("ascii"), path, outcome)
        except Refusal as refusal:
            if refusal.status == 401:
                await asyncio.sleep(state.throttle.record())
            LOG.info("refused %d: %s", refusal.status, refusal.reason)
            writer.write(error_response(refusal.status))
            await writer.drain()
            await _drain(reader)
        except (TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, OSError):
            return
    finally:
        state.open_connections -= 1
        writer.close()


def server_context(state: GateState) -> ssl.SSLContext:
    """Return the gate's TLS server context."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(state.config.tls_cert_file, state.config.tls_key_file)
    return context


async def serve_gate(
    state: GateState, *, started: Callable[[int], Awaitable[None]] | None = None
) -> None:
    """Serve until cancelled; ``started`` receives the bound port."""
    server = await asyncio.start_server(
        lambda reader, writer: handle(state, reader, writer),
        state.config.listen_host,
        state.config.listen_port,
        ssl=server_context(state),
        ssl_handshake_timeout=HEAD_DEADLINE,
        limit=MAX_HEAD + 4,
    )
    async with server:
        if started is not None:
            await started(server.sockets[0].getsockname()[1])
        await server.serve_forever()
