# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li — fetching half of the federation-bundle exchange

from __future__ import annotations

import asyncio
import contextlib
import json
import subprocess
from collections.abc import AsyncIterator, Sequence
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Any, cast

import pytest
from websockets.exceptions import ConnectionClosed

from hub_e2e_helpers import _await_listening, _free_port, running_hub
from synapse_channel.core.federation import FederationPeer, ScopeGrant
from synapse_channel.core.federation_fetch import (
    FederationFetchError,
    _live_certificate_pin,
    fetch_federation_offer,
    pinned_connector,
)
from synapse_channel.core.federation_store import peer_to_dict
from synapse_channel.core.federation_wire import bundle_fingerprint, encode_federation_offer
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.protocol import MAX_JSON_DEPTH, MessageType
from synapse_channel.core.tls import build_server_ssl_context, certificate_sha256_pin

_MATERIAL = FederationPeer(
    domain_id="lab-a",
    namespaces=frozenset({"lab-a/shared"}),
    certificate_pins=frozenset({"sha256:aa"}),
    signing_key_ids=frozenset({"key-1"}),
    scope_grants=(ScopeGrant("read_board", "lab-a/shared"),),
)


def _wire(frame: dict[str, Any]) -> str:
    """Serialise a frame the way the hub would put it on the wire."""
    return json.dumps(frame)


def _offer_frame(material: FederationPeer = _MATERIAL) -> str:
    """Build a serialised federation-offer reply frame."""
    return _wire({"type": MessageType.FEDERATION_OFFER, **encode_federation_offer(material)})


def _write_self_signed_cert(tmp_path: Path) -> tuple[Path, Path]:
    """Write a localhost self-signed certificate pair for WSS fetch tests."""
    certfile = tmp_path / "hub-cert.pem"
    keyfile = tmp_path / "hub-key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=localhost",
            "-addext",
            "subjectAltName=DNS:localhost,IP:127.0.0.1",
            "-keyout",
            str(keyfile),
            "-out",
            str(certfile),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return certfile, keyfile


class _FakeSocket:
    """A scripted connection: returns queued frames, records what was sent."""

    def __init__(self, frames: Sequence[str | bytes | BaseException]) -> None:
        self._frames = list(frames)
        self.sent: list[str] = []

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def recv(self) -> str | bytes:
        if not self._frames:
            raise ConnectionClosed(None, None)
        nxt = self._frames.pop(0)
        if isinstance(nxt, BaseException):
            raise nxt
        return nxt


