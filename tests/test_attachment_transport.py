# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — reject corrupted responses over real attachment hub connections
"""Forward a real source hub's frames through an adversarial socket boundary."""

from __future__ import annotations

import asyncio
import contextlib
import json
import ssl
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import ServerConnection, serve

from hub_e2e_helpers import running_hub
from synapse_channel.core.attachment_store import MAX_CHUNK_BYTES, AttachmentError
from synapse_channel.core.attachment_transport import request_attachment
from test_attachment_peer_e2e import DIGEST, RECIPIENT, SCOPE, SOURCE, Source
from test_attachment_peer_e2e import source as source

pytestmark = pytest.mark.real_hub


@contextlib.asynccontextmanager
async def _proxy(
    upstream_uri: str, corrupt: Callable[[dict[str, Any]], str | bytes | None]
) -> AsyncIterator[str]:
    async def handle(downstream: ServerConnection) -> None:
        async with connect(upstream_uri) as upstream:

            async def requests() -> None:
                async for raw in downstream:
                    await upstream.send(raw)

            async def replies() -> None:
                async for raw in upstream:
                    frame = json.loads(raw)
                    altered = corrupt(frame)
                    if altered is not None:
                        await downstream.send(altered)

            tasks = [asyncio.create_task(requests()), asyncio.create_task(replies())]
            try:
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    async with serve(handle, "localhost", 0) as server:
        port = server.sockets[0].getsockname()[1]
        yield f"ws://localhost:{port}"


async def _request(
    uri: str, fixture: Source, action: str = "info", **fields: Any
) -> dict[str, Any]:
    return await request_attachment(
        action,
        uri=uri,
        local_id=RECIPIENT,
        source_hub_id=SOURCE,
        scope=SCOPE,
        digest=DIGEST,
        token="test-token",
        signer=fixture.signer,
        **fields,
    )


