# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — federation diagnostics behind `synapse doctor`

from __future__ import annotations

import builtins
import contextlib
import json
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from websockets.asyncio.client import connect

from hub_e2e_helpers import read_until_type, running_hub, send_json
from synapse_channel.cli_doctor_federation import (
    FederationDoctorError,
    FederationDoctorPeer,
    FederationPeerProbe,
    _finite_timestamp,
    _load_der_certificate,
    _peer_certificate_expires_at,
    diagnose_federation,
    parse_cursor_specs,
    parse_peer_specs,
    probe_federation_peer,
)
from synapse_channel.client.diagnostics import Diagnosis
from synapse_channel.core.federation import FederationPeer, ScopeGrant
from synapse_channel.core.federation_store import FederationRecord, PeerProvenance, save_store
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.multihub_wire import LogSnapshot
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType


class _FakeSocket:
    """Scripted websocket for probe tests."""

    def __init__(self, frames: list[str | bytes | BaseException]) -> None:
        self.frames = frames
        self.sent: list[str] = []

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def recv(self) -> str | bytes:
        frame = self.frames.pop(0)
        if isinstance(frame, BaseException):
            raise frame
        return frame


def _connector(socket: _FakeSocket) -> Any:
    """Return a connector yielding ``socket`` for one probe."""

    @contextlib.asynccontextmanager
    async def open_socket(_uri: str) -> AsyncIterator[_FakeSocket]:
        yield socket

    return open_socket


def _record(
    domain_id: str, *, expires_at: float | None = None, revoked: bool = False
) -> FederationRecord:
    """Build a federation record for store diagnostics."""
    return FederationRecord(
        peer=FederationPeer(
            domain_id=domain_id,
            namespaces=frozenset({"SYNAPSE-CHANNEL"}),
            certificate_pins=frozenset({"sha256:" + "a" * 64}),
            signing_key_ids=frozenset({"key-1"}),
            scope_grants=(ScopeGrant(verb="read", namespace="SYNAPSE-CHANNEL"),),
            expires_at=expires_at,
            revoked=revoked,
        ),
        provenance=PeerProvenance(source="fixture", imported_at=1.0, confirmed_by="operator"),
    )


def test_parse_peer_specs_returns_peers_and_failures() -> None:
    peers, diagnoses = parse_peer_specs(["alpha=ws://hub", "broken", "=ws://empty", "beta="])

    assert peers == [FederationDoctorPeer(peer_id="alpha", uri="ws://hub")]
    assert [diagnosis.status for diagnosis in diagnoses] == ["fail", "fail", "fail"]


def test_parse_cursor_specs_returns_non_negative_cursors_and_failures() -> None:
    cursors, diagnoses = parse_cursor_specs(["alpha=7", "beta=-1", "gamma=nope", "=3"])

    assert cursors == {"alpha": 7}
    assert [diagnosis.status for diagnosis in diagnoses] == ["fail", "fail", "fail"]


async def test_diagnose_federation_reports_store_states(tmp_path: Path) -> None:
    store = tmp_path / "federation.json"
    now = 1_000.0
    save_store(
        store,
        [
            _record("active", expires_at=now + 60.0 * 86_400.0),
            _record("near", expires_at=now + 5.0 * 86_400.0),
            _record("old", expires_at=now - 2.0 * 86_400.0),
            _record("revoked", revoked=True),
            _record("open"),
        ],
    )

    diagnoses = await diagnose_federation(
        peer_specs=(),
        cursor_specs=(),
        local_id="doctor",
        token=None,
        store_path=store,
        now=lambda: now,
    )

    by_check = {diagnosis.check: diagnosis for diagnosis in diagnoses}
    assert by_check["federation-bundle:active"].status == "pass"
    assert by_check["federation-bundle:near"].status == "warn"
    assert by_check["federation-bundle:old"].status == "fail"
    assert by_check["federation-bundle:revoked"].status == "warn"
    assert by_check["federation-bundle:open"].status == "pass"


async def test_diagnose_federation_fails_on_malformed_store(tmp_path: Path) -> None:
    store = tmp_path / "bad.json"
    store.write_text("{not-json", encoding="utf-8")

    diagnoses = await diagnose_federation(
        peer_specs=(),
        cursor_specs=(),
        local_id="doctor",
        token=None,
        store_path=store,
    )

    assert diagnoses[0].check == "federation-store"
    assert diagnoses[0].status == "fail"


