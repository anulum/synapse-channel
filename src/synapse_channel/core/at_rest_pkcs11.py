# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — optional PKCS#11 key-encryption-key backend for at-rest wrapped keys
"""Wrap the at-rest data key with a key-encryption key held on a PKCS#11 token.

A hardware token — a YubiKey PIV, a cloud or network HSM, or SoftHSM for tests — holds an AES
key-encryption key that never leaves the device (it can be sensitive and non-extractable). It wraps
and unwraps the random data key that :class:`~synapse_channel.core.at_rest.AtRestCipher` uses for
bulk AES-GCM, via RFC 3394 AES key wrap (``CKM_AES_KEY_WRAP`` / ``C_WrapKey`` / ``C_UnwrapKey``).

This is the hardware counterpart to the software passphrase backend, sharing the same wrapped-key
file format (recorded with ``backend`` = :data:`PKCS11_BACKEND`) and the same
:class:`~synapse_channel.core.at_rest.KeyEncryptionKey` shape. Only the ``python-pkcs11`` optional
dependency and a PKCS#11 module path are needed; importing this module never requires them —
:func:`require_pkcs11` raises a clear error only when a token operation is actually attempted.

The unwrapped data key is returned to the process because ``AtRestCipher`` runs AES-GCM in software;
the key-encryption key stays on the token. That is standard envelope encryption: the long-term
secret is hardware-protected, while the ephemeral data key lives in process memory during use.
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

PKCS11_BACKEND = "pkcs11"
"""Wrapped-key ``backend`` tag for a key-encryption key held on a PKCS#11 token."""

DEFAULT_KEK_LABEL = "synapse-at-rest-kek"
"""Default label for the token's at-rest key-encryption key object."""


def require_pkcs11() -> Any:
    """Return the ``pkcs11`` module, raising a clear error when it is not installed.

    Raises
    ------
    RuntimeError
        When the optional ``python-pkcs11`` dependency is absent.
    """
    try:
        import pkcs11
    except ImportError as exc:  # pragma: no cover - exercised via a patched import in tests.
        raise RuntimeError(
            "the PKCS#11 at-rest key backend requires the optional 'python-pkcs11' dependency; "
            "install it with: pip install synapse-channel[pkcs11]"
        ) from exc
    return pkcs11


class Pkcs11KeyEncryptionKey:
    """A key-encryption key held on a PKCS#11 token, wrapping via RFC 3394 AES key wrap.

    Implements the :class:`~synapse_channel.core.at_rest.KeyEncryptionKey` protocol. The token's AES
    key is used only via its ``C_WrapKey`` / ``C_UnwrapKey`` operations, so it can stay sensitive,
    non-extractable, and never leave the device.
    """

    def __init__(self, session: Any, kek: Any) -> None:
        self._session = session
        self._kek = kek

    def wrap(self, data_key: bytes) -> bytes:
        """Wrap ``data_key`` under the token key-encryption key (``C_WrapKey``, AES key wrap)."""
        pkcs11 = require_pkcs11()
        dek_key = self._session.create_object(
            {
                pkcs11.Attribute.CLASS: pkcs11.ObjectClass.SECRET_KEY,
                pkcs11.Attribute.KEY_TYPE: pkcs11.KeyType.AES,
                pkcs11.Attribute.VALUE: data_key,
                pkcs11.Attribute.TOKEN: False,
                pkcs11.Attribute.EXTRACTABLE: True,
                pkcs11.Attribute.SENSITIVE: False,
            }
        )
        wrapped: bytes = self._kek.wrap_key(dek_key, mechanism=pkcs11.Mechanism.AES_KEY_WRAP)
        return wrapped

    def unwrap(self, wrapped: bytes) -> bytes:
        """Recover the data key from ``wrapped`` (``C_UnwrapKey``, AES key wrap)."""
        pkcs11 = require_pkcs11()
        unwrapped = self._kek.unwrap_key(
            pkcs11.ObjectClass.SECRET_KEY,
            pkcs11.KeyType.AES,
            wrapped,
            mechanism=pkcs11.Mechanism.AES_KEY_WRAP,
            template={
                pkcs11.Attribute.TOKEN: False,
                pkcs11.Attribute.EXTRACTABLE: True,
                pkcs11.Attribute.SENSITIVE: False,
            },
        )
        value: bytes = unwrapped[pkcs11.Attribute.VALUE]
        return value


def _find_kek(session: Any, key_label: str) -> Any | None:
    """Return the token key-encryption key object with ``key_label``, or ``None`` if absent."""
    pkcs11 = require_pkcs11()
    matches = list(
        session.get_objects(
            {
                pkcs11.Attribute.LABEL: key_label,
                pkcs11.Attribute.CLASS: pkcs11.ObjectClass.SECRET_KEY,
            }
        )
    )
    return matches[0] if matches else None


