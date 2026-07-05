# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — optional TPM 2.0 key-encryption-key backend for at-rest wrapped keys
"""Wrap the at-rest data key with a key-encryption key rooted in a TPM 2.0 device.

A Trusted Platform Module holds a storage primary seed that never leaves the chip. From that
seed and a fixed template this backend derives a deterministic **decrypt-only RSA-2048 primary**
— the same key every process re-creates, so nothing needs to be persisted as a handle — and uses
RSA-OAEP to wrap and unwrap the random data key that
:class:`~synapse_channel.core.at_rest.AtRestCipher` uses for bulk AES-GCM. The RSA private key is
generated inside the TPM and never leaves it; only the public operation could be done off-chip.

This is the TPM counterpart to the PKCS#11 and passphrase backends, sharing the same wrapped-key
file format (recorded with ``backend`` = :data:`TPM2_BACKEND`) and the same
:class:`~synapse_channel.core.at_rest.KeyEncryptionKey` shape. Only the optional ``tpm2-pytss``
dependency and a TPM transmission interface (TCTI) are needed; importing this module never
requires them — :func:`require_tpm2` raises a clear error only when a TPM operation is attempted.

RSA-OAEP is used rather than a sealed AES key because a sealing storage parent needs a symmetric
scheme that some TPM firmware rejects, whereas a decrypt-only RSA primary is universally
supported. As with the other backends the unwrapped data key is returned to the process — AES-GCM
runs in software — while the long-term secret (the RSA private key) stays inside the TPM. That is
standard envelope encryption: rotating the TPM-held key re-wraps the same data key without
re-encrypting stored data.
"""

from __future__ import annotations

import contextlib
import secrets
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from synapse_channel.core.at_rest import (
    KEY_BYTES,
    AtRestCipher,
    _read_wrapped_key_document,
    _write_new_key_file,
    _write_wrapped_key_document,
)

TPM2_BACKEND = "tpm2"
"""Wrapped-key ``backend`` tag for a key-encryption key rooted in a TPM 2.0 device."""

DEFAULT_TPM2_TCTI = "device:/dev/tpmrm0"
"""Default TPM transmission interface: the in-kernel resource-managed device."""

TEMPLATE_VERSION = 1
"""Version of the fixed key-encryption-key template recorded in the wrapped-key file.

The key-encryption key is fully determined by the TPM storage seed and :func:`_kek_template`. This
version is stored so that, should a future release need a different template, a file written today
still records which template derived its key and stays loadable by version-aware code.
"""


def require_tpm2() -> Any:
    """Return the ``tpm2_pytss`` module, raising a clear error when it is not installed.

    Raises
    ------
    RuntimeError
        When the optional ``tpm2-pytss`` dependency is absent.
    """
    try:
        import tpm2_pytss
    except ImportError as exc:  # pragma: no cover - exercised via a patched import in tests.
        raise RuntimeError(
            "the TPM 2.0 at-rest key backend requires the optional 'tpm2-pytss' dependency; "
            "install it with: pip install synapse-channel[tpm2]"
        ) from exc
    return tpm2_pytss


def _kek_template(tpm2: Any) -> Any:
    """Build the fixed decrypt-only RSA-2048 primary template.

    Every field is a code constant, so the template bytes never change and each process derives
    the identical key-encryption key from the TPM's stable storage seed. Built lazily rather than
    kept as a module constant so importing this module never requires ``tpm2-pytss``.
    """
    alg = tpm2.TPM2_ALG
    attrs = tpm2.TPMA_OBJECT
    return tpm2.TPM2B_PUBLIC(
        publicArea=tpm2.TPMT_PUBLIC(
            type=alg.RSA,
            nameAlg=alg.SHA256,
            objectAttributes=(
                attrs.DECRYPT
                | attrs.FIXEDTPM
                | attrs.FIXEDPARENT
                | attrs.SENSITIVEDATAORIGIN
                | attrs.USERWITHAUTH
            ),
            parameters=tpm2.TPMU_PUBLIC_PARMS(
                rsaDetail=tpm2.TPMS_RSA_PARMS(
                    symmetric=tpm2.TPMT_SYM_DEF_OBJECT(algorithm=alg.NULL),
                    scheme=tpm2.TPMT_RSA_SCHEME(scheme=alg.NULL),
                    keyBits=2048,
                    exponent=0,
                )
            ),
        )
    )


def _oaep_scheme(tpm2: Any) -> Any:
    """Build the RSA-OAEP decryption scheme with an explicit SHA-256 hash.

    The hash algorithm must be set on the scheme; leaving it ``NULL`` makes the TPM reject the
    operation with "hash algorithm not supported".
    """
    scheme = tpm2.TPMT_RSA_DECRYPT(scheme=tpm2.TPM2_ALG.OAEP)
    scheme.details.oaep.hashAlg = tpm2.TPM2_ALG.SHA256
    return scheme


