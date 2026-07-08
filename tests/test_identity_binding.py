# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for connection-identity binding (trust bundle + registration verify)

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from synapse_channel.core.identity_binding import (
    IdentityBindingError,
    enroll_identity_key,
    load_identity_trust_bundle,
    verify_registration,
)
from synapse_channel.core.message_auth import SignedEventVerificationResult, sign_event_frame

_SENDER = "SYNAPSE-CHANNEL/claude-2759"
_KEY_ID = "id-k1"


def _public_b64(private_key: Ed25519PrivateKey) -> str:
    raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    return base64.b64encode(raw).decode("ascii")


def _write_bundle(path: Path, **key_overrides: object) -> Ed25519PrivateKey:
    private_key = Ed25519PrivateKey.generate()
    entry: dict[str, object] = {
        "key_id": _KEY_ID,
        "public_key": _public_b64(private_key),
        "senders": [_SENDER],
    }
    entry.update(key_overrides)
    path.write_text(json.dumps({"keys": [entry]}), encoding="utf-8")
    return private_key


def _registration_frame(
    private_key: Ed25519PrivateKey, *, sender: str, signed_at: float
) -> dict[str, Any]:
    frame = {"sender": sender, "type": "heartbeat", "target": "System", "payload": "online"}
    return sign_event_frame(
        frame, key_id=_KEY_ID, private_key=private_key, nonce="n1", sequence=1, signed_at=signed_at
    )


class TestLoad:
    def test_loads_a_valid_bundle(self, tmp_path: Path) -> None:
        path = tmp_path / "trust.json"
        _write_bundle(path)

        bundle = load_identity_trust_bundle(path)

        assert _KEY_ID in bundle.keys
        assert bundle.keys[_KEY_ID].senders == frozenset({_SENDER})

    def test_carries_projects_expiry_and_revoked(self, tmp_path: Path) -> None:
        path = tmp_path / "trust.json"
        _write_bundle(path, projects=["SYNAPSE-CHANNEL"], expires_at=1900000000.0, revoked=True)

        key = load_identity_trust_bundle(path).keys[_KEY_ID]

        assert key.projects == frozenset({"SYNAPSE-CHANNEL"})
        assert key.expires_at == 1900000000.0
        assert key.revoked is True

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(IdentityBindingError, match="does not exist"):
            load_identity_trust_bundle(tmp_path / "nope.json")

    def test_invalid_json_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "trust.json"
        path.write_text("{bad", encoding="utf-8")

        with pytest.raises(IdentityBindingError, match="invalid identity trust JSON"):
            load_identity_trust_bundle(path)

    def test_shape_must_be_mapping_with_keys_list(self, tmp_path: Path) -> None:
        path = tmp_path / "trust.json"
        path.write_text(json.dumps({"keys": {}}), encoding="utf-8")

        with pytest.raises(IdentityBindingError, match="mapping with a 'keys' list"):
            load_identity_trust_bundle(path)

    def test_key_entry_must_be_an_object(self, tmp_path: Path) -> None:
        path = tmp_path / "trust.json"
        path.write_text(json.dumps({"keys": ["notanobject"]}), encoding="utf-8")

        with pytest.raises(IdentityBindingError, match="must be an object"):
            load_identity_trust_bundle(path)

    def test_blank_key_id_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "trust.json"
        path.write_text(
            json.dumps({"keys": [{"key_id": "  ", "public_key": "AA==", "senders": ["a/b"]}]}),
            encoding="utf-8",
        )

        with pytest.raises(IdentityBindingError, match="non-empty key_id"):
            load_identity_trust_bundle(path)

    def test_invalid_base64_public_key_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "trust.json"
        path.write_text(
            json.dumps({"keys": [{"key_id": "k", "public_key": "!!!", "senders": ["a/b"]}]}),
            encoding="utf-8",
        )

        with pytest.raises(IdentityBindingError, match="invalid base64 public_key"):
            load_identity_trust_bundle(path)

    def test_wrong_public_key_length_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "trust.json"
        short = base64.b64encode(b"tooshort").decode("ascii")
        path.write_text(
            json.dumps({"keys": [{"key_id": "k", "public_key": short, "senders": ["a/b"]}]}),
            encoding="utf-8",
        )

        with pytest.raises(IdentityBindingError, match="raw Ed25519 bytes"):
            load_identity_trust_bundle(path)

    def test_no_senders_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "trust.json"
        _write_bundle(path, senders=[])

        with pytest.raises(IdentityBindingError, match="at least one sender"):
            load_identity_trust_bundle(path)

    def test_senders_must_be_a_list(self, tmp_path: Path) -> None:
        path = tmp_path / "trust.json"
        _write_bundle(path, senders="a/b")

        with pytest.raises(IdentityBindingError, match="must be a list"):
            load_identity_trust_bundle(path)

    def test_non_boolean_revoked_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "trust.json"
        _write_bundle(path, revoked="yes")

        with pytest.raises(IdentityBindingError, match="revoked must be a boolean"):
            load_identity_trust_bundle(path)

    def test_non_numeric_expiry_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "trust.json"
        _write_bundle(path, expires_at="soon")

        with pytest.raises(IdentityBindingError, match="expires_at must be a number"):
            load_identity_trust_bundle(path)

    def test_infinite_expiry_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "trust.json"
        # 1e400 parses to inf under json
        path.write_text(
            '{"keys": [{"key_id": "k", "public_key": "'
            + base64.b64encode(b"x" * 32).decode("ascii")
            + '", "senders": ["a/b"], "expires_at": 1e400}]}',
            encoding="utf-8",
        )

        with pytest.raises(IdentityBindingError, match="expires_at must be finite"):
            load_identity_trust_bundle(path)

    def test_nan_expiry_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "trust.json"
        # Python's json.loads accepts the NaN token; a NaN expiry would never compare
        # as elapsed, silently making the key non-expiring, so it must be rejected.
        path.write_text(
            '{"keys": [{"key_id": "k", "public_key": "'
            + base64.b64encode(b"x" * 32).decode("ascii")
            + '", "senders": ["a/b"], "expires_at": NaN}]}',
            encoding="utf-8",
        )

        with pytest.raises(IdentityBindingError, match="expires_at must be finite"):
            load_identity_trust_bundle(path)

    def test_duplicate_key_id_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "trust.json"
        pub = base64.b64encode(b"x" * 32).decode("ascii")
        path.write_text(
            json.dumps(
                {
                    "keys": [
                        {"key_id": "dup", "public_key": pub, "senders": ["a/b"]},
                        {"key_id": "dup", "public_key": pub, "senders": ["a/c"]},
                    ]
                }
            ),
            encoding="utf-8",
        )

        with pytest.raises(IdentityBindingError, match="duplicate key id"):
            load_identity_trust_bundle(path)

    def test_expands_home_relative_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        store = tmp_path / ".synapse" / "identity-trust.json"
        store.parent.mkdir(parents=True)
        _write_bundle(store)

        assert _KEY_ID in load_identity_trust_bundle("~/.synapse/identity-trust.json").keys


