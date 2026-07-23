# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Ed25519 identity signing keys: generate, store, load, and sign registration
"""Ed25519 identity signing keys — the private half of connection-identity binding.

An identity key proves a socket is the identity it registers as: the connecting agent
signs its registration frame with the private key, and the hub verifies it against the
public half enrolled in its identity trust bundle
(:mod:`synapse_channel.core.identity_binding`). This module owns the private-key
lifecycle an operator and an agent need — generate a keypair, write the private key to
an owner-only file, load it back, and sign a registration frame — while the trust and
verification side stays in :mod:`~synapse_channel.core.identity_binding`.

Key material is deliberately separate from the receipt-signing and federation keys:
proving *who a connection is* is a different credential from signing durable receipts
or peering a federation domain, so each keeps its own key file.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from synapse_channel.core.errors import SynapseError
from synapse_channel.core.message_auth import sign_legacy_event_frame

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

SIGNING_KEY_FILE_MODE = 0o600
"""Owner-only permissions for a private identity key file."""


class IdentityKeyError(SynapseError, ValueError):
    """Raised when an identity key cannot be generated, written, or loaded."""

    code = "identity_key"


def generate_signing_key() -> Ed25519PrivateKey:
    """Return a fresh Ed25519 identity signing key."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    return Ed25519PrivateKey.generate()


def public_key_b64(private_key: Ed25519PrivateKey) -> str:
    """Return the base64 raw Ed25519 public key, as enrolled in a trust bundle."""
    from cryptography.hazmat.primitives import serialization

    raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    return base64.b64encode(raw).decode("ascii")


def write_signing_key(path: str | Path, private_key: Ed25519PrivateKey) -> None:
    """Write ``private_key`` to ``path`` as owner-only PKCS#8 PEM, never overwriting.

    The write is exclusive-create at ``0o600``, so an existing key is never silently
    clobbered — regenerating over a live identity is an error, not a surprise.

    Raises
    ------
    IdentityKeyError
        When the file already exists or cannot be written.
    """
    from cryptography.hazmat.primitives import serialization

    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    from synapse_channel.core.secure_path import SecurePathError, apply_owner_only_file

    target = Path(path).expanduser()
    try:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, SIGNING_KEY_FILE_MODE)
    except OSError as exc:
        raise IdentityKeyError(f"cannot write identity key {target}: {exc}") from exc
    try:
        os.write(fd, pem)
    finally:
        os.close(fd)
    try:
        apply_owner_only_file(target)
    except SecurePathError as exc:
        target.unlink(missing_ok=True)
        raise IdentityKeyError(f"cannot secure identity key {target}: {exc}") from exc


def load_signing_key(path: str | Path) -> Ed25519PrivateKey:
    """Load an Ed25519 identity signing key from a PKCS#8 PEM file.

    The private PEM is read through the shared owner-only secret floor
    (``O_NOFOLLOW``, euid owner, mode without group/other bits) so identity
    and capability-card keys match connect-token and receipt-key discipline.
    Errors never include key material.

    Raises
    ------
    IdentityKeyError
        When the file is missing, is not owner-only, is not PEM, or does not
        hold an Ed25519 key.
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from synapse_channel.core.secret_files import SecretFileError, read_secret_file

    target = Path(path).expanduser()
    try:
        # PEM is UTF-8 text; outer strip matches exclusive 0o600 write.
        pem_text = read_secret_file(target, flag="identity-signing-key")
    except SecretFileError as exc:
        raise IdentityKeyError(f"cannot read identity key {target}: {exc}") from exc
    pem = pem_text.encode("utf-8")
    try:
        private_key = serialization.load_pem_private_key(pem, password=None)
    except (ValueError, TypeError) as exc:
        raise IdentityKeyError(f"identity key {target} is not a valid PEM key") from exc
    if not isinstance(private_key, Ed25519PrivateKey):
        raise IdentityKeyError(f"identity key {target} must be Ed25519")
    return private_key


def sign_registration(
    frame: dict[str, Any],
    *,
    private_key: Ed25519PrivateKey,
    key_id: str,
    nonce: str,
    sequence: int,
    signed_at: float | None = None,
) -> dict[str, Any]:
    """Return ``frame`` with an Ed25519 identity signature the hub verifies at registration.

    Registration is a pre-admission bootstrap and cannot negotiate the new AEF
    event-signature profile. It therefore emits the exact legacy-v1 profile so
    upgraded clients can still prove identity to a hub that has not restarted
    into the v2 verifier. The ``nonce`` and monotonic ``sequence`` make each
    registration signature single-use against the hub's replay cache.
    """
    return sign_legacy_event_frame(
        frame,
        key_id=key_id,
        private_key=private_key,
        nonce=nonce,
        sequence=sequence,
        signed_at=signed_at,
    )
