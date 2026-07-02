# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the federation gate as a standalone unit
"""Unit tests for :class:`~synapse_channel.core.hub_federation_gate.HubFederationGate`.

The gate is constructed directly — no :class:`SynapseHub` — with a recording
``send_json`` and a dictionary-building ``system`` factory, proving it carries no
hub back-reference: classification (local versus cross-domain), the deny-closed
composition with each refusal reason, the denial reply shape, and the unresolved
misconfiguration warning are all reachable through the injected callbacks alone.
The hub-wired path over a real socket lives in
``test_hub_federation_frame_path.py``; this file pins the extracted unit.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from synapse_channel.core.acl import CLAIM, MESSAGE
from synapse_channel.core.federation import FederationBundle, FederationPeer, ScopeGrant
from synapse_channel.core.hub_federation_gate import FrameDisposition, HubFederationGate
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.tls import certificate_sha256_pin_from_der

_DOMAIN = "domain-b"
_KEY_ID = "domain-b:main:2026-06"
_LOCAL_KEY_ID = "SYNAPSE-CHANNEL:main:2026-06"
_NAMESPACE = "SYNAPSE-CHANNEL"
_REMOTE = "SYNAPSE-CHANNEL/remote"


def _peer_der() -> bytes:
    """Return the DER bytes of a fresh self-signed certificate the gate can pin."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "peer-gate")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime(2020, 1, 1, tzinfo=timezone.utc))
        .not_valid_after(datetime(2030, 1, 1, tzinfo=timezone.utc))
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.DER)


_DER = _peer_der()
_PIN = certificate_sha256_pin_from_der(_DER)
_OTHER_DER = _peer_der()  # a second certificate whose pin no peering enrolls


def _bundle(
    *, scope: tuple[ScopeGrant, ...] = (ScopeGrant(CLAIM, _NAMESPACE),)
) -> FederationBundle:
    return FederationBundle(
        [
            FederationPeer(
                domain_id=_DOMAIN,
                namespaces=frozenset({_NAMESPACE}),
                certificate_pins=frozenset({_PIN}),
                signing_key_ids=frozenset({_KEY_ID}),
                scope_grants=scope,
            )
        ]
    )