class TestVerifyRegistration:
    def test_valid_signature_verifies(self, tmp_path: Path) -> None:
        path = tmp_path / "trust.json"
        private_key = _write_bundle(path)
        bundle = load_identity_trust_bundle(path)
        frame = _registration_frame(private_key, sender=_SENDER, signed_at=1000.0)

        result = verify_registration(
            frame, trust_bundle=bundle, now=1000.0, required_sender=_SENDER
        )

        assert result is SignedEventVerificationResult.VALID

    def test_unsigned_frame_is_missing_signature(self, tmp_path: Path) -> None:
        path = tmp_path / "trust.json"
        _write_bundle(path)
        bundle = load_identity_trust_bundle(path)

        result = verify_registration(
            {"sender": _SENDER, "type": "heartbeat"},
            trust_bundle=bundle,
            now=1000.0,
            required_sender=_SENDER,
        )

        assert result is SignedEventVerificationResult.MISSING_SIGNATURE

    def test_other_identity_cannot_impersonate(self, tmp_path: Path) -> None:
        # The key is bound to _SENDER; a frame claiming a different sender is refused
        # even though the signature itself is valid for the key.
        path = tmp_path / "trust.json"
        private_key = _write_bundle(path)
        bundle = load_identity_trust_bundle(path)
        frame = _registration_frame(private_key, sender="SYNAPSE-CHANNEL/evil", signed_at=1000.0)

        result = verify_registration(
            frame, trust_bundle=bundle, now=1000.0, required_sender="SYNAPSE-CHANNEL/evil"
        )

        assert result is SignedEventVerificationResult.SENDER_MISMATCH

    def test_revoked_key_is_refused(self, tmp_path: Path) -> None:
        path = tmp_path / "trust.json"
        private_key = _write_bundle(path, revoked=True)
        bundle = load_identity_trust_bundle(path)
        frame = _registration_frame(private_key, sender=_SENDER, signed_at=1000.0)

        result = verify_registration(
            frame, trust_bundle=bundle, now=1000.0, required_sender=_SENDER
        )

        assert result is SignedEventVerificationResult.REVOKED_KEY

    def test_expired_key_is_refused(self, tmp_path: Path) -> None:
        path = tmp_path / "trust.json"
        private_key = _write_bundle(path, expires_at=500.0)
        bundle = load_identity_trust_bundle(path)
        frame = _registration_frame(private_key, sender=_SENDER, signed_at=1000.0)

        result = verify_registration(
            frame, trust_bundle=bundle, now=1000.0, required_sender=_SENDER
        )

        assert result is SignedEventVerificationResult.EXPIRED

    def test_replayed_signature_is_refused(self, tmp_path: Path) -> None:
        path = tmp_path / "trust.json"
        private_key = _write_bundle(path)
        bundle = load_identity_trust_bundle(path)
        frame = _registration_frame(private_key, sender=_SENDER, signed_at=1000.0)

        first = verify_registration(frame, trust_bundle=bundle, now=1000.0, required_sender=_SENDER)
        second = verify_registration(
            frame, trust_bundle=bundle, now=1000.0, required_sender=_SENDER
        )

        assert first is SignedEventVerificationResult.VALID
        assert second is SignedEventVerificationResult.REPLAYED

    def test_unknown_key_is_refused(self, tmp_path: Path) -> None:
        path = tmp_path / "trust.json"
        _write_bundle(path)
        bundle = load_identity_trust_bundle(path)
        # A frame signed by a key the bundle does not carry.
        stranger = Ed25519PrivateKey.generate()
        frame = _registration_frame(stranger, sender=_SENDER, signed_at=1000.0)
        frame["signature"]["key_id"] = "not-in-bundle"

        result = verify_registration(
            frame, trust_bundle=bundle, now=1000.0, required_sender=_SENDER
        )

        assert result is SignedEventVerificationResult.UNKNOWN_KEY


