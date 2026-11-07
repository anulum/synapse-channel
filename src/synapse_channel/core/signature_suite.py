# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — signature suites: Ed25519 and a hybrid Ed25519 + ML-DSA-65 composite
"""Named signature suites, so a signer can change algorithms without changing callers.

Two suites exist:

* ``ed25519`` signs ``domain || NUL || message``, the convention of the
  existing preimages.
* ``ed25519+ml-dsa-65/v1`` is an AND composite. Both halves sign the composite
  preimage::

      SYNAPSE-COMPOSITE-SIG-v1 || NUL || suite || NUL || domain || NUL || message

  ML-DSA-65 also takes the domain as its FIPS 204 context. Because the label
  and the suite name are inside what each half signs, neither half verifies as
  a stand-alone signature over the plain domain preimage. The public key is the
  32-byte Ed25519 key followed by the 1952-byte ML-DSA-65 key, and a signature
  is the 64-byte Ed25519 signature followed by the 3309-byte ML-DSA-65
  signature. Both halves must verify. The encoding version is part of the
  suite name, so a later encoding is a new suite, never a change to this one.

Composite key halves belong to the composite only: a composite key file names
its suite, and the loader refuses it for any other suite. ML-DSA support depends
on the ``cryptography`` backend; :data:`ML_DSA_AVAILABLE` records the probe made
at import, and selecting the composite without it is refused with a clear error.
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from synapse_channel.core.errors import SynapseError
from synapse_channel.core.receipt_signing import receipt_key_id
from synapse_channel.core.secret_files import read_secret_file

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.mldsa import MLDSA65PrivateKey

SUITE_ED25519 = "ed25519"
SUITE_HYBRID = "ed25519+ml-dsa-65/v1"
SUITES = (SUITE_ED25519, SUITE_HYBRID)
"""Every suite name this module knows."""

COMPOSITE_LABEL = b"SYNAPSE-COMPOSITE-SIG-v1"
KEY_FILE_FORMAT = "synapse-signature-key-v1"
TRUST_FILE_FORMAT = "synapse-signature-trust-v1"

_ED25519_PUBLIC = 32
_ED25519_SIGNATURE = 64
_ML_DSA_PUBLIC = 1952
_ML_DSA_SIGNATURE = 3309
_SEED = 32
_NUL = b"\x00"


class SignatureSuiteError(SynapseError, ValueError):
    """A suite, key, key file or signature input is unknown, malformed or unavailable."""

    code = "signature_suite"


def _probe_ml_dsa() -> bool:
    try:
        from cryptography.hazmat.primitives.asymmetric import mldsa

        mldsa.MLDSA65PrivateKey.from_seed_bytes(bytes(_SEED))
    except (ImportError, AttributeError, ValueError, TypeError):
        return False
    return True


ML_DSA_AVAILABLE = _probe_ml_dsa()
"""Whether the installed ``cryptography`` backend provides ML-DSA-65."""


def _require_ml_dsa() -> None:
    if not ML_DSA_AVAILABLE:
        raise SignatureSuiteError(
            f"{SUITE_HYBRID} needs ML-DSA-65, which this cryptography backend does not provide"
        )


def _require_suite(suite: str) -> None:
    if suite not in SUITES:
        raise SignatureSuiteError(f"unknown signature suite {suite!r}")


def _text_part(label: str, value: str) -> bytes:
    if "\x00" in value:
        raise SignatureSuiteError(f"a signature {label} may not contain a NUL byte")
    return value.encode("utf-8")


def preimage(suite: str, domain: str, message: bytes) -> bytes:
    """Return the bytes that ``suite`` signs for ``message`` under ``domain``.

    Raises
    ------
    SignatureSuiteError
        For an unknown suite, or a suite name or domain holding a NUL byte.
    """
    _require_suite(suite)
    domain_bytes = _text_part("domain", domain)
    if suite == SUITE_ED25519:
        return domain_bytes + _NUL + message
    return _NUL.join((COMPOSITE_LABEL, _text_part("suite", suite), domain_bytes, message))


@dataclass(frozen=True)
class SuiteKey:
    """A private signing key of one suite, held as its seeds."""

    suite: str
    ed25519_seed: bytes
    ml_dsa_seed: bytes = b""

    def __post_init__(self) -> None:
        """Refuse a key whose seeds do not fit its suite."""
        _require_suite(self.suite)
        if len(self.ed25519_seed) != _SEED:
            raise SignatureSuiteError("an Ed25519 seed is 32 bytes")
        expected = _SEED if self.suite == SUITE_HYBRID else 0
        if len(self.ml_dsa_seed) != expected:
            raise SignatureSuiteError(f"a {self.suite} key holds {expected} ML-DSA seed bytes")

    def _ml_dsa(self) -> MLDSA65PrivateKey:
        _require_ml_dsa()
        from cryptography.hazmat.primitives.asymmetric import mldsa

        return mldsa.MLDSA65PrivateKey.from_seed_bytes(self.ml_dsa_seed)

    def public_bytes(self) -> bytes:
        """Return the suite's public key bytes."""
        ed25519 = Ed25519PrivateKey.from_private_bytes(self.ed25519_seed).public_key()
        public = ed25519.public_bytes_raw()
        if self.suite == SUITE_HYBRID:
            public += self._ml_dsa().public_key().public_bytes_raw()
        return public

    def key_id(self) -> str:
        """Return the id derived from the public key, as receipt key ids are."""
        return receipt_key_id(self.public_bytes())

    def sign(self, domain: str, message: bytes) -> bytes:
        """Sign ``message`` under ``domain`` with every half of this suite."""
        data = preimage(self.suite, domain, message)
        signature = Ed25519PrivateKey.from_private_bytes(self.ed25519_seed).sign(data)
        if self.suite == SUITE_HYBRID:
            signature += self._ml_dsa().sign(data, context=_text_part("domain", domain))
        return signature