class _HangingSocket:
    """A connection whose receive never completes, to drive the fetch timeout."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def recv(self) -> str | bytes:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")  # pragma: no cover


class _FakeTransport:
    """Transport facade exposing one scripted TLS object."""

    def __init__(self, ssl_object: object) -> None:
        self._ssl_object = ssl_object

    def get_extra_info(self, name: str, default: object = None) -> object:
        """Return the scripted TLS object for ``ssl_object`` requests."""
        return self._ssl_object if name == "ssl_object" else default


class _FakePinnedSocket:
    """Socket facade with transport metadata for pin-inspection failures."""

    def __init__(self, ssl_object: object) -> None:
        self.transport = _FakeTransport(ssl_object)

    async def send(self, message: str) -> None:
        """Satisfy the socket protocol; pin inspection never sends."""

    async def recv(self) -> str | bytes:
        """Satisfy the socket protocol; pin inspection never receives."""
        return ""


class _FakePeerCertificate:
    """TLS object facade returning scripted certificate bytes."""

    def __init__(self, certificate: object) -> None:
        self._certificate = certificate

    def getpeercert(self, binary_form: bool = False) -> object:
        """Return the scripted certificate when binary form is requested."""
        return self._certificate if binary_form else {}


def _connector(socket: Any, *, opened: list[str] | None = None) -> Any:
    """Return an injectable connector yielding ``socket`` and recording opened URIs."""

    @contextlib.asynccontextmanager
    async def _open(_uri: str) -> AsyncIterator[Any]:
        if opened is not None:
            opened.append(_uri)
        yield socket

    def factory(uri: str) -> AbstractAsyncContextManager[Any]:
        return _open(uri)

    return factory


# --- happy path --------------------------------------------------------------------------


async def test_fetch_returns_the_offer_and_sends_the_request() -> None:
    socket = _FakeSocket([_wire({"type": "welcome"}), _offer_frame()])
    opened: list[str] = []
    fetched = await fetch_federation_offer(
        "ws://peer:1/", local_id="ops", connector=_connector(socket, opened=opened)
    )
    assert fetched == _MATERIAL
    assert bundle_fingerprint(fetched) == bundle_fingerprint(_MATERIAL)
    assert opened == ["ws://peer:1/"]
    request = json.loads(socket.sent[0])
    assert request["type"] == MessageType.FEDERATION_OFFER_REQUEST
    assert request["sender"] == "ops"
    assert "token" not in request


async def test_fetch_skips_unrelated_frames_and_decodes_a_bytes_offer() -> None:
    frames: list[str | bytes | BaseException] = [
        _wire({"type": "presence_update", "agent": "x"}),
        _offer_frame().encode("utf-8"),
    ]
    fetched = await fetch_federation_offer(
        "ws://peer:1/", local_id="ops", connector=_connector(_FakeSocket(frames))
    )
    assert fetched == _MATERIAL


async def test_fetch_carries_the_token_on_the_request_frame() -> None:
    socket = _FakeSocket([_offer_frame()])
    await fetch_federation_offer(
        "ws://peer:1/", local_id="ops", token="secret", connector=_connector(socket)
    )
    assert json.loads(socket.sent[0])["token"] == "secret"


# --- failure modes ------------------------------------------------------------------------


async def test_an_error_frame_fails_the_fetch_with_the_refusal() -> None:
    refusal = _wire({"type": "error", "payload": "No federation offer is configured on this hub."})
    with pytest.raises(FederationFetchError, match="refused the federation-offer request"):
        await fetch_federation_offer(
            "ws://peer:1/", local_id="ops", connector=_connector(_FakeSocket([refusal]))
        )


async def test_a_malformed_offer_fails_the_fetch() -> None:
    frame = _wire({"type": MessageType.FEDERATION_OFFER, "namespaces": "not-a-list"})
    with pytest.raises(FederationFetchError, match="federation-offer fetch"):
        await fetch_federation_offer(
            "ws://peer:1/", local_id="ops", connector=_connector(_FakeSocket([frame]))
        )


async def test_a_dropped_connection_fails_the_fetch() -> None:
    with pytest.raises(FederationFetchError, match="federation-offer fetch"):
        await fetch_federation_offer(
            "ws://peer:1/", local_id="ops", connector=_connector(_FakeSocket([]))
        )


async def test_a_refused_connection_fails_the_fetch() -> None:
    @contextlib.asynccontextmanager
    async def _refuse(_uri: str) -> AsyncIterator[Any]:
        raise OSError("connection refused")
        yield  # pragma: no cover

    def factory(uri: str) -> AbstractAsyncContextManager[Any]:
        return _refuse(uri)

    with pytest.raises(FederationFetchError, match="federation-offer fetch"):
        await fetch_federation_offer("ws://peer:1/", local_id="ops", connector=factory)


async def test_a_silent_peer_times_the_fetch_out() -> None:
    with pytest.raises(FederationFetchError, match="federation-offer fetch"):
        await fetch_federation_offer(
            "ws://peer:1/", local_id="ops", timeout=0.05, connector=_connector(_HangingSocket())
        )


async def test_a_non_object_frame_fails_the_fetch() -> None:
    with pytest.raises(FederationFetchError, match="not a JSON object"):
        await fetch_federation_offer(
            "ws://peer:1/", local_id="ops", connector=_connector(_FakeSocket(['["array"]']))
        )


async def test_a_deeply_nested_frame_fails_the_fetch_bounded() -> None:
    hostile = "[" * (MAX_JSON_DEPTH + 1) + "]" * (MAX_JSON_DEPTH + 1)
    with pytest.raises(FederationFetchError, match="federation-offer fetch"):
        await fetch_federation_offer(
            "ws://peer:1/", local_id="ops", connector=_connector(_FakeSocket([hostile]))
        )


# --- against a real serving hub -----------------------------------------------------------


async def test_fetches_a_real_hub_offer_end_to_end(tmp_path: Path) -> None:
    offer = tmp_path / "offer.json"
    offer.write_text(json.dumps(peer_to_dict(_MATERIAL)), encoding="utf-8")
    hub = SynapseHub(hub_id="syn-a", federation_offer_path=offer)
    async with running_hub(hub) as (_, uri):
        fetched = await fetch_federation_offer(uri, local_id="peer-ops")
    assert fetched == _MATERIAL


async def test_a_real_unconfigured_hub_refuses_the_fetch() -> None:
    hub = SynapseHub(hub_id="syn-a")
    async with running_hub(hub) as (_, uri):
        with pytest.raises(FederationFetchError, match="refused the federation-offer request"):
            await fetch_federation_offer(uri, local_id="peer-ops")


async def test_pinned_fetch_accepts_a_self_signed_wss_peer(tmp_path: Path) -> None:
    offer = tmp_path / "offer.json"
    offer.write_text(json.dumps(peer_to_dict(_MATERIAL)), encoding="utf-8")
    certfile, keyfile = _write_self_signed_cert(tmp_path)
    server_context = build_server_ssl_context(certfile=certfile, keyfile=keyfile)
    pin = certificate_sha256_pin(certfile)
    hub = SynapseHub(hub_id="syn-pinned", federation_offer_path=offer)
    port = _free_port()
    task = asyncio.create_task(hub.serve("localhost", port, ssl_context=server_context))
    try:
        await _await_listening(port)
        fetched = await fetch_federation_offer(
            f"wss://localhost:{port}",
            local_id="peer-ops",
            connector=pinned_connector(pin),
        )
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert fetched == _MATERIAL


async def test_pinned_fetch_rejects_a_mismatched_certificate_pin(tmp_path: Path) -> None:
    offer = tmp_path / "offer.json"
    offer.write_text(json.dumps(peer_to_dict(_MATERIAL)), encoding="utf-8")
    certfile, keyfile = _write_self_signed_cert(tmp_path)
    server_context = build_server_ssl_context(certfile=certfile, keyfile=keyfile)
    hub = SynapseHub(hub_id="syn-pinned", federation_offer_path=offer)
    port = _free_port()
    task = asyncio.create_task(hub.serve("localhost", port, ssl_context=server_context))
    try:
        await _await_listening(port)
        with pytest.raises(FederationFetchError, match="certificate pin mismatch"):
            await fetch_federation_offer(
                f"wss://localhost:{port}",
                local_id="peer-ops",
                connector=pinned_connector("sha256:" + "0" * 64),
            )
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_pinned_fetch_requires_a_wss_uri() -> None:
    with pytest.raises(FederationFetchError, match="requires a wss://"):
        await fetch_federation_offer(
            "ws://localhost:1",
            local_id="peer-ops",
            connector=pinned_connector("sha256:" + "0" * 64),
        )


def test_live_certificate_pin_rejects_missing_tls_object() -> None:
    with pytest.raises(FederationFetchError, match="not TLS"):
        _live_certificate_pin(cast(Any, _FakePinnedSocket(None)))


def test_live_certificate_pin_rejects_absent_peer_certificate() -> None:
    with pytest.raises(FederationFetchError, match="did not present"):
        _live_certificate_pin(cast(Any, _FakePinnedSocket(_FakePeerCertificate(None))))


def test_live_certificate_pin_rejects_unparseable_peer_certificate() -> None:
    with pytest.raises(FederationFetchError, match="cannot be pinned"):
        _live_certificate_pin(
            cast(Any, _FakePinnedSocket(_FakePeerCertificate(b"not a certificate")))
        )