@pytest.mark.parametrize(
    ("action", "field", "value"),
    [
        ("info", "metadata", None),
        ("info", "metadata", {}),
        *[
            ("info", "metadata." + field, value)
            for field, value in (
                ("scope", "OTHER"),
                ("digest", "b" * 64),
                ("length", True),
                ("length", -1),
                ("length", 8 * 1024 * 1024 + 1),
                ("expires_at", True),
                ("expires_at", "forever"),
                ("expires_at", float("inf")),
                ("expires_at", 10**400),
                ("media_type", []),
                ("provenance", {}),
            )
        ],
        ("read", "scope", "OTHER"),
        ("read", "digest", "b" * 64),
        ("read", "offset", True),
        ("read", "offset", 1),
        ("read", "eof", 1),
        ("read", "body", None),
        ("read", "body", "a" * (4 * ((MAX_CHUNK_BYTES + 2) // 3) + 1)),
        ("read", "body", "!not-base64!"),
        ("read", "body", ""),
        ("read", "body", "AAAA" * ((MAX_CHUNK_BYTES // 3) + 1)),
        ("info", "target", "wrong-recipient"),
        ("info", "hub_id", "wrong-source"),
    ],
)
async def test_corrupted_source_answers_are_rejected(
    source: Source, action: str, field: str, value: object
) -> None:
    def corrupt(frame: dict[str, Any]) -> str:
        if frame.get("type") == "attachment_peer_result":
            if field.startswith("metadata."):
                frame["metadata"][field.split(".")[1]] = value
            else:
                frame[field] = value
        return json.dumps(frame)

    async with running_hub(source.hub) as (_, upstream):
        async with _proxy(upstream, corrupt) as uri:
            with pytest.raises(
                AttachmentError,
                match="invalid attachment source response|attachment source request failed",
            ):
                await _request(uri, source, action, offset=0 if action == "read" else None)


@pytest.mark.parametrize("raw", ["[]", "{bad-json", b"\xff", b"[]"])
async def test_malformed_response_envelopes_fail_closed(source: Source, raw: str | bytes) -> None:
    def corrupt(frame: dict[str, Any]) -> str | bytes:
        return raw if frame.get("type") == "attachment_peer_result" else json.dumps(frame)

    async with running_hub(source.hub) as (_, upstream):
        async with _proxy(upstream, corrupt) as uri:
            with pytest.raises(AttachmentError):
                await _request(uri, source)


@pytest.mark.parametrize("version", [5, True, None, "6"])
async def test_negotiation_rejects_old_or_malformed_welcome(
    source: Source, version: object
) -> None:
    def corrupt(frame: dict[str, Any]) -> str:
        if frame.get("type") == "welcome":
            frame["protocol_version"] = version
        return json.dumps(frame)

    async with running_hub(source.hub) as (_, upstream):
        async with _proxy(upstream, corrupt) as uri:
            with pytest.raises(AttachmentError, match="protocol version six required"):
                await _request(uri, source)


async def test_binary_results_and_connection_failures(source: Source) -> None:
    async with running_hub(source.hub) as (_, upstream):
        async with _proxy(upstream, lambda frame: json.dumps(frame).encode()) as uri:
            result = await _request(uri, source)
            assert result["digest"] == DIGEST
        async with _proxy(upstream, lambda frame: None) as uri:
            with pytest.raises(AttachmentError, match="timed out"):
                await _request(uri, source, timeout=0.05)
    with pytest.raises(AttachmentError, match="failed"):
        await _request("ws://localhost:1", source)


@pytest.mark.parametrize(
    ("action", "fields"),
    [
        ("write", {}),
        ("info", {"offset": 0}),
        ("read", {}),
        ("read", {"offset": -1}),
        ("read", {"offset": True}),
        ("read", {"offset": 8 * 1024 * 1024 + 1}),
        ("info", {"timeout": 0}),
        ("info", {"timeout": float("nan")}),
    ],
)
async def test_invalid_requests_fail_before_connect(
    source: Source, action: str, fields: dict[str, Any]
) -> None:
    with pytest.raises(AttachmentError, match="invalid attachment peer request"):
        await _request("ws://localhost:1", source, action, **fields)


async def test_source_error_is_fixed_and_never_echoes_received_text(source: Source) -> None:
    def corrupt(frame: dict[str, Any]) -> str:
        if frame.get("type") == "attachment_peer_result":
            frame.update(type="error", payload="private-server-path-and-interpreter-error")
        return json.dumps(frame)

    async with running_hub(source.hub) as (_, upstream):
        async with _proxy(upstream, corrupt) as uri:
            with pytest.raises(AttachmentError, match="^attachment source refused the connection$"):
                await _request(uri, source)
    with pytest.raises(AttachmentError, match="^attachment source request failed$"):
        await _request("not-a-websocket-uri", source)


@pytest.mark.parametrize("uri", ["ws://example.com:1", "ws://192.0.2.1:1", "ws://[2001:db8::1]:1"])
async def test_non_loopback_plaintext_is_refused_before_credentials(
    source: Source, uri: str
) -> None:
    with pytest.raises(AttachmentError, match="requires TLS"):
        await _request(uri, source)


async def test_loopback_numeric_address_keeps_local_transport(source: Source) -> None:
    with pytest.raises(AttachmentError, match="attachment source request failed"):
        await _request("ws://127.0.0.1:1", source)


@pytest.mark.parametrize("disable_ca", [False, True])
async def test_unverified_tls_context_requires_source_pin(source: Source, disable_ca: bool) -> None:
    context = ssl.create_default_context()
    context.check_hostname = False
    if disable_ca:
        context.verify_mode = ssl.CERT_NONE
    with pytest.raises(AttachmentError, match="certificate verification or a pin"):
        await _request("wss://localhost:1", source, ssl_context=context)


async def test_certificate_pin_requires_tls_and_canonical_digest(source: Source) -> None:
    for pin in ("wrong-format", "sha256:UPPERCASE", 123):
        with pytest.raises(AttachmentError):
            await _request("wss://localhost:1", source, source_certificate_pin=pin)
    with pytest.raises(AttachmentError, match="requires TLS"):
        await _request("ws://localhost:1", source, source_certificate_pin="sha256:" + "a" * 64)