def generate_key(suite: str) -> SuiteKey:
    """Return a new random key of ``suite``."""
    import secrets

    _require_suite(suite)
    if suite == SUITE_HYBRID:
        _require_ml_dsa()
        return SuiteKey(suite, secrets.token_bytes(_SEED), secrets.token_bytes(_SEED))
    return SuiteKey(suite, secrets.token_bytes(_SEED))


def _verify_ed25519(public: bytes, data: bytes, signature: bytes) -> bool:
    try:
        Ed25519PublicKey.from_public_bytes(public).verify(signature, data)
    except InvalidSignature:
        return False
    return True


def _verify_ml_dsa(public: bytes, data: bytes, signature: bytes, context: bytes) -> bool:
    from cryptography.hazmat.primitives.asymmetric import mldsa

    try:
        mldsa.MLDSA65PublicKey.from_public_bytes(public).verify(signature, data, context=context)
    except InvalidSignature:
        return False
    return True


def verify(suite: str, public: bytes, domain: str, message: bytes, signature: bytes) -> bool:
    """Return whether ``signature`` is ``suite``'s valid signature of ``message``.

    A wrong length, a swapped order, or one composite half alone is ``False``.

    Raises
    ------
    SignatureSuiteError
        For an unknown suite, a NUL in the suite or domain, or the composite on
        a backend without ML-DSA.
    """
    data = preimage(suite, domain, message)
    if suite == SUITE_ED25519:
        return (
            len(public) == _ED25519_PUBLIC
            and len(signature) == _ED25519_SIGNATURE
            and _verify_ed25519(public, data, signature)
        )
    _require_ml_dsa()
    if len(public) != _ED25519_PUBLIC + _ML_DSA_PUBLIC:
        return False
    if len(signature) != _ED25519_SIGNATURE + _ML_DSA_SIGNATURE:
        return False
    checks: tuple[Callable[[], bool], ...] = (
        lambda: _verify_ed25519(public[:_ED25519_PUBLIC], data, signature[:_ED25519_SIGNATURE]),
        lambda: _verify_ml_dsa(
            public[_ED25519_PUBLIC:],
            data,
            signature[_ED25519_SIGNATURE:],
            _text_part("domain", domain),
        ),
    )
    return all(check() for check in checks)


