# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — standalone AEF trust-input file (K4-AEF-SURFACE)
"""Read and write the file an independent verifier trusts AEF receipts under.

An auditor verifies a hub's AEF receipts offline, without the hub, against a
trust file they control. The file names every receipt-signing key it accepts and
every log those keys may sign for:

.. code-block:: json

    {
      "format": "aef-trust-v0.1",
      "keys": {
        "<16-hex key_id>": {
          "public_key_hex": "<64 hex: raw Ed25519 public key>",
          "revoked": false,
          "not_before": 1783940400000,
          "not_after": 1815476400000,
          "senders": ["agent-7"]
        }
      },
      "logs": {"<64-hex log_id>": "<key_id that signs this log>"}
    }

``revoked``, ``not_before``, ``not_after`` and ``senders`` are optional per key.
Every ``key_id`` is recomputed from its public key, and every ``log_id`` must
name a listed key. Unknown fields, duplicate JSON keys and values of the wrong
type are refused, so a file cannot mean something other than what it shows. The
public-key field name matches the AEF conformance vectors.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from synapse_channel.core.aef_emission import derive_aef_log_id
from synapse_channel.core.aef_verification import AefTrustedKey, AefTrustStore
from synapse_channel.core.errors import SynapseError
from synapse_channel.core.receipt_signing import receipt_key_id

AEF_TRUST_FORMAT = "aef-trust-v0.1"
"""The ``format`` value of a version 0.1 trust file."""

_MAX_TRUST_BYTES = 1 << 20
_TOP_FIELDS = frozenset({"format", "keys", "logs"})
_KEY_FIELDS = frozenset({"public_key_hex", "revoked", "not_before", "not_after", "senders"})


class AefTrustFileError(SynapseError, ValueError):
    """The AEF trust file is unreadable, malformed or self-inconsistent."""

    code = "aef_trust_file"


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AefTrustFileError(f"AEF trust file repeats the field {key!r}")
        result[key] = value
    return result


def _object(value: object, label: str, allowed: frozenset[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AefTrustFileError(f"AEF trust file {label} must be an object")
    document = cast("dict[str, Any]", value)
    unknown = sorted(set(document) - allowed)
    if unknown:
        raise AefTrustFileError(f"AEF trust file {label} has unknown fields: {', '.join(unknown)}")
    return document


def _optional_ms(entry: Mapping[str, Any], field: str, key_id: str) -> int | None:
    value = entry.get(field)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise AefTrustFileError(f"AEF trust key {key_id} {field} must be integer milliseconds")
    return value


def _trusted_key(key_id: str, value: object) -> AefTrustedKey:
    entry = _object(value, f"key {key_id}", _KEY_FIELDS)
    raw_key = entry.get("public_key_hex")
    if not isinstance(raw_key, str):
        raise AefTrustFileError(f"AEF trust key {key_id} needs public_key_hex")
    try:
        public_key = bytes.fromhex(raw_key)
    except ValueError:
        raise AefTrustFileError(f"AEF trust key {key_id} public_key_hex is not hex") from None
    revoked = entry.get("revoked", False)
    if not isinstance(revoked, bool):
        raise AefTrustFileError(f"AEF trust key {key_id} revoked must be true or false")
    senders_value = entry.get("senders")
    senders: frozenset[str] | None = None
    if senders_value is not None:
        if not isinstance(senders_value, list) or not all(
            isinstance(sender, str) for sender in senders_value
        ):
            raise AefTrustFileError(f"AEF trust key {key_id} senders must be a list of strings")
        senders = frozenset(senders_value)
    try:
        return AefTrustedKey(
            public_key=public_key,
            revoked=revoked,
            not_before=_optional_ms(entry, "not_before", key_id),
            not_after=_optional_ms(entry, "not_after", key_id),
            senders=senders,
        )
    except ValueError as exc:
        raise AefTrustFileError(f"AEF trust key {key_id}: {exc}") from None


def parse_aef_trust(raw: bytes) -> AefTrustStore:
    """Parse one AEF trust file.

    Parameters
    ----------
    raw : bytes
        The UTF-8 JSON document.

    Returns
    -------
    AefTrustStore
        The trust store the verifier uses.

    Raises
    ------
    AefTrustFileError
        When the document is malformed, has unknown or repeated fields, or a
        key id or log binding does not match the key material.
    """
    try:
        document = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except AefTrustFileError:
        raise
    except (UnicodeDecodeError, ValueError):
        raise AefTrustFileError("AEF trust file is not UTF-8 JSON") from None
    top = _object(document, "document", _TOP_FIELDS)
    if top.get("format") != AEF_TRUST_FORMAT:
        raise AefTrustFileError(f"AEF trust file format must be {AEF_TRUST_FORMAT!r}")
    missing = sorted(_TOP_FIELDS - set(top))
    if missing:
        raise AefTrustFileError(f"AEF trust file is missing fields: {', '.join(missing)}")
    keys, logs = top["keys"], top["logs"]
    if not isinstance(keys, dict) or not isinstance(logs, dict):
        raise AefTrustFileError("AEF trust file keys and logs must be objects")
    trusted = {key_id: _trusted_key(key_id, value) for key_id, value in keys.items()}
    if any(not isinstance(key_id, str) for key_id in logs.values()):
        raise AefTrustFileError("AEF trust file logs must map a log id to a key id")
    try:
        return AefTrustStore(keys=trusted, logs=cast("dict[str, str]", logs))
    except ValueError as exc:
        raise AefTrustFileError(f"AEF trust file is inconsistent: {exc}") from None


def load_aef_trust(path: str | Path) -> AefTrustStore:
    """Read and parse an AEF trust file.

    Raises
    ------
    AefTrustFileError
        When the file cannot be read, exceeds 1 MiB, or is invalid.
    """
    target = Path(path)
    try:
        with target.open("rb") as handle:
            raw = handle.read(_MAX_TRUST_BYTES + 1)
    except OSError as exc:
        raise AefTrustFileError(f"cannot read AEF trust file {target}: {exc.strerror}") from None
    if len(raw) > _MAX_TRUST_BYTES:
        raise AefTrustFileError(f"AEF trust file {target} exceeds {_MAX_TRUST_BYTES} bytes")
    return parse_aef_trust(raw)


def aef_trust_document(*, hub_id: str, public_key: bytes) -> dict[str, object]:
    """Return the trust document for one hub's AEF log and its signing key.

    Parameters
    ----------
    hub_id : str
        The hub id the log was created with (``synapse hub --hub-id``).
    public_key : bytes
        The raw 32-byte Ed25519 key from the hub's ``--aef-signing-key`` pair.

    Returns
    -------
    dict[str, object]
        A document that :func:`parse_aef_trust` accepts, naming the derived
        ``log_id`` and ``key_id``.
    """
    key_id = receipt_key_id(public_key)
    return {
        "format": AEF_TRUST_FORMAT,
        "keys": {key_id: {"public_key_hex": public_key.hex(), "revoked": False}},
        "logs": {derive_aef_log_id(hub_id, public_key): key_id},
    }
