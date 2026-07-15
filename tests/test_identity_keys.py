# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for Ed25519 identity signing keys

from __future__ import annotations

import stat
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from synapse_channel.core.identity_binding import (
    enroll_identity_key,
    load_identity_trust_bundle,
    verify_registration,
)
from synapse_channel.core.identity_keys import (
    SIGNING_KEY_FILE_MODE,
    IdentityKeyError,
    generate_signing_key,
    load_signing_key,
    public_key_b64,
    sign_registration,
    write_signing_key,
)
from synapse_channel.core.message_auth import SignedEventVerificationResult

_SENDER = "SYNAPSE-CHANNEL/claude-2759"
_KEY_ID = "id-k1"


def test_generate_write_and_load_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "id.pem"
    original = generate_signing_key()

    write_signing_key(path, original)
    loaded = load_signing_key(path)

    assert public_key_b64(loaded) == public_key_b64(original)


def test_written_key_is_owner_only(tmp_path: Path) -> None:
    path = tmp_path / "id.pem"
    write_signing_key(path, generate_signing_key())

    assert stat.S_IMODE(path.stat().st_mode) == SIGNING_KEY_FILE_MODE


def test_write_refuses_to_overwrite(tmp_path: Path) -> None:
    path = tmp_path / "id.pem"
    write_signing_key(path, generate_signing_key())

    with pytest.raises(IdentityKeyError, match="cannot write identity key"):
        write_signing_key(path, generate_signing_key())


def test_load_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(IdentityKeyError, match="cannot read identity key"):
        load_signing_key(tmp_path / "nope.pem")


def test_load_non_pem_raises(tmp_path: Path) -> None:
    path = tmp_path / "id.pem"
    path.write_bytes(b"not a pem key")
    # Secret floor runs before PEM parse — fixtures must be owner-only.
    path.chmod(SIGNING_KEY_FILE_MODE)

    with pytest.raises(IdentityKeyError, match="not a valid PEM key"):
        load_signing_key(path)


def test_load_non_ed25519_key_raises(tmp_path: Path) -> None:
    path = tmp_path / "rsa.pem"
    rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    path.write_bytes(
        rsa_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    path.chmod(SIGNING_KEY_FILE_MODE)

    with pytest.raises(IdentityKeyError, match="must be Ed25519"):
        load_signing_key(path)


def test_expands_home_relative_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

    write_signing_key("~/id.pem", generate_signing_key())

    assert load_signing_key("~/id.pem") is not None


def test_sign_registration_produces_a_frame_the_hub_verifies(tmp_path: Path) -> None:
    # The full agent-to-hub flow: generate a key, enrol its public half, sign a
    # registration frame, and confirm the hub-side verifier accepts it.
    key_path = tmp_path / "id.pem"
    trust_path = tmp_path / "trust.json"
    private_key = generate_signing_key()
    write_signing_key(key_path, private_key)
    enroll_identity_key(
        trust_path, key_id=_KEY_ID, public_key_b64=public_key_b64(private_key), senders=[_SENDER]
    )
    bundle = load_identity_trust_bundle(trust_path)

    frame = sign_registration(
        {"sender": _SENDER, "type": "heartbeat", "target": "System"},
        private_key=load_signing_key(key_path),
        key_id=_KEY_ID,
        nonce="reg-1",
        sequence=1,
        signed_at=1000.0,
    )
    result = verify_registration(frame, trust_bundle=bundle, now=1000.0, required_sender=_SENDER)

    assert result is SignedEventVerificationResult.VALID


def test_two_generated_keys_differ() -> None:
    assert public_key_b64(generate_signing_key()) != public_key_b64(generate_signing_key())