class TestEnroll:
    def test_creates_a_new_bundle(self, tmp_path: Path) -> None:
        path = tmp_path / "trust.json"
        private_key = Ed25519PrivateKey.generate()

        enroll_identity_key(
            path, key_id=_KEY_ID, public_key_b64=_public_b64(private_key), senders=[_SENDER]
        )

        assert load_identity_trust_bundle(path).keys[_KEY_ID].senders == frozenset({_SENDER})

    def test_appends_to_an_existing_bundle(self, tmp_path: Path) -> None:
        path = tmp_path / "trust.json"
        first = Ed25519PrivateKey.generate()
        second = Ed25519PrivateKey.generate()
        enroll_identity_key(path, key_id="k1", public_key_b64=_public_b64(first), senders=["a/one"])

        enroll_identity_key(
            path, key_id="k2", public_key_b64=_public_b64(second), senders=["a/two"]
        )

        assert set(load_identity_trust_bundle(path).keys) == {"k1", "k2"}

    def test_carries_expiry(self, tmp_path: Path) -> None:
        path = tmp_path / "trust.json"
        private_key = Ed25519PrivateKey.generate()

        enroll_identity_key(
            path,
            key_id=_KEY_ID,
            public_key_b64=_public_b64(private_key),
            senders=[_SENDER],
            expires_at=1900000000.0,
        )

        assert load_identity_trust_bundle(path).keys[_KEY_ID].expires_at == 1900000000.0

    def test_duplicate_key_id_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "trust.json"
        private_key = Ed25519PrivateKey.generate()
        enroll_identity_key(
            path, key_id=_KEY_ID, public_key_b64=_public_b64(private_key), senders=[_SENDER]
        )

        with pytest.raises(IdentityBindingError, match="already enrolled"):
            enroll_identity_key(
                path,
                key_id=_KEY_ID,
                public_key_b64=_public_b64(Ed25519PrivateKey.generate()),
                senders=["a/other"],
            )

    def test_invalid_public_key_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "trust.json"

        with pytest.raises(IdentityBindingError, match="raw Ed25519 bytes"):
            enroll_identity_key(path, key_id="k", public_key_b64="AA==", senders=["a/b"])

    def test_malformed_existing_bundle_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "trust.json"
        path.write_text("{bad", encoding="utf-8")

        with pytest.raises(IdentityBindingError, match="invalid identity trust JSON"):
            enroll_identity_key(
                path,
                key_id="k",
                public_key_b64=_public_b64(Ed25519PrivateKey.generate()),
                senders=["a/b"],
            )

    def test_shape_guard_on_existing_bundle(self, tmp_path: Path) -> None:
        path = tmp_path / "trust.json"
        path.write_text('{"keys": {}}', encoding="utf-8")

        with pytest.raises(IdentityBindingError, match="mapping with a 'keys' list"):
            enroll_identity_key(
                path,
                key_id="k",
                public_key_b64=_public_b64(Ed25519PrivateKey.generate()),
                senders=["a/b"],
            )

    def test_unwritable_parent_raises(self, tmp_path: Path) -> None:
        blocker = tmp_path / "afile"
        blocker.write_text("x", encoding="utf-8")

        with pytest.raises(IdentityBindingError, match="cannot write identity trust bundle"):
            enroll_identity_key(
                blocker / "sub" / "trust.json",
                key_id="k",
                public_key_b64=_public_b64(Ed25519PrivateKey.generate()),
                senders=["a/b"],
            )

    def test_replace_failure_cleans_up_temp_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "trust.json"

        def _boom(src: object, dst: object) -> None:
            raise RuntimeError("replace failed")

        monkeypatch.setattr(os, "replace", _boom)

        with pytest.raises(RuntimeError, match="replace failed"):
            enroll_identity_key(
                path,
                key_id="k",
                public_key_b64=_public_b64(Ed25519PrivateKey.generate()),
                senders=["a/b"],
            )

        assert list(tmp_path.glob("*.tmp")) == []
