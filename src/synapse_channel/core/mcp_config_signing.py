# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Ed25519 signatures for outbound MCP execution manifests
"""Domain-separated Ed25519 signing for outbound MCP execution policy.

A static manifest signature authenticates the complete versioned config apart
from its signature envelope. Trust roots remain in a separate owner-controlled
bundle, so a repository that can replace the config cannot replace the key that
authorises it at the same time.
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from synapse_channel.core.mcp_config import MCP_CONFIG_VERSION, McpConfigError

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

MCP_CONFIG_SIGNATURE_ALGORITHM = "ed25519"
"""Only signature algorithm accepted for outbound MCP manifests."""

MCP_CONFIG_SIGNATURE_DOMAIN = b"SYNAPSE-CHANNEL:MCP-CONFIG:v1\x00"
"""Domain separator prepended to canonical manifest bytes."""

MCP_SIGNATURE_EXTRA_HINT = (
    "signed MCP configs need Ed25519 support: pip install 'synapse-channel[mcp]'"
)
"""Installation hint when signature verification support is unavailable."""

_SIGNATURE_FIELDS = frozenset({"algorithm", "key_id", "value", "version"})
_TRUST_BUNDLE_FIELDS = frozenset({"keys", "version"})
_TRUST_KEY_FIELDS = frozenset({"key_id", "public_key", "revoked"})
_ED25519_PUBLIC_BYTES = 32


def canonical_mcp_config(document: Mapping[str, Any]) -> bytes:
    """Return bytes binding policy and signature metadata, excluding only its value."""
    unsigned = dict(document)
    signature = unsigned.get("signature")
    if isinstance(signature, dict):
        unsigned["signature"] = {key: value for key, value in signature.items() if key != "value"}
    payload = json.dumps(
        unsigned,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return MCP_CONFIG_SIGNATURE_DOMAIN + payload


def sign_mcp_config_document(
    document: Mapping[str, Any],
    *,
    key_id: str,
    private_key: Ed25519PrivateKey,
) -> dict[str, Any]:
    """Return a copy of ``document`` with a domain-separated Ed25519 signature.

    Parameters
    ----------
    document : Mapping[str, Any]
        Unsigned version-1 MCP config document.
    key_id : str
        Operator trust-bundle key identifier.
    private_key : Ed25519PrivateKey
        Ed25519 key used only for this static manifest.

    Returns
    -------
    dict[str, Any]
        JSON-compatible document with a ``signature`` envelope.
    """
    if not key_id or key_id != key_id.strip():
        raise McpConfigError(
            "MCP config signature key_id must be non-empty without surrounding whitespace"
        )
    public_key_id = key_id
    signed = dict(document)
    signed.pop("signature", None)
    signed["signature"] = {
        "algorithm": MCP_CONFIG_SIGNATURE_ALGORITHM,
        "key_id": public_key_id,
        "version": MCP_CONFIG_VERSION,
    }
    signature = private_key.sign(canonical_mcp_config(signed))
    signed["signature"]["value"] = base64.b64encode(signature).decode("ascii")
    return signed


def verify_mcp_config_signature(
    document: Mapping[str, Any], trust_document: Mapping[str, Any]
) -> str:
    """Verify ``document`` against an owner-loaded Ed25519 trust bundle.

    Returns
    -------
    str
        Verified signing key id.

    Raises
    ------
    McpConfigError
        If the envelope, trust bundle, key, or signature is invalid.
    """
    signature = document.get("signature")
    if not isinstance(signature, dict):
        raise McpConfigError("MCP config trust bundle requires a signed config document")
    unknown = set(signature) - _SIGNATURE_FIELDS
    if unknown or set(signature) != _SIGNATURE_FIELDS:
        raise McpConfigError(
            "MCP config signature must contain exactly version, algorithm, key_id, value"
        )
    signature_version = signature["version"]
    if (
        isinstance(signature_version, bool)
        or not isinstance(signature_version, int)
        or signature_version != MCP_CONFIG_VERSION
    ):
        raise McpConfigError(f"MCP config signature version must be {MCP_CONFIG_VERSION}")
    if signature["algorithm"] != MCP_CONFIG_SIGNATURE_ALGORITHM:
        raise McpConfigError(
            f"MCP config signature algorithm must be {MCP_CONFIG_SIGNATURE_ALGORITHM}"
        )
    key_id = signature["key_id"]
    value = signature["value"]
    if not isinstance(key_id, str) or not key_id or key_id != key_id.strip():
        raise McpConfigError(
            "MCP config signature key_id must be non-empty without surrounding whitespace"
        )
    if not isinstance(value, str):
        raise McpConfigError("MCP config signature value must be base64 text")
    keys = _parse_trust_bundle(trust_document)
    public_key = keys.get(key_id)
    if public_key is None:
        raise McpConfigError(f"MCP config signature key {key_id!r} is not trusted")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise McpConfigError("MCP config signature value is not valid base64") from exc
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError as exc:
        raise McpConfigError(MCP_SIGNATURE_EXTRA_HINT) from exc
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(
            decoded, canonical_mcp_config(document)
        )
    except InvalidSignature as exc:
        raise McpConfigError("MCP config signature verification failed") from exc
    return key_id


def _parse_trust_bundle(document: Mapping[str, Any]) -> dict[str, bytes]:
    """Parse a minimal versioned Ed25519 config trust bundle."""
    if set(document) - _TRUST_BUNDLE_FIELDS:
        raise McpConfigError("MCP config trust bundle has unknown fields")
    bundle_version = document.get("version")
    if (
        isinstance(bundle_version, bool)
        or not isinstance(bundle_version, int)
        or bundle_version != MCP_CONFIG_VERSION
    ):
        raise McpConfigError(f"MCP config trust bundle version must be {MCP_CONFIG_VERSION}")
    entries = document.get("keys")
    if not isinstance(entries, list) or not entries:
        raise McpConfigError("MCP config trust bundle must contain a non-empty 'keys' list")
    keys: dict[str, bytes] = {}
    seen_key_ids: set[str] = set()
    seen_public_keys: set[bytes] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or set(entry) - _TRUST_KEY_FIELDS:
            raise McpConfigError(f"MCP config trust key {index} has an invalid shape")
        key_id = entry.get("key_id")
        public_value = entry.get("public_key")
        revoked = entry.get("revoked", False)
        if not isinstance(key_id, str) or not key_id or key_id != key_id.strip():
            raise McpConfigError(
                f"MCP config trust key {index} needs non-empty key_id "
                "without surrounding whitespace"
            )
        if key_id in seen_key_ids:
            raise McpConfigError(f"duplicate MCP config trust key id: {key_id}")
        seen_key_ids.add(key_id)
        if not isinstance(revoked, bool):
            raise McpConfigError(f"MCP config trust key {key_id!r} revoked must be boolean")
        if not isinstance(public_value, str):
            raise McpConfigError(f"MCP config trust key {key_id!r} public_key must be base64 text")
        try:
            public_key = base64.b64decode(public_value, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise McpConfigError(
                f"MCP config trust key {key_id!r} public_key is not valid base64"
            ) from exc
        if len(public_key) != _ED25519_PUBLIC_BYTES:
            raise McpConfigError(
                f"MCP config trust key {key_id!r} must be {_ED25519_PUBLIC_BYTES} raw Ed25519 bytes"
            )
        if public_key in seen_public_keys:
            raise McpConfigError("duplicate MCP config trust public key material")
        seen_public_keys.add(public_key)
        if revoked:
            continue
        keys[key_id] = public_key
    return keys