async def test_diagnose_federation_reports_peer_lag_skew_and_revocation(
    tmp_path: Path,
) -> None:
    store = tmp_path / "federation.json"
    save_store(store, [_record("alpha", revoked=True)])
    calls: list[tuple[FederationDoctorPeer, int, str, str | None]] = []

    async def probe(
        peer: FederationDoctorPeer, cursor: int, local_id: str, token: str | None
    ) -> FederationPeerProbe:
        calls.append((peer, cursor, local_id, token))
        return FederationPeerProbe(
            snapshot=LogSnapshot(events=(), next_cursor=cursor, log_end_seq=12),
            peer_timestamp=90.0,
            clock_skew_seconds=10.0,
            certificate_expires_at=1_000.0 + 2.0 * 86_400.0,
        )

    diagnoses = await diagnose_federation(
        peer_specs=("alpha=ws://peer",),
        cursor_specs=("alpha=5",),
        local_id="local-doctor",
        token="secret",
        store_path=store,
        skew_warn_seconds=5.0,
        cert_warn_days=30,
        probe=probe,
        now=lambda: 1_000.0,
    )

    peer_diagnosis = next(d for d in diagnoses if d.check == "federation-peer:alpha")
    assert calls == [
        (FederationDoctorPeer(peer_id="alpha", uri="ws://peer"), 5, "local-doctor", "secret")
    ]
    assert peer_diagnosis.status == "fail"
    assert "lag=7" in peer_diagnosis.detail
    assert "clock_skew=+10.000s" in peer_diagnosis.detail
    assert "bundle_revoked=true" in peer_diagnosis.detail


async def test_diagnose_federation_passes_with_low_skew_and_far_certificate() -> None:
    async def probe(
        peer: FederationDoctorPeer, cursor: int, local_id: str, token: str | None
    ) -> FederationPeerProbe:
        return FederationPeerProbe(
            snapshot=LogSnapshot(events=(), next_cursor=cursor, log_end_seq=cursor),
            peer_timestamp=98.0,
            clock_skew_seconds=2.0,
            certificate_expires_at=1_000.0 + 60.0 * 86_400.0,
        )

    diagnoses = await diagnose_federation(
        peer_specs=("alpha=ws://peer",),
        cursor_specs=("alpha=5",),
        local_id="doctor",
        token=None,
        store_path=None,
        skew_warn_seconds=5.0,
        cert_warn_days=30,
        probe=probe,
        now=lambda: 1_000.0,
    )

    assert diagnoses == [
        Diagnosis(
            check="federation-peer:alpha",
            status="pass",
            detail=(
                "ws://peer answered; cursor=5, log_end=5, lag=0; "
                "clock_skew=+2.000s; tls_cert_expires_in=60.0d"
            ),
        )
    ]


async def test_diagnose_federation_fails_on_expired_peer_certificate() -> None:
    async def probe(
        peer: FederationDoctorPeer, cursor: int, local_id: str, token: str | None
    ) -> FederationPeerProbe:
        return FederationPeerProbe(
            snapshot=LogSnapshot(events=(), next_cursor=cursor, log_end_seq=9),
            peer_timestamp=100.0,
            clock_skew_seconds=0.0,
            certificate_expires_at=900.0,
        )

    diagnoses = await diagnose_federation(
        peer_specs=("alpha=ws://peer",),
        cursor_specs=("alpha=10",),
        local_id="doctor",
        token=None,
        store_path=None,
        probe=probe,
        now=lambda: 1_000.0,
    )

    peer_diagnosis = diagnoses[0]
    assert peer_diagnosis.status == "fail"
    assert "lag=0" in peer_diagnosis.detail
    assert (
        peer_diagnosis.remedy
        == "rotate the peer TLS certificate before federation traffic is trusted"
    )


async def test_diagnose_federation_warns_on_near_peer_certificate() -> None:
    async def probe(
        peer: FederationDoctorPeer, cursor: int, local_id: str, token: str | None
    ) -> FederationPeerProbe:
        return FederationPeerProbe(
            snapshot=LogSnapshot(events=(), next_cursor=cursor, log_end_seq=9),
            peer_timestamp=100.0,
            clock_skew_seconds=0.0,
            certificate_expires_at=1_000.0 + 2.0 * 86_400.0,
        )

    diagnoses = await diagnose_federation(
        peer_specs=("alpha=ws://peer",),
        cursor_specs=(),
        local_id="doctor",
        token=None,
        store_path=None,
        cert_warn_days=30,
        probe=probe,
        now=lambda: 1_000.0,
    )

    assert diagnoses[0].status == "warn"
    assert diagnoses[0].remedy == "schedule TLS certificate rotation"


async def test_diagnose_federation_warns_when_peer_omits_lag_and_timestamp() -> None:
    async def probe(
        peer: FederationDoctorPeer, cursor: int, local_id: str, token: str | None
    ) -> FederationPeerProbe:
        return FederationPeerProbe(
            snapshot=LogSnapshot(events=(), next_cursor=cursor),
            peer_timestamp=None,
            clock_skew_seconds=None,
            certificate_expires_at=None,
        )

    diagnoses = await diagnose_federation(
        peer_specs=("alpha=ws://peer",),
        cursor_specs=(),
        local_id="doctor",
        token=None,
        store_path=None,
        probe=probe,
    )

    assert diagnoses == [
        Diagnosis(
            check="federation-peer:alpha",
            status="warn",
            detail="ws://peer answered; cursor lag unknown: peer omitted log_end_seq; "
            "clock_skew=unknown",
            remedy="upgrade the peer so multi-hub snapshots include log_end_seq",
        )
    ]


