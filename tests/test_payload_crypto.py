# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for end-to-end payload crypto envelopes
"""Payload encryption envelope tests."""

from __future__ import annotations

import base64
import os
from collections.abc import Callable
from pathlib import Path

import pytest

from synapse_channel.core.at_rest import generate_key_file
from synapse_channel.core.payload_crypto import (
    PAYLOAD_PLACEHOLDER,
    PayloadContext,
    PayloadCryptoError,
    decrypt_payload,
    encrypt_payload,
    load_payload_key,
    payload_key_fingerprint,
)
from synapse_channel.core.protocol import MessageType


def test_payload_envelope_round_trips_and_hides_plaintext() -> None:
    key = b"k" * 32
    context = PayloadContext(
        message_type=MessageType.CHAT,
        sender="alice",
        target="bob",
        channel="ops",
        task_id="TASK-7",
    )

    envelope = encrypt_payload(
        "rotate the release key",
        key,
        key_id="project:main:v1",
        recipients=["bob", "carol"],
        context=context,
    )

    assert PAYLOAD_PLACEHOLDER == "<encrypted payload>"
    assert envelope["version"] == 1
    assert envelope["key_id"] == "project:main:v1"
    assert envelope["recipients"] == ["bob", "carol"]
    assert "rotate" not in str(envelope)
    assert base64.b64decode(envelope["aad"])
    assert decrypt_payload(envelope, key, context=context) == "rotate the release key"


def test_payload_decryption_rejects_replayed_routing_metadata() -> None:
    key = b"k" * 32
    context = PayloadContext(
        message_type=MessageType.CHAT,
        sender="alice",
        target="bob",
        channel="ops",
    )
    envelope = encrypt_payload(
        "handoff secret",
        key,
        key_id="ops:v1",
        recipients=["bob"],
        context=context,
    )

    with pytest.raises(PayloadCryptoError, match="routing metadata"):
        decrypt_payload(
            envelope,
            key,
            context=PayloadContext(
                message_type=MessageType.CHAT,
                sender="alice",
                target="mallory",
                channel="ops",
            ),
        )


def test_payload_key_file_loader_validates_and_fingerprints(tmp_path: Path) -> None:
    key_path = generate_key_file(tmp_path / "payload.key")

    key = load_payload_key(key_path)

    assert len(key) == 32
    fingerprint = payload_key_fingerprint(key)
    assert len(fingerprint) == 16
    assert payload_key_fingerprint(load_payload_key(key_path)) == fingerprint


def test_payload_key_file_loader_rejects_unsafe_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = tmp_path / "missing.key"
    assert "does not exist" in _error(lambda: load_payload_key(missing))

    unsafe = tmp_path / "unsafe.key"
    unsafe.write_bytes(b"k" * 32)
    unsafe.chmod(0o644)
    assert "owner-only" in _error(lambda: load_payload_key(unsafe))
    unsafe.chmod(0o600)

    short = tmp_path / "short.key"
    short.write_bytes(b"k")
    short.chmod(0o600)
    assert "exactly 32 bytes" in _error(lambda: load_payload_key(short))

    link = tmp_path / "link.key"
    link.symlink_to(unsafe)
    assert "must not be a symlink" in _error(lambda: load_payload_key(link))

    directory = tmp_path / "key-dir"
    directory.mkdir(mode=0o700)
    assert "not a regular file" in _error(lambda: load_payload_key(directory))

    monkeypatch.setattr(os, "geteuid", lambda: -1)
    assert "owned by the current user" in _error(lambda: load_payload_key(unsafe))


def test_payload_key_loader_rejects_short_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key_path = generate_key_file(tmp_path / "payload.key")
    monkeypatch.setattr(os, "read", lambda _fd, _size: b"short")

    assert "exactly 32 bytes" in _error(lambda: load_payload_key(key_path))


def test_payload_crypto_rejects_invalid_keys_and_key_ids() -> None:
    context = PayloadContext(message_type=MessageType.CHAT, sender="alice")

    assert "must be 32 bytes" in _error(lambda: payload_key_fingerprint(b"short"))
    assert "key id is required" in _error(
        lambda: encrypt_payload("secret", b"k" * 32, key_id=" ", recipients=[], context=context)
    )


def test_payload_decryption_rejects_malformed_envelopes() -> None:
    key = b"k" * 32
    context = PayloadContext(message_type=MessageType.CHAT, sender="alice")
    envelope = encrypt_payload("secret", key, key_id="k", recipients=[], context=context)

    bad_version = dict(envelope)
    bad_version["version"] = 99
    assert "unsupported" in _error(lambda: decrypt_payload(bad_version, key, context=context))

    missing_key_id = dict(envelope)
    missing_key_id["key_id"] = ""
    assert "key_id" in _error(lambda: decrypt_payload(missing_key_id, key, context=context))

    bad_recipients = dict(envelope)
    bad_recipients["recipients"] = ["alice", 1]
    assert "recipients" in _error(lambda: decrypt_payload(bad_recipients, key, context=context))

    bad_aad = dict(envelope)
    bad_aad["aad"] = "\u2603"
    assert "base64" in _error(lambda: decrypt_payload(bad_aad, key, context=context))


def test_payload_decryption_rejects_wrong_key_and_invalid_utf8() -> None:
    key = b"k" * 32
    context = PayloadContext(message_type=MessageType.CHAT, sender="alice")
    envelope = encrypt_payload("secret", key, key_id="k", recipients=[], context=context)

    assert "authentication failed" in _error(
        lambda: decrypt_payload(envelope, b"w" * 32, context=context)
    )

    from synapse_channel.core.at_rest import require_aes_gcm

    nonce = b"n" * 12
    aad = base64.urlsafe_b64decode(envelope["aad"].encode("ascii"))
    invalid_utf8 = dict(envelope)
    invalid_utf8["nonce"] = base64.urlsafe_b64encode(nonce).decode("ascii")
    invalid_utf8["ciphertext"] = base64.urlsafe_b64encode(
        require_aes_gcm()(key).encrypt(nonce, b"\xff", aad)
    ).decode("ascii")
    assert "valid UTF-8" in _error(lambda: decrypt_payload(invalid_utf8, key, context=context))


def _error(operation: Callable[[], object]) -> str:
    """Run ``operation`` and return its ``PayloadCryptoError`` text."""
    with pytest.raises(PayloadCryptoError) as exc_info:
        operation()
    return str(exc_info.value)