class Tpm2KeyEncryptionKey:
    """A key-encryption key rooted in a TPM 2.0 device, wrapping via RSA-OAEP.

    Implements the :class:`~synapse_channel.core.at_rest.KeyEncryptionKey` protocol. The RSA private
    key was generated inside the TPM and is used only through its ``rsa_encrypt`` / ``rsa_decrypt``
    operations on the live primary handle, so it never leaves the device.
    """

    def __init__(self, ectx: Any, primary_handle: Any) -> None:
        self._ectx = ectx
        self._primary = primary_handle

    def wrap(self, data_key: bytes) -> bytes:
        """Wrap ``data_key`` under the TPM key-encryption key (RSA-OAEP encrypt)."""
        tpm2 = require_tpm2()
        ciphertext = self._ectx.rsa_encrypt(
            self._primary,
            tpm2.TPM2B_PUBLIC_KEY_RSA(data_key),
            _oaep_scheme(tpm2),
            tpm2.TPM2B_DATA(),
        )
        return bytes(ciphertext)

    def unwrap(self, wrapped: bytes) -> bytes:
        """Recover the data key from ``wrapped`` (RSA-OAEP decrypt inside the TPM)."""
        tpm2 = require_tpm2()
        plaintext = self._ectx.rsa_decrypt(
            self._primary,
            tpm2.TPM2B_PUBLIC_KEY_RSA(wrapped),
            _oaep_scheme(tpm2),
            tpm2.TPM2B_DATA(),
        )
        return bytes(plaintext)


@contextlib.contextmanager
def _open_kek(*, tcti: str) -> Iterator[Tpm2KeyEncryptionKey]:
    """Connect to the TPM, derive the primary key-encryption key, and yield it.

    The primary is flushed on exit — a TPM has few transient object slots — and the ESAPI context is
    always closed.
    """
    tpm2 = require_tpm2()
    ectx = tpm2.ESAPI(tcti)
    try:
        with contextlib.suppress(Exception):
            # A device already powered up rejects a second startup; that is benign.
            ectx.startup(tpm2.TPM2_SU.CLEAR)
        primary = ectx.create_primary(tpm2.TPM2B_SENSITIVE_CREATE(), _kek_template(tpm2))
        handle = primary[0]
        try:
            yield Tpm2KeyEncryptionKey(ectx, handle)
        finally:
            ectx.flush_context(handle)
    finally:
        ectx.close()


def generate_wrapped_key_file_tpm2(path: str | Path, *, tcti: str = DEFAULT_TPM2_TCTI) -> Path:
    """Write a random data key wrapped by a TPM 2.0 key-encryption key, owner-only.

    Parameters
    ----------
    path : str or pathlib.Path
        Destination wrapped-key file; refused when it already exists.
    tcti : str, optional
        TPM transmission interface (for example ``device:/dev/tpmrm0`` for hardware, or
        ``swtpm:host=127.0.0.1,port=2321`` for a software TPM in tests).

    Returns
    -------
    pathlib.Path
        The written wrapped-key file path.

    Raises
    ------
    FileExistsError
        When ``path`` already exists, so an existing key is never overwritten.
    RuntimeError
        When the optional ``tpm2-pytss`` dependency is absent.
    """
    data_key = secrets.token_bytes(KEY_BYTES)
    with _open_kek(tcti=tcti) as kek:
        wrapped = kek.wrap(data_key)
    params = {"template_version": TEMPLATE_VERSION}
    document = _write_wrapped_key_document(backend=TPM2_BACKEND, params=params, wrapped=wrapped)
    return _write_new_key_file(Path(path), document)


def cipher_from_wrapped_key_file_tpm2(
    path: str | Path, *, tcti: str = DEFAULT_TPM2_TCTI
) -> AtRestCipher:
    """Build a cipher from a TPM-wrapped key file, unwrapping the data key inside the TPM.

    Parameters
    ----------
    path : str or pathlib.Path
        Wrapped-key file written by :func:`generate_wrapped_key_file_tpm2`.
    tcti : str, optional
        TPM transmission interface to reach the same device that wrote the file.

    Returns
    -------
    AtRestCipher
        A cipher bound to the unwrapped data key.

    Raises
    ------
    ValueError
        When the file is not a TPM-wrapped key file, its params are malformed, or its template
        version is not supported by this build.
    RuntimeError
        When the optional ``tpm2-pytss`` dependency is absent.
    """
    backend, params, wrapped = _read_wrapped_key_document(Path(path))
    if backend != TPM2_BACKEND:
        raise ValueError(f"wrapped key file uses the {backend!r} backend, not TPM2: {path}")
    try:
        template_version = int(params["template_version"])
    except (KeyError, ValueError, TypeError) as exc:
        raise ValueError(f"malformed TPM2 wrapped at-rest key file: {path}") from exc
    if template_version != TEMPLATE_VERSION:
        raise ValueError(
            f"unsupported TPM2 key template version {template_version} "
            f"(this build supports {TEMPLATE_VERSION}): {path}"
        )
    with _open_kek(tcti=tcti) as kek:
        data_key = kek.unwrap(wrapped)
    return AtRestCipher(data_key)