async def test_diagnose_federation_fails_when_probe_fails() -> None:
    async def probe(
        peer: FederationDoctorPeer, cursor: int, local_id: str, token: str | None
    ) -> FederationPeerProbe:
        raise FederationDoctorError("no route")

    diagnoses = await diagnose_federation(
        peer_specs=("alpha=ws://peer",),
        cursor_specs=(),
        local_id="doctor",
        token=None,
        store_path=None,
        probe=probe,
    )

    assert diagnoses[0].check == "federation-peer:alpha"
    assert diagnoses[0].status == "fail"
    assert "no route" in diagnoses[0].detail


async def test_probe_federation_peer_reads_welcome_skew_snapshot_and_token() -> None:
    socket = _FakeSocket(
        [
            json.dumps({"type": MessageType.WELCOME, "timestamp": 100.0}),
            json.dumps(
                {
                    "type": MessageType.MULTIHUB_LOG_SNAPSHOT,
                    "events": [],
                    "next_cursor": 4,
                    "log_end_seq": 9,
                }
            ).encode("utf-8"),
        ]
    )

    observed = await probe_federation_peer(
        FederationDoctorPeer(peer_id="alpha", uri="ws://peer"),
        cursor=4,
        local_id="doctor",
        token="secret",
        connector=_connector(socket),
        clock=lambda: 103.0,
    )

    sent = json.loads(socket.sent[0])
    assert sent["token"] == "secret"
    assert sent["after_seq"] == 4
    assert observed.snapshot.log_end_seq == 9
    assert observed.clock_skew_seconds == 3.0
    assert observed.peer_timestamp == 100.0


async def test_probe_federation_peer_fails_on_peer_error_frame() -> None:
    socket = _FakeSocket([json.dumps({"type": MessageType.ERROR, "payload": "denied"})])

    with pytest.raises(FederationDoctorError, match="refused"):
        await probe_federation_peer(
            FederationDoctorPeer(peer_id="alpha", uri="ws://peer"),
            cursor=0,
            local_id="doctor",
            token=None,
            connector=_connector(socket),
        )


async def test_probe_federation_peer_wraps_socket_errors() -> None:
    socket = _FakeSocket([OSError("network down")])

    with pytest.raises(FederationDoctorError, match="network down"):
        await probe_federation_peer(
            FederationDoctorPeer(peer_id="alpha", uri="ws://peer"),
            cursor=0,
            local_id="doctor",
            token=None,
            connector=_connector(socket),
        )


async def test_probe_federation_peer_rejects_non_object_frames() -> None:
    socket = _FakeSocket(["[]"])

    with pytest.raises(FederationDoctorError, match="not a JSON object"):
        await probe_federation_peer(
            FederationDoctorPeer(peer_id="alpha", uri="ws://peer"),
            cursor=0,
            local_id="doctor",
            token=None,
            connector=_connector(socket),
        )


async def test_probe_federation_peer_ignores_unusable_welcome_timestamp() -> None:
    socket = _FakeSocket(
        [
            json.dumps({"type": MessageType.WELCOME, "timestamp": True}),
            json.dumps(
                {
                    "type": MessageType.MULTIHUB_LOG_SNAPSHOT,
                    "events": [],
                    "next_cursor": 0,
                    "log_end_seq": 0,
                }
            ),
        ]
    )

    observed = await probe_federation_peer(
        FederationDoctorPeer(peer_id="alpha", uri="ws://peer"),
        cursor=0,
        local_id="doctor",
        token=None,
        connector=_connector(socket),
    )

    assert observed.peer_timestamp is None
    assert observed.clock_skew_seconds is None