def _generate_kek(session: Any, key_label: str) -> Any:
    """Generate a sensitive, non-extractable AES-256 key-encryption key on the token."""
    pkcs11 = require_pkcs11()
    return session.generate_key(
        pkcs11.KeyType.AES,
        256,
        label=key_label,
        store=True,
        template={
            pkcs11.Attribute.TOKEN: True,
            pkcs11.Attribute.WRAP: True,
            pkcs11.Attribute.UNWRAP: True,
            pkcs11.Attribute.SENSITIVE: True,
            pkcs11.Attribute.EXTRACTABLE: False,
        },
    )


@contextlib.contextmanager
def _open_kek(
    *, module_path: str, token_label: str, key_label: str, pin: str, create: bool
) -> Iterator[Pkcs11KeyEncryptionKey]:
    """Open the token and yield its at-rest key-encryption key, generating it when ``create``."""
    pkcs11 = require_pkcs11()
    lib = pkcs11.lib(module_path)
    token = lib.get_token(token_label=token_label)
    with token.open(user_pin=pin, rw=create) as session:
        kek = _find_kek(session, key_label)
        if kek is None:
            if not create:
                raise ValueError(
                    f"no PKCS#11 key-encryption key labelled {key_label!r} on token {token_label!r}"
                )
            kek = _generate_kek(session, key_label)
        yield Pkcs11KeyEncryptionKey(session, kek)


def generate_wrapped_key_file_pkcs11(
    path: str | Path,
    *,
    module_path: str,
    token_label: str,
    pin: str,
    key_label: str = DEFAULT_KEK_LABEL,
    create_kek: bool = True,
) -> Path:
    """Write a random data key wrapped by a PKCS#11 token key-encryption key, owner-only.

    Parameters
    ----------
    path : str or pathlib.Path
        Destination wrapped-key file; refused when it already exists.
    module_path : str
        Filesystem path to the PKCS#11 module (``.so``/``.dll``) for the token.
    token_label : str
        Label of the token that holds (or will hold) the key-encryption key.
    pin : str
        User PIN for the token.
    key_label : str, optional
        Label of the token key-encryption key object.
    create_kek : bool, optional
        Generate the key-encryption key on the token when it does not yet exist (default). When
        ``False``, an absent key is an error rather than silently created.

    Returns
    -------
    pathlib.Path
        The written wrapped-key file path.

    Raises
    ------
    ValueError
        When ``create_kek`` is ``False`` and the key-encryption key is absent.
    FileExistsError
        When ``path`` already exists, so an existing key is never overwritten.
    """
    data_key = secrets.token_bytes(KEY_BYTES)
    with _open_kek(
        module_path=module_path,
        token_label=token_label,
        key_label=key_label,
        pin=pin,
        create=create_kek,
    ) as kek:
        wrapped = kek.wrap(data_key)
    params = {"token_label": token_label, "key_label": key_label}
    document = _write_wrapped_key_document(backend=PKCS11_BACKEND, params=params, wrapped=wrapped)
    return _write_new_key_file(Path(path), document)


def cipher_from_wrapped_key_file_pkcs11(
    path: str | Path, *, module_path: str, pin: str
) -> AtRestCipher:
    """Build a cipher from a PKCS#11-wrapped key file, unwrapping the data key on the token.

    Parameters
    ----------
    path : str or pathlib.Path
        Wrapped-key file written by :func:`generate_wrapped_key_file_pkcs11`.
    module_path : str
        Filesystem path to the PKCS#11 module for the token.
    pin : str
        User PIN for the token.

    Returns
    -------
    AtRestCipher
        A cipher bound to the unwrapped data key.

    Raises
    ------
    ValueError
        When the file is not a PKCS#11-wrapped key file, its params are malformed, or the token has
        no matching key-encryption key.
    """
    backend, params, wrapped = _read_wrapped_key_document(Path(path))
    if backend != PKCS11_BACKEND:
        raise ValueError(f"wrapped key file uses the {backend!r} backend, not PKCS#11: {path}")
    try:
        token_label = str(params["token_label"])
        key_label = str(params["key_label"])
    except KeyError as exc:
        raise ValueError(f"malformed PKCS#11 wrapped at-rest key file: {path}") from exc
    with _open_kek(
        module_path=module_path,
        token_label=token_label,
        key_label=key_label,
        pin=pin,
        create=False,
    ) as kek:
        data_key = kek.unwrap(wrapped)
    return AtRestCipher(data_key)