class _Sink:
    """Records every frame the gate hands to ``send_json`` on a deny."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_json(self, _websocket: Any, data: dict[str, Any]) -> None:
        self.sent.append(data)


def _system(payload: str, **extra: Any) -> dict[str, Any]:
    """Stand-in system-message factory mirroring the keyword pass-through shape."""
    return {"payload": payload, **extra}


def _gate(
    sink: _Sink,
    *,
    bundle: FederationBundle | None,
    cert: bytes | None = _DER,
    require_auth: bool = True,
    trust: bool = True,
) -> HubFederationGate:
    return HubFederationGate(
        bundle,
        cert_source=lambda _websocket: cert,
        require_per_message_auth=require_auth,
        signed_event_trust=trust,
        system=_system,
        send_json=sink.send_json,
    )


def _claim(*, key_id: str | None = _KEY_ID, sender: str = _REMOTE) -> dict[str, Any]:
    frame: dict[str, Any] = {"type": MessageType.CLAIM, "sender": sender, "task_id": "T1"}
    if key_id is not None:
        frame["signature"] = {"key_id": key_id}
    return frame


# --- classification: frames that are not peered cross-domain frames stay local ---


async def test_gate_without_a_bundle_is_a_no_op() -> None:
    sink = _Sink()
    gate = _gate(sink, bundle=None)
    disposition = await gate.authorise(_REMOTE, MessageType.CLAIM, _claim(), object())
    assert disposition is FrameDisposition.LOCAL
    assert sink.sent == []


async def test_frame_without_a_signature_block_is_local() -> None:
    gate = _gate(_Sink(), bundle=_bundle())
    frame = {"type": MessageType.CHAT, "sender": _REMOTE}
    assert await gate.authorise(_REMOTE, MessageType.CHAT, frame, object()) is (
        FrameDisposition.LOCAL
    )


async def test_frame_with_a_blank_key_id_is_local() -> None:
    gate = _gate(_Sink(), bundle=_bundle())
    disposition = await gate.authorise(_REMOTE, MessageType.CLAIM, _claim(key_id=""), object())
    assert disposition is FrameDisposition.LOCAL


async def test_peered_key_without_a_certificate_is_denied() -> None:
    # a peered key claims cross-domain authority only a live pin can bind;
    # a plaintext connection cannot bind it, so the frame never runs as local
    sink = _Sink()
    gate = _gate(sink, bundle=_bundle(), cert=None)
    disposition = await gate.authorise(_REMOTE, MessageType.CLAIM, _claim(), object())
    assert disposition is FrameDisposition.DENY
    assert sink.sent[0]["federation_reason"] == "peer_certificate_unavailable"


async def test_local_key_without_a_certificate_stays_local() -> None:
    gate = _gate(_Sink(), bundle=_bundle(), cert=None)
    disposition = await gate.authorise(
        _REMOTE, MessageType.CLAIM, _claim(key_id=_LOCAL_KEY_ID), object()
    )
    assert disposition is FrameDisposition.LOCAL


def _raising_gate(sink: _Sink) -> HubFederationGate:
    def _raising(_websocket: Any) -> bytes | None:
        raise OSError("socket closed during certificate read")

    return HubFederationGate(
        _bundle(),
        cert_source=_raising,
        require_per_message_auth=True,
        signed_event_trust=True,
        system=_system,
        send_json=sink.send_json,
    )


async def test_certificate_read_failure_denies_a_peered_key(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # an attacker who can break the certificate read must not be able to
    # downgrade a cross-domain frame to local processing
    sink = _Sink()
    gate = _raising_gate(sink)
    with caplog.at_level("WARNING"):
        disposition = await gate.authorise(_REMOTE, MessageType.CLAIM, _claim(), object())
    assert disposition is FrameDisposition.DENY
    assert "certificate read failed" in caplog.text
    assert sink.sent[0]["federation_reason"] == "peer_certificate_unavailable"


async def test_certificate_read_failure_keeps_a_local_key_local(
    caplog: pytest.LogCaptureFixture,
) -> None:
    sink = _Sink()
    gate = _raising_gate(sink)
    with caplog.at_level("WARNING"):
        disposition = await gate.authorise(
            _REMOTE, MessageType.CLAIM, _claim(key_id=_LOCAL_KEY_ID), object()
        )
    assert disposition is FrameDisposition.LOCAL
    assert sink.sent == []


async def test_partial_credential_match_warns_and_stays_local(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # the enrolled certificate pin with a signing key no peering knows is the
    # misconfiguration the unresolved diagnosis exists for: local, but warned.
    gate = _gate(_Sink(), bundle=_bundle())
    with caplog.at_level("WARNING"):
        disposition = await gate.authorise(
            _REMOTE, MessageType.CLAIM, _claim(key_id=_LOCAL_KEY_ID), object()
        )
    assert disposition is FrameDisposition.LOCAL
    assert "resolves to no peered domain" in caplog.text


async def test_wholly_unrelated_signed_frame_stays_silent(
    caplog: pytest.LogCaptureFixture,
) -> None:
    gate = _gate(_Sink(), bundle=_bundle(), cert=_OTHER_DER)
    with caplog.at_level("WARNING"):
        disposition = await gate.authorise(
            _REMOTE, MessageType.CLAIM, _claim(key_id=_LOCAL_KEY_ID), object()
        )
    assert disposition is FrameDisposition.LOCAL
    assert "resolves to no peered domain" not in caplog.text


# --- cross-domain authorisation: one allow, every refusal reason ---


async def test_cross_domain_frame_within_scope_is_allowed() -> None:
    sink = _Sink()
    gate = _gate(sink, bundle=_bundle())
    disposition = await gate.authorise(_REMOTE, MessageType.CLAIM, _claim(), object())
    assert disposition is FrameDisposition.ALLOW_CROSS_DOMAIN
    assert sink.sent == []


async def test_policy_refusal_reports_the_policy_reason() -> None:
    sink = _Sink()
    gate = _gate(sink, bundle=_bundle())
    disposition = await gate.authorise(
        "OTHER/remote", MessageType.CLAIM, _claim(sender="OTHER/remote"), object()
    )
    assert disposition is FrameDisposition.DENY
    assert sink.sent[0]["federation_reason"] == "namespace_not_granted"
    assert sink.sent[0]["federation_domain"] == _DOMAIN
    assert sink.sent[0]["payload"] == "federation denied: namespace_not_granted"


async def test_hub_without_required_message_auth_cannot_bind_the_signature() -> None:
    sink = _Sink()
    gate = _gate(sink, bundle=_bundle(), require_auth=False)
    disposition = await gate.authorise(_REMOTE, MessageType.CLAIM, _claim(), object())
    assert disposition is FrameDisposition.DENY
    assert sink.sent[0]["federation_reason"] == "signature_not_verified"


async def test_hub_without_a_trust_bundle_cannot_bind_the_signature() -> None:
    sink = _Sink()
    gate = _gate(sink, bundle=_bundle(), trust=False)
    disposition = await gate.authorise(_REMOTE, MessageType.CLAIM, _claim(), object())
    assert disposition is FrameDisposition.DENY
    assert sink.sent[0]["federation_reason"] == "signature_not_verified"


async def test_hmac_authenticated_frame_cannot_bind_the_signature() -> None:
    # an "auth" block means the frame passed HMAC verification, not the Ed25519
    # signature path, so it binds no cross-domain authority.
    sink = _Sink()
    gate = _gate(sink, bundle=_bundle())
    frame = _claim()
    frame["auth"] = {"key_id": "hmac-1"}
    disposition = await gate.authorise(_REMOTE, MessageType.CLAIM, frame, object())
    assert disposition is FrameDisposition.DENY
    assert sink.sent[0]["federation_reason"] == "signature_not_verified"


async def test_out_of_scope_frame_is_denied_with_the_scope_reason() -> None:
    sink = _Sink()
    gate = _gate(sink, bundle=_bundle(scope=(ScopeGrant(MESSAGE, _NAMESPACE),)))
    disposition = await gate.authorise(_REMOTE, MessageType.CLAIM, _claim(), object())
    assert disposition is FrameDisposition.DENY
    assert sink.sent[0]["federation_reason"] == "out_of_scope"


# --- the unresolved warning as a direct call ---


def test_warn_unresolved_without_a_bundle_is_silent(caplog: pytest.LogCaptureFixture) -> None:
    gate = _gate(_Sink(), bundle=None)
    with caplog.at_level("WARNING"):
        gate.warn_unresolved("SENDER", "chat", "key-1", "sha256:aa")
    assert "resolves to no peered domain" not in caplog.text


def test_warn_unresolved_names_the_diagnosis(caplog: pytest.LogCaptureFixture) -> None:
    gate = _gate(_Sink(), bundle=_bundle())
    with caplog.at_level("WARNING"):
        gate.warn_unresolved(_REMOTE, "claim", _LOCAL_KEY_ID, _PIN)
    assert "certificate_pin_enrolled_but_signing_key_unknown" in caplog.text
