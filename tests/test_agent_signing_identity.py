# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — one signing identity owns the counters
"""Counters advance once per signed frame, and a key never leaves the identity."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from synapse_channel.client.agent import SynapseAgent
from synapse_channel.client.agent_signing_identity import AgentSigningIdentity
from synapse_channel.client.frame_signing_client import ClientFrameSession
from synapse_channel.core.frame_signature import FRAME_SIGNATURE_FIELD
from synapse_channel.core.identity_keys import generate_signing_key, write_signing_key
from synapse_channel.core.message_auth import DEFAULT_SIGNED_MESSAGE_TYPES, MessageAuthKey
from synapse_channel.core.protocol import MessageType


def _identity(
    tmp_path: Path,
    *,
    identity_key_path: str | None = "",
    per_message_auth_key_id: str | None = None,
    per_message_auth_secret: str | bytes | None = None,
    capability_card_key_path: str | None = None,
    capability_card_key_id: str = "",
    capability_card_project: str = "",
) -> AgentSigningIdentity:
    key_path = tmp_path / "id.pem"
    write_signing_key(key_path, generate_signing_key())
    chosen = str(key_path) if identity_key_path == "" else identity_key_path
    return AgentSigningIdentity(
        name="proj/seat",
        identity_key_path=chosen,
        identity_key_id="seat-key",
        machine_identity=False,
        per_message_auth_key_id=per_message_auth_key_id,
        per_message_auth_secret=per_message_auth_secret,
        capability_card_key_path=capability_card_key_path,
        capability_card_key_id=capability_card_key_id,
        capability_card_project=capability_card_project,
        capability_card_lifetime_seconds=60.0,
    )


def test_the_constructor_accepts_keywords_only() -> None:
    factory: Any = AgentSigningIdentity
    with pytest.raises(TypeError):
        factory("proj/seat")


def test_registration_with_a_frame_session_omits_the_session_signature(tmp_path: Path) -> None:
    signing = _identity(tmp_path)
    session = ClientFrameSession("hub", "nonce")
    envelope = signing.apply_to_envelope(
        {"type": MessageType.HEARTBEAT},
        msg_type=MessageType.HEARTBEAT,
        sign_identity=True,
        frame_session=session,
    )
    assert [name for name in envelope if name in {FRAME_SIGNATURE_FIELD, "signature"}] == [
        "signature"
    ]
    assert session.seq == 0
    signature = envelope["signature"]
    assert signature["key_id"] == "seat-key"
    assert signature["sequence"] == 1
    assert isinstance(signature["value"], str) and signature["value"]


def test_an_identity_signature_advances_once_and_an_unsigned_frame_does_not(tmp_path: Path) -> None:
    signing = _identity(tmp_path)
    signing.apply_to_envelope(
        {"type": MessageType.HEARTBEAT},
        msg_type=MessageType.HEARTBEAT,
        sign_identity=True,
        frame_session=None,
    )
    assert signing._identity_sequence == 1
    signing.apply_to_envelope(
        {"type": MessageType.CHAT},
        msg_type=MessageType.CHAT,
        sign_identity=False,
        frame_session=None,
    )
    assert signing._identity_sequence == 1


def test_message_auth_does_not_advance_on_an_unsigned_type(tmp_path: Path) -> None:
    signing = _identity(
        tmp_path,
        identity_key_path=None,
        per_message_auth_key_id="auth",
        per_message_auth_secret=b"secret",
    )
    signed_type = next(iter(DEFAULT_SIGNED_MESSAGE_TYPES))
    signing.apply_to_envelope({}, msg_type=signed_type, sign_identity=False, frame_session=None)
    advanced = signing._message_auth_sequence
    assert advanced != 0
    signing.apply_to_envelope(
        {}, msg_type="not-a-signed-type", sign_identity=False, frame_session=None
    )
    assert signing._message_auth_sequence == advanced


def test_a_capability_card_sequence_is_not_reused(tmp_path: Path) -> None:
    card = tmp_path / "card.pem"
    write_signing_key(card, generate_signing_key())
    signing = _identity(
        tmp_path,
        capability_card_key_path=str(card),
        capability_card_key_id="proj:card",
        capability_card_project="proj",
    )
    first = signing.signed_capability_fields(
        description="",
        skills=(),
        task_classes=(),
        model="",
        manifest_digest="",
        contracts=None,
        meta=None,
    )
    second = signing.signed_capability_fields(
        description="",
        skills=(),
        task_classes=(),
        model="",
        manifest_digest="",
        contracts=None,
        meta=None,
    )
    assert signing._capability_card_sequence == 2
    assert first["signature"] != second["signature"]


def test_a_missing_card_key_refuses_before_the_counter_moves(tmp_path: Path) -> None:
    signing = _identity(tmp_path)
    with pytest.raises(ValueError, match="a signed capability card requires a key"):
        signing.signed_capability_fields(
            description="",
            skills=(),
            task_classes=(),
            model="",
            manifest_digest="",
            contracts=None,
            meta=None,
        )
    assert signing._capability_card_sequence == 0


def test_repr_is_exactly_the_key_ids(tmp_path: Path) -> None:
    signing = _identity(tmp_path, per_message_auth_key_id="auth", per_message_auth_secret=b"secret")
    assert repr(signing) == (
        "AgentSigningIdentity("
        "identity_key_id='seat-key', "
        "message_auth_key_id='auth', "
        "capability_card_key_id='')"
    )


def test_the_agent_facade_does_not_hold_the_key(tmp_path: Path) -> None:
    identity = tmp_path / "id.pem"
    card = tmp_path / "card.pem"
    write_signing_key(identity, generate_signing_key())
    write_signing_key(card, generate_signing_key())
    agent = SynapseAgent(
        "proj/seat",
        verbose=False,
        machine_identity=False,
        identity_key_path=str(identity),
        identity_key_id="seat-key",
        per_message_auth_key_id="auth",
        per_message_auth_secret=b"secret",
        capability_card_key_path=str(card),
        capability_card_key_id="proj:card",
        capability_card_project="proj",
    )
    assert agent.signing.has_identity_key
    assert agent.signing.has_capability_card_key
    assert agent.signing.capability_card_key_id == "proj:card"
    assert agent.signing.capability_card_project == "proj"
    agent.signing.require_message_auth()
    for owner in (agent, agent.signing):
        for name in dir(owner):
            if name.startswith("_"):
                continue
            value = getattr(owner, name)
            assert not isinstance(value, Ed25519PrivateKey)
            assert not isinstance(value, MessageAuthKey)