def b64(data: bytes) -> str:
    """Return canonical URL-safe base64 text for ``data``."""
    return base64.urlsafe_b64encode(data).decode("ascii")


def unb64(text: object, label: str) -> bytes:
    """Decode canonical unpadded-or-padded URL-safe base64, refusing anything else."""
    if not isinstance(text, str):
        raise SignatureSuiteError(f"{label} must be base64 text")
    try:
        data = base64.b64decode(text, altchars=b"-_", validate=True)
    except (binascii.Error, ValueError):
        raise SignatureSuiteError(f"{label} is not valid base64") from None
    if b64(data) != text:
        raise SignatureSuiteError(f"{label} is not canonical base64")
    return data


def key_document(key: SuiteKey) -> str:
    """Return the owner-only key file text for ``key``."""
    document: dict[str, object] = {
        "format": KEY_FILE_FORMAT,
        "suite": key.suite,
        "ed25519_seed": b64(key.ed25519_seed),
    }
    if key.suite == SUITE_HYBRID:
        document["ml_dsa_seed"] = b64(key.ml_dsa_seed)
    return json.dumps(document, sort_keys=True) + "\n"


def load_key(path: str | Path, *, suite: str) -> SuiteKey:
    """Load an owner-only key file that must belong to ``suite``.

    The file goes through the shared owner-only secret reader, which refuses a
    group- or world-readable file. A key file of another suite is refused, so
    composite halves are never used by a single-algorithm suite.
    """
    _require_suite(suite)
    raw = read_secret_file(path, flag="--key", require_single_link=True)
    try:
        document = json.loads(raw)
    except json.JSONDecodeError:
        raise SignatureSuiteError("the key file is not JSON") from None
    if not isinstance(document, dict) or document.get("format") != KEY_FILE_FORMAT:
        raise SignatureSuiteError(f"the key file is not {KEY_FILE_FORMAT}")
    if document.get("suite") != suite:
        raise SignatureSuiteError(f"the key file belongs to {document.get('suite')!r}, not {suite}")
    ml_dsa = document.get("ml_dsa_seed")
    return SuiteKey(
        suite,
        unb64(document.get("ed25519_seed"), "ed25519_seed"),
        unb64(ml_dsa, "ml_dsa_seed") if ml_dsa is not None else b"",
    )


def trust_document(key: SuiteKey) -> str:
    """Return a trust file naming ``key``'s public key, its suite and id."""
    entry = {"key_id": key.key_id(), "suite": key.suite, "public_key": b64(key.public_bytes())}
    return json.dumps({"format": TRUST_FILE_FORMAT, "keys": [entry]}, indent=2) + "\n"


def load_trust(path: str | Path) -> dict[str, tuple[str, bytes]]:
    """Return ``key_id -> (suite, public key)`` from a trust file.

    Each id must be the one derived from its public key.
    """
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SignatureSuiteError(f"cannot read the trust file {path}: {exc}") from None
    if not isinstance(document, dict) or document.get("format") != TRUST_FILE_FORMAT:
        raise SignatureSuiteError(f"the trust file is not {TRUST_FILE_FORMAT}")
    keys = document.get("keys")
    if not isinstance(keys, list) or not keys:
        raise SignatureSuiteError("the trust file names no keys")
    trust: dict[str, tuple[str, bytes]] = {}
    for entry in keys:
        if not isinstance(entry, dict):
            raise SignatureSuiteError("a trust file key entry is not an object")
        suite = entry.get("suite")
        if not isinstance(suite, str):
            raise SignatureSuiteError("a trust file key names no suite")
        _require_suite(suite)
        public = unb64(entry.get("public_key"), "public_key")
        if entry.get("key_id") != receipt_key_id(public):
            raise SignatureSuiteError("a trust file key id is not derived from its public key")
        trust[receipt_key_id(public)] = (suite, public)
    return trust
