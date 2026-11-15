# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — unimplemented protocol declarations stay inert

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import cast

from synapse_channel.ack import AckIdentity
from synapse_channel.cli_doctor_federation import (
    _Connector as DoctorConnector,
)
from synapse_channel.cli_doctor_federation import (
    _ExtraInfoTransport as DoctorTransport,
)
from synapse_channel.cli_doctor_federation import (
    _PeerCertificate as DoctorCertificate,
)
from synapse_channel.cli_doctor_federation import (
    _Socket as DoctorSocket,
)
from synapse_channel.commit import IdentityLike
from synapse_channel.core.aef_verification import AefReplayIndex
from synapse_channel.core.dead_letter_forwarding_transport import (
    _Connector as ForwardConnector,
)
from synapse_channel.core.dead_letter_forwarding_transport import (
    _Socket as ForwardSocket,
)
from synapse_channel.core.federation_fetch import _Connector as FetchConnector
from synapse_channel.core.federation_fetch import _ExtraInfoTransport as FetchTransport
from synapse_channel.core.federation_fetch import _Socket as FetchSocket
from synapse_channel.core.fleet_scorecard_metrics import BenchmarkRun
from synapse_channel.core.http_response import _Readable
from synapse_channel.core.multihub_equivocation import FingerprintableHubEvent
from synapse_channel.core.tls import _ExtraInfoTransport as TlsTransport
from synapse_channel.core.tls import _PeerCertificate as TlsCertificate
from synapse_channel.ergonomics_inbox import InboxIdentity
from synapse_channel.locks import LockIdentity
from synapse_channel.reap import ReapIdentity


def _call(member: object, *args: object, **kwargs: object) -> object:
    """Call one protocol function body. A real type supplies its own."""

    function = cast(Callable[..., object], member)
    return function(*args, **kwargs)


async def _await(member: object, *args: object) -> object:
    """Await one protocol coroutine body. A real socket supplies its own."""

    function = cast(Callable[..., Awaitable[object]], member)
    return await function(*args)


def _read(member: object) -> object:
    """Call one protocol property body. A real type supplies its own."""

    function = cast(property, member).fget
    assert function is not None
    return cast(Callable[[object], object], function)(object())


def test_an_unimplemented_ack_identity_does_not_name_anyone() -> None:
    """The declaration reads nothing. The ergonomics identity supplies the names."""

    assert _read(AckIdentity.project) is None
    assert _read(AckIdentity.identity) is None


def test_an_unimplemented_commit_identity_does_not_name_anyone() -> None:
    """The declaration reads nothing. The resolved identity supplies the names."""

    assert _read(IdentityLike.project) is None
    assert _read(IdentityLike.identity) is None


def test_an_unimplemented_lock_identity_does_not_name_anyone() -> None:
    """The declaration reads nothing. The query identity supplies the names."""

    assert _read(LockIdentity.project) is None
    assert _read(LockIdentity.identity) is None


def test_an_unimplemented_inbox_identity_does_not_name_anyone() -> None:
    """The declaration reads nothing. The inbox identity supplies the names."""

    assert _read(InboxIdentity.project) is None
    assert _read(InboxIdentity.identity) is None


def test_an_unimplemented_reap_identity_does_not_name_a_waiter() -> None:
    """The declaration reads nothing. The reaper identity supplies the names."""

    assert _read(ReapIdentity.project) is None
    assert _read(ReapIdentity.identity) is None
    assert _read(ReapIdentity.waiter_name) is None


def test_an_unimplemented_fingerprint_event_carries_nothing() -> None:
    """The declaration reads nothing. A stored hub event supplies the fields."""

    assert _read(FingerprintableHubEvent.hub_id) is None
    assert _read(FingerprintableHubEvent.seq) is None
    assert _read(FingerprintableHubEvent.ts) is None
    assert _read(FingerprintableHubEvent.kind) is None
    assert _read(FingerprintableHubEvent.payload) is None


def test_an_unimplemented_benchmark_run_measures_nothing() -> None:
    """The declaration reads nothing. A stored benchmark run supplies the fields."""

    assert _read(BenchmarkRun.run_id) is None
    assert _read(BenchmarkRun.started_at) is None
    assert _read(BenchmarkRun.package_version) is None
    assert _read(BenchmarkRun.cpu_model) is None
    assert _read(BenchmarkRun.governor) is None
    assert _read(BenchmarkRun.metrics) is None


def test_an_unimplemented_replay_index_records_nothing() -> None:
    """The declaration records nothing. A receipt index supplies the memory."""

    classify = cast(Callable[..., object], AefReplayIndex.classify_and_remember)
    assert classify(object(), "log", 1, "receipt") is None


async def test_an_unimplemented_doctor_socket_does_not_transmit() -> None:
    """The probe declarations transmit nothing. A live socket supplies the frames."""

    assert await _await(DoctorSocket.send, object(), "frame") is None
    assert await _await(DoctorSocket.recv, object()) is None
    assert _call(DoctorConnector.__call__, object(), "ws://peer") is None
    assert _call(DoctorTransport.get_extra_info, object(), "ssl_object") is None
    assert _call(DoctorCertificate.getpeercert, object(), binary_form=True) is None


async def test_an_unimplemented_fetch_socket_does_not_transmit() -> None:
    """The fetch declarations transmit nothing. A live socket supplies the frames."""

    assert await _await(FetchSocket.send, object(), "frame") is None
    assert await _await(FetchSocket.recv, object()) is None
    assert _call(FetchConnector.__call__, object(), "ws://peer") is None
    assert _call(FetchTransport.get_extra_info, object(), "ssl_object") is None


async def test_an_unimplemented_forward_socket_does_not_transmit() -> None:
    """The forward declarations transmit nothing. A live socket supplies the frame."""

    assert await _await(ForwardSocket.send, object(), "frame") is None
    assert _call(ForwardConnector.__call__, object(), "ws://owner") is None


def test_an_unimplemented_response_reader_does_not_read() -> None:
    """The reader declaration returns nothing. A real response supplies the bytes."""

    assert _call(_Readable.read, object(), 8) is None


def test_an_unimplemented_tls_peer_does_not_present_a_certificate() -> None:
    """The TLS declarations return nothing. A live transport supplies the certificate."""

    assert _call(TlsTransport.get_extra_info, object(), "ssl_object") is None
    assert _call(TlsCertificate.getpeercert, object(), binary_form=True) is None
