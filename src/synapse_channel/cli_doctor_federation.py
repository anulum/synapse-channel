# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — federation diagnostics for the `synapse doctor` command
"""Federation diagnostics used by ``synapse doctor``.

The main doctor command checks local identity, hub reachability, and waiter
presence. This module adds an opt-in peer layer for federated deployments:
operators name the peers to inspect, and the checks use the same multi-hub log
request path production followers use. No peer is inferred from ambient state.
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import timezone
from pathlib import Path
from typing import Any, Protocol, cast

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from synapse_channel.client.diagnostics import Diagnosis, DoctorStatus
from synapse_channel.core.federation_store import FederationRecord, FederationStoreError, load_store
from synapse_channel.core.multihub_wire import (
    LogRequest,
    LogSnapshot,
    MultiHubWireError,
    decode_log_snapshot,
    encode_log_request,
)
from synapse_channel.core.protocol import MessageType, build_envelope, loads_bounded

SECONDS_PER_DAY = 86_400.0
"""Seconds in one day, used for certificate and bundle warning windows."""

DEFAULT_FEDERATION_SKEW_WARN_SECONDS = 5.0
"""Clock-skew warning threshold for peer welcome timestamps."""

DEFAULT_FEDERATION_CERT_WARN_DAYS = 30
"""Days before certificate or bundle expiry at which doctor warns."""


class FederationDoctorError(RuntimeError):
    """Raised when a federation peer probe fails before a snapshot is decoded."""


@dataclass(frozen=True, slots=True)
class FederationDoctorPeer:
    """Peer hub named for a doctor federation check.

    Attributes
    ----------
    peer_id : str
        Operator-facing peer id.
    uri : str
        Peer hub websocket URI.
    """

    peer_id: str
    uri: str


@dataclass(frozen=True, slots=True)
class FederationPeerProbe:
    """Observed state from one federation peer probe.

    Attributes
    ----------
    snapshot : LogSnapshot
        Multi-hub log snapshot returned by the peer.
    peer_timestamp : float or None
        Timestamp advertised in the peer's welcome frame, when present.
    clock_skew_seconds : float or None
        Local receive time minus the peer welcome timestamp.
    certificate_expires_at : float or None
        TLS peer certificate expiry as a UNIX timestamp, when inspectable.
    """

    snapshot: LogSnapshot
    peer_timestamp: float | None
    clock_skew_seconds: float | None
    certificate_expires_at: float | None


class _Socket(Protocol):
    """Minimal websocket surface used by the federation probe."""

    async def send(self, message: str) -> None:  # pragma: no cover
        """Send one text frame to the peer."""
        ...

    async def recv(self) -> str | bytes:  # pragma: no cover
        """Receive the next frame from the peer."""
        ...


class _Connector(Protocol):
    """Open a peer websocket connection."""

    def __call__(self, uri: str) -> AbstractAsyncContextManager[_Socket]:  # pragma: no cover
        """Open a connection to ``uri``."""
        ...


class _ExtraInfoTransport(Protocol):
    """Transport metadata surface used to inspect TLS certificates."""

    def get_extra_info(self, name: str, default: object = None) -> object:  # pragma: no cover
        """Return transport metadata by name."""
        ...


class _PeerCertificate(Protocol):
    """TLS object surface exposing the peer certificate bytes."""

    def getpeercert(self, binary_form: bool = False) -> object:  # pragma: no cover
        """Return the peer certificate."""
        ...


class _Certificate(Protocol):
    """Parsed certificate fields used by doctor expiry diagnostics."""

    not_valid_after: Any
    """Legacy cryptography certificate expiry datetime."""


def _default_connector(uri: str) -> AbstractAsyncContextManager[_Socket]:
    """Open a real websocket connection to ``uri`` with keepalive pings."""
    return cast(AbstractAsyncContextManager[_Socket], connect(uri, ping_interval=20.0))


FederationProbe = Callable[
    [FederationDoctorPeer, int, str, str | None],
    Awaitable[FederationPeerProbe],
]
"""Async probe callable signature used by tests and by ``synapse doctor``."""


def parse_peer_specs(specs: Sequence[str]) -> tuple[list[FederationDoctorPeer], list[Diagnosis]]:
    """Parse ``PEER=URI`` doctor peer specifications.

    Parameters
    ----------
    specs : sequence of str
        Raw command-line values from ``--federation-peer``.

    Returns
    -------
    tuple[list[FederationDoctorPeer], list[Diagnosis]]
        Parsed peers plus fail diagnoses for malformed inputs.
    """
    peers: list[FederationDoctorPeer] = []
    diagnoses: list[Diagnosis] = []
    for spec in specs:
        peer_id, sep, uri = spec.partition("=")
        peer_id = peer_id.strip()
        uri = uri.strip()
        if not sep or not peer_id or not uri:
            diagnoses.append(
                Diagnosis(
                    check="federation-peer",
                    status="fail",
                    detail=f"malformed federation peer spec {spec!r}",
                    remedy="pass --federation-peer PEER=ws://host:port",
                )
            )
            continue
        peers.append(FederationDoctorPeer(peer_id=peer_id, uri=uri))
    return peers, diagnoses


def parse_cursor_specs(specs: Sequence[str]) -> tuple[dict[str, int], list[Diagnosis]]:
    """Parse ``PEER=SEQ`` cursor specifications for lag diagnostics."""
    cursors: dict[str, int] = {}
    diagnoses: list[Diagnosis] = []
    for spec in specs:
        peer_id, sep, raw_seq = spec.partition("=")
        peer_id = peer_id.strip()
        try:
            cursor = int(raw_seq)
        except ValueError:
            cursor = -1
        if not sep or not peer_id or cursor < 0:
            diagnoses.append(
                Diagnosis(
                    check="federation-cursor",
                    status="fail",
                    detail=f"malformed federation cursor spec {spec!r}",
                    remedy="pass --federation-cursor PEER=SEQ with SEQ >= 0",
                )
            )
            continue
        cursors[peer_id] = cursor
    return cursors, diagnoses


async def diagnose_federation(
    *,
    peer_specs: Sequence[str],
    cursor_specs: Sequence[str],
    local_id: str,
    token: str | None,
    store_path: Path | None,
    skew_warn_seconds: float = DEFAULT_FEDERATION_SKEW_WARN_SECONDS,
    cert_warn_days: int = DEFAULT_FEDERATION_CERT_WARN_DAYS,
    probe: FederationProbe | None = None,
    now: Callable[[], float] = time.time,
) -> list[Diagnosis]:
    """Return opt-in federation doctor diagnoses.

    Parameters
    ----------
    peer_specs : sequence of str
        ``PEER=URI`` values naming peers to probe.
    cursor_specs : sequence of str
        ``PEER=SEQ`` values giving local consumed cursors for lag checks.
    local_id : str
        Sender identity used for peer log requests.
    token : str or None
        Optional token included on peer requests.
    store_path : pathlib.Path or None
        Optional federation bundle store to inspect for revocation and expiry.
    skew_warn_seconds : float, optional
        Warn when measured absolute skew exceeds this many seconds.
    cert_warn_days : int, optional
        Warn when certificate or bundle expiry is within this many days.
    probe : callable, optional
        Async peer probe. Defaults to :func:`probe_federation_peer`.
    now : callable, optional
        Wall-clock source for deterministic tests.
    """
    peers, peer_diagnoses = parse_peer_specs(peer_specs)
    cursors, cursor_diagnoses = parse_cursor_specs(cursor_specs)
    diagnoses = [*peer_diagnoses, *cursor_diagnoses]
    records, store_diagnoses = _load_records(store_path)
    now_ts = now()
    warning_window = max(cert_warn_days, 0) * SECONDS_PER_DAY
    diagnoses.extend(store_diagnoses)
    diagnoses.extend(_store_diagnoses(records, now=now_ts, warn_within=warning_window))
    active_probe = probe_federation_peer if probe is None else probe
    for peer in peers:
        cursor = cursors.get(peer.peer_id, 0)
        try:
            observed = await active_probe(peer, cursor, local_id, token)
        except FederationDoctorError as exc:
            diagnoses.append(
                Diagnosis(
                    check=f"federation-peer:{peer.peer_id}",
                    status="fail",
                    detail=f"{peer.uri} did not answer a multi-hub log request: {exc}",
                    remedy="verify the peer URI, token, serving policy, network path, and TLS pin",
                )
            )
            continue
        diagnoses.append(
            _peer_diagnosis(
                peer=peer,
                probe=observed,
                cursor=cursor,
                skew_warn_seconds=skew_warn_seconds,
                now=now_ts,
                warn_within=warning_window,
                store_record=records.get(peer.peer_id),
            )
        )
    return diagnoses


async def probe_federation_peer(
    peer: FederationDoctorPeer,
    cursor: int,
    local_id: str,
    token: str | None,
    *,
    timeout: float = 10.0,
    connector: _Connector = _default_connector,
    clock: Callable[[], float] = time.time,
) -> FederationPeerProbe:
    """Probe one peer through the production multi-hub log request path."""
    fields: dict[str, Any] = dict(encode_log_request(LogRequest(after_seq=cursor, limit=0)))
    if token is not None:
        fields["token"] = token
    request = build_envelope(local_id, MessageType.MULTIHUB_LOG_REQUEST, **fields)
    try:
        async with connector(peer.uri) as socket:
            await socket.send(json.dumps(request))
            certificate_expires_at = _peer_certificate_expires_at(socket)
            snapshot, peer_timestamp, skew = await asyncio.wait_for(
                _await_snapshot_with_welcome(socket, clock=clock), timeout
            )
    except FederationDoctorError:
        raise
    except (
        OSError,
        ConnectionClosed,
        asyncio.TimeoutError,
        MultiHubWireError,
        json.JSONDecodeError,
        ValueError,
    ) as exc:
        msg = str(exc)
        raise FederationDoctorError(msg) from exc
    return FederationPeerProbe(
        snapshot=snapshot,
        peer_timestamp=peer_timestamp,
        clock_skew_seconds=skew,
        certificate_expires_at=certificate_expires_at,
    )


async def _await_snapshot_with_welcome(
    socket: _Socket, *, clock: Callable[[], float]
) -> tuple[LogSnapshot, float | None, float | None]:
    """Read frames until a log snapshot arrives, recording welcome clock skew."""
    peer_timestamp: float | None = None
    skew: float | None = None
    while True:
        frame = _parse_frame(await socket.recv())
        frame_type = frame.get("type")
        if frame_type == MessageType.WELCOME:
            timestamp = _finite_timestamp(frame.get("timestamp"))
            if timestamp is not None:
                peer_timestamp = timestamp
                skew = clock() - timestamp
            continue
        if frame_type == MessageType.MULTIHUB_LOG_SNAPSHOT:
            return decode_log_snapshot(frame), peer_timestamp, skew
        if frame_type == MessageType.ERROR:
            msg = f"peer refused the multi-hub log request: {frame.get('payload')!r}"
            raise FederationDoctorError(msg)


def _parse_frame(raw: str | bytes) -> dict[str, Any]:
    """Decode one peer frame as a JSON object."""
    text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
    decoded = loads_bounded(text)
    if not isinstance(decoded, dict):
        msg = "peer sent a frame that is not a JSON object"
        raise FederationDoctorError(msg)
    return decoded


def _finite_timestamp(value: object) -> float | None:
    """Return ``value`` as a finite timestamp, or ``None`` when unusable."""
    if isinstance(value, bool):
        return None
    if not isinstance(value, (str, bytes, bytearray, int, float)):
        return None
    try:
        timestamp = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return timestamp if math.isfinite(timestamp) else None


def _peer_certificate_expires_at(socket: _Socket) -> float | None:
    """Return a websocket peer certificate expiry timestamp when TLS exposes one."""
    transport = getattr(socket, "transport", None)
    get_extra_info = getattr(transport, "get_extra_info", None)
    if get_extra_info is None:
        return None
    ssl_object = get_extra_info("ssl_object")
    if ssl_object is None:
        return None
    der = cast(_PeerCertificate, ssl_object).getpeercert(binary_form=True)
    if not isinstance(der, bytes):
        return None
    certificate = _load_der_certificate(der)
    if certificate is None:
        return None
    not_after = getattr(certificate, "not_valid_after_utc", None)
    if not_after is None:
        not_after = certificate.not_valid_after.replace(tzinfo=timezone.utc)
    return float(not_after.timestamp())


def _load_der_certificate(der: bytes) -> _Certificate | None:
    """Parse a DER certificate when the optional cryptography dependency is present."""
    try:
        from cryptography import x509
    except ImportError:
        return None
    return cast(_Certificate, x509.load_der_x509_certificate(der))


def _load_records(store_path: Path | None) -> tuple[dict[str, FederationRecord], list[Diagnosis]]:
    """Load federation records from ``store_path`` or return a fail diagnosis."""
    if store_path is None:
        return {}, []
    try:
        return load_store(store_path), []
    except FederationStoreError as exc:
        return {}, [
            Diagnosis(
                check="federation-store",
                status="fail",
                detail=f"{store_path} could not be read as a federation store: {exc}",
                remedy="repair or regenerate the federation store before trusting peer routes",
            )
        ]


def _store_diagnoses(
    records: Mapping[str, FederationRecord], *, now: float, warn_within: float
) -> list[Diagnosis]:
    """Return revocation and expiry diagnoses for imported federation records."""
    diagnoses: list[Diagnosis] = []
    for domain_id, record in sorted(records.items()):
        peer = record.peer
        if peer.revoked:
            diagnoses.append(
                Diagnosis(
                    check=f"federation-bundle:{domain_id}",
                    status="warn",
                    detail=f"imported peer {domain_id!r} is revoked in the federation store",
                    remedy=(
                        "remove active peer routes for revoked domains or import a "
                        "non-revoked bundle"
                    ),
                )
            )
            continue
        if peer.expires_at is None:
            diagnoses.append(
                Diagnosis(
                    check=f"federation-bundle:{domain_id}",
                    status="pass",
                    detail=f"imported peer {domain_id!r} is active with no bundle expiry",
                )
            )
            continue
        seconds_left = peer.expires_at - now
        if seconds_left < 0:
            diagnoses.append(
                Diagnosis(
                    check=f"federation-bundle:{domain_id}",
                    status="fail",
                    detail=(
                        f"imported peer {domain_id!r} expired "
                        f"{-seconds_left / SECONDS_PER_DAY:.1f}d ago"
                    ),
                    remedy=(
                        "rotate or re-import the peer bundle before serving or "
                        "pulling federation traffic"
                    ),
                )
            )
        elif seconds_left <= warn_within:
            diagnoses.append(
                Diagnosis(
                    check=f"federation-bundle:{domain_id}",
                    status="warn",
                    detail=(
                        f"imported peer {domain_id!r} expires in "
                        f"{seconds_left / SECONDS_PER_DAY:.1f}d"
                    ),
                    remedy="schedule bundle rotation before the federation expiry window closes",
                )
            )
        else:
            diagnoses.append(
                Diagnosis(
                    check=f"federation-bundle:{domain_id}",
                    status="pass",
                    detail=(
                        f"imported peer {domain_id!r} expires in "
                        f"{seconds_left / SECONDS_PER_DAY:.1f}d"
                    ),
                )
            )
    return diagnoses


def _peer_diagnosis(
    *,
    peer: FederationDoctorPeer,
    probe: FederationPeerProbe,
    cursor: int,
    skew_warn_seconds: float,
    now: float,
    warn_within: float,
    store_record: FederationRecord | None,
) -> Diagnosis:
    """Return one diagnosis for a reachable peer probe."""
    log_end = probe.snapshot.log_end_seq
    parts = [f"{peer.uri} answered"]
    status: DoctorStatus = "pass"
    remedy = ""
    if log_end is None:
        status = "warn"
        parts.append("cursor lag unknown: peer omitted log_end_seq")
        remedy = "upgrade the peer so multi-hub snapshots include log_end_seq"
    else:
        lag = max(0, log_end - cursor)
        parts.append(f"cursor={cursor}, log_end={log_end}, lag={lag}")
    if probe.clock_skew_seconds is not None:
        skew = probe.clock_skew_seconds
        parts.append(f"clock_skew={skew:+.3f}s")
        if abs(skew) > skew_warn_seconds:
            status = "warn"
            remedy = "synchronise clocks with NTP/chrony before relying on federated ordering"
    else:
        status = "warn" if status == "pass" else status
        parts.append("clock_skew=unknown")
        remedy = remedy or "upgrade the peer so welcome frames carry timestamps"
    if probe.certificate_expires_at is not None:
        seconds_left = probe.certificate_expires_at - now
        parts.append(f"tls_cert_expires_in={seconds_left / SECONDS_PER_DAY:.1f}d")
        if seconds_left < 0:
            status = "fail"
            remedy = "rotate the peer TLS certificate before federation traffic is trusted"
        elif seconds_left <= warn_within and status != "fail":
            status = "warn"
            remedy = remedy or "schedule TLS certificate rotation"
    if store_record is not None and store_record.peer.revoked:
        status = "fail"
        parts.append("bundle_revoked=true")
        remedy = "remove this peer route or import a non-revoked federation bundle"
    return Diagnosis(
        check=f"federation-peer:{peer.peer_id}",
        status=status,
        detail="; ".join(parts),
        remedy=remedy,
    )
