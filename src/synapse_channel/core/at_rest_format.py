# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — byte-format contract and primitive access for at-rest encryption
"""Byte-format constants and lazy AES-GCM primitive access for at-rest encryption.

This module owns the versioned envelope magic, key and nonce lengths, scrypt
defaults, wrapped-key and backup-manifest schema markers, AES-GCM per-key
message limits, and the optional ``cryptography`` AES-GCM accessor. Importing
this module never imports ``cryptography`` eagerly: :func:`require_aes_gcm`
loads it only when an encryption operation is actually requested.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ENVELOPE_MAGIC = b"SYNAR\x01\x00\x00"
"""Versioned envelope header bound into the AES-GCM additional authenticated data."""

KEY_BYTES = 32
"""AES-256-GCM key length in bytes."""

NONCE_BYTES = 12
"""AES-GCM nonce length in bytes."""

DEFAULT_SCRYPT_N = 2**15
"""Default scrypt CPU/memory cost; a power of two."""

DEFAULT_SCRYPT_R = 8
"""Default scrypt block size parameter."""

DEFAULT_SCRYPT_P = 1
"""Default scrypt parallelisation parameter."""

SCRYPT_SALT_BYTES = 16
"""Salt length, in bytes, for passphrase key derivation."""

BACKUP_MANIFEST_SCHEMA = "synapse-at-rest-backup.v2"
"""Schema marker for at-rest encrypted backup manifests.

Version 2 binds every copy in the bundle by SHA-256 and seals the entry list with the profile key.
"""

LEGACY_BACKUP_MANIFEST_SCHEMAS = frozenset({"synapse-at-rest-backup.v1"})
"""Earlier backup manifest markers, which bound no copy digest and are refused on restore."""

WRAPPED_KEY_SCHEMA = "synapse-at-rest-wrapped-key.v1"
"""Schema marker for an envelope-encrypted (KEK-wrapped) at-rest key file.

A random data key does the bulk AES-GCM; a key-encryption key (KEK) — from a passphrase now, or a
PKCS#11 token, TPM, YubiKey, or cloud HSM in an optional backend — wraps it. The file records which
``backend`` produced it, so a fresh process rebuilds the matching key-encryption key. Because the
data key is unchanged when the KEK rotates, the passphrase (or hardware key) can change without
re-encrypting any stored data.
"""

PASSPHRASE_SCRYPT_BACKEND = "passphrase-scrypt"  # nosec B105 # backend identifier, not a secret
"""Wrapped-key ``backend`` tag for the default software KEK (a passphrase derived with scrypt)."""

GCM_MESSAGE_LIMIT = 2**32
"""Per-key AES-GCM message cap for random 96-bit nonces (NIST SP 800-38D).

Sealing more than this many messages under one key with random nonces lets the
nonce-collision probability climb past the 2**-32 safety bound, weakening the
confidentiality guarantee. :class:`AtRestCipher` refuses to encrypt past it.
"""

GCM_REKEY_WARNING_THRESHOLD = GCM_MESSAGE_LIMIT - GCM_MESSAGE_LIMIT // 16
"""Message count at which a cipher instance logs a one-time rekey warning.

Set to fifteen-sixteenths of :data:`GCM_MESSAGE_LIMIT`, leaving an operator a
wide margin to rotate the key before the hard cap stops encryption.
"""


class _AESGCMFactory(Protocol):
    """Callable building an AES-GCM cipher from a 32-byte key."""

    def __call__(self, key: bytes) -> AESGCM:
        """Return an AES-GCM cipher bound to ``key``."""


def require_aes_gcm() -> _AESGCMFactory:
    """Return the AES-GCM cipher class, raising a clear error when it is absent.

    Returns
    -------
    _AESGCMFactory
        The ``cryptography`` ``AESGCM`` class.

    Raises
    ------
    RuntimeError
        When the optional ``cryptography`` dependency is not installed.
    """
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:
        raise RuntimeError(
            "at-rest encryption requires the optional 'cryptography' dependency; "
            "install it with: pip install synapse-channel[encryption]"
        ) from exc
    return AESGCM