async def test_probe_federation_peer_reports_log_end_seq_from_real_hub(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    hub = SynapseHub(hub_id="syn-a", journal=store)
    try:
        async with running_hub(hub) as (_, uri):
            async with connect(uri) as writer:
                await read_until_type(writer, MessageType.WELCOME)
                await send_json(writer, sender="writer", type=MessageType.HEARTBEAT)
                for index in range(3):
                    await send_json(
                        writer, sender="writer", type=MessageType.CHAT, payload=f"m{index}"
                    )
                    await read_until_type(writer, MessageType.CHAT)
            observed = await probe_federation_peer(
                FederationDoctorPeer(peer_id="alpha", uri=uri),
                cursor=1,
                local_id="doctor",
                token=None,
            )
    finally:
        store.close()

    assert observed.snapshot.events == ()
    assert observed.snapshot.next_cursor == 1
    assert observed.snapshot.log_end_seq == 3
    assert observed.clock_skew_seconds is not None


def test_finite_timestamp_rejects_unusable_values() -> None:
    assert _finite_timestamp(True) is None
    assert _finite_timestamp(object()) is None
    assert _finite_timestamp("nan") is None
    assert _finite_timestamp("not-a-time") is None


def test_peer_certificate_expiry_is_read_from_tls_object() -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    expires = datetime.now(timezone.utc) + timedelta(days=9)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "peer")]))
        .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "peer")]))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(timezone.utc) - timedelta(minutes=1))
        .not_valid_after(expires)
        .sign(key, hashes.SHA256())
    )

    class _TLS:
        def getpeercert(self, binary_form: bool = False) -> object:
            return certificate.public_bytes(serialization.Encoding.DER) if binary_form else None

    class _Transport:
        def get_extra_info(self, name: str, default: object = None) -> object:
            return _TLS() if name == "ssl_object" else default

    class _SocketWithTransport:
        transport = _Transport()

        async def send(self, message: str) -> None:
            raise AssertionError(message)

        async def recv(self) -> str | bytes:
            raise AssertionError("unused")

    assert _peer_certificate_expires_at(_SocketWithTransport()) == pytest.approx(
        expires.timestamp(), abs=1.0
    )


def test_peer_certificate_expiry_handles_absent_or_non_binary_tls() -> None:
    class _NoExtraInfo:
        pass

    class _NoTLS:
        def get_extra_info(self, name: str, default: object = None) -> object:
            return default

    class _TextTLS:
        def getpeercert(self, binary_form: bool = False) -> object:
            return "not-der"

    class _TextTransport:
        def get_extra_info(self, name: str, default: object = None) -> object:
            return _TextTLS() if name == "ssl_object" else default

    class _SocketNoExtraInfo:
        transport = _NoExtraInfo()

        async def send(self, message: str) -> None:
            raise AssertionError(message)

        async def recv(self) -> str | bytes:
            raise AssertionError("unused")

    class _SocketNoTLS:
        transport = _NoTLS()

        async def send(self, message: str) -> None:
            raise AssertionError(message)

        async def recv(self) -> str | bytes:
            raise AssertionError("unused")

    class _SocketTextTLS:
        transport = _TextTransport()

        async def send(self, message: str) -> None:
            raise AssertionError(message)

        async def recv(self) -> str | bytes:
            raise AssertionError("unused")

    assert _peer_certificate_expires_at(_SocketNoExtraInfo()) is None
    assert _peer_certificate_expires_at(_SocketNoTLS()) is None
    assert _peer_certificate_expires_at(_SocketTextTLS()) is None


def test_peer_certificate_expiry_is_none_when_certificate_parser_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _TLS:
        def getpeercert(self, binary_form: bool = False) -> object:
            return b"der"

    class _Transport:
        def get_extra_info(self, name: str, default: object = None) -> object:
            return _TLS() if name == "ssl_object" else default

    class _SocketWithUnparsedCertificate:
        transport = _Transport()

        async def send(self, message: str) -> None:
            raise AssertionError(message)

        async def recv(self) -> str | bytes:
            raise AssertionError("unused")

    monkeypatch.setattr(
        "synapse_channel.cli_doctor_federation._load_der_certificate", lambda _der: None
    )

    assert _peer_certificate_expires_at(_SocketWithUnparsedCertificate()) is None


def test_load_der_certificate_returns_none_without_cryptography(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_import = builtins.__import__

    def import_without_cryptography(
        name: str,
        globals_: dict[str, object] | None = None,
        locals_: dict[str, object] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> object:
        if name == "cryptography":
            raise ImportError(name)
        return real_import(name, globals_, locals_, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", import_without_cryptography)

    assert _load_der_certificate(b"der") is None


def test_peer_certificate_expiry_supports_legacy_cryptography_datetime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expires = datetime(2026, 7, 9, tzinfo=timezone.utc)

    class _LegacyCertificate:
        not_valid_after = expires.replace(tzinfo=None)

    class _TLS:
        def getpeercert(self, binary_form: bool = False) -> object:
            return b"der"

    class _Transport:
        def get_extra_info(self, name: str, default: object = None) -> object:
            return _TLS() if name == "ssl_object" else default

    class _SocketWithLegacyCert:
        transport = _Transport()

        async def send(self, message: str) -> None:
            raise AssertionError(message)

        async def recv(self) -> str | bytes:
            raise AssertionError("unused")

    monkeypatch.setattr(
        "synapse_channel.cli_doctor_federation._load_der_certificate",
        lambda _der: _LegacyCertificate(),
    )

    assert _peer_certificate_expires_at(_SocketWithLegacyCert()) == expires.timestamp()
