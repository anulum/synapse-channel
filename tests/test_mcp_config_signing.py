# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — outbound MCP Ed25519 manifest signature tests

from __future__ import annotations

import base64
import builtins
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from synapse_channel.core.mcp_config import McpConfigError
from synapse_channel.core.mcp_config_signing import (
    canonical_mcp_config,
    sign_mcp_config_document,
    verify_mcp_config_signature,
)


def _document(command: Path) -> dict[str, Any]:
    return {
        "version": 1,
        "servers": [{"name": "echo", "command": str(command), "allowed_tools": ["echo"]}],
    }


def _trust_bundle(private_key: Ed25519PrivateKey, *, revoked: bool = False) -> dict[str, Any]:
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return {
        "version": 1,
        "keys": [
            {
                "key_id": "ops",
                "public_key": base64.b64encode(public_key).decode("ascii"),
                "revoked": revoked,
            }
        ],
    }


def test_signature_verifies_and_rejects_tampering_revocation_and_unknown_key(
    tmp_path: Path,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    clean = sign_mcp_config_document(
        _document(tmp_path / "server"), key_id="ops", private_key=private_key
    )
    assert verify_mcp_config_signature(clean, _trust_bundle(private_key)) == "ops"

    tampered = {**clean, "servers": [{**clean["servers"][0], "args": ["--tampered"]}]}
    with pytest.raises(McpConfigError, match="verification failed"):
        verify_mcp_config_signature(tampered, _trust_bundle(private_key))
    with pytest.raises(McpConfigError, match="is not trusted"):
        verify_mcp_config_signature(clean, _trust_bundle(private_key, revoked=True))
    unknown = {**clean, "signature": {**clean["signature"], "key_id": "unknown"}}
    with pytest.raises(McpConfigError, match="is not trusted"):
        verify_mcp_config_signature(unknown, _trust_bundle(private_key))


def test_signature_rejects_empty_signing_key_id(tmp_path: Path) -> None:
    for key_id in ("", " ops "):
        with pytest.raises(McpConfigError, match="without surrounding whitespace"):
            sign_mcp_config_document(
                _document(tmp_path / "server"),
                key_id=key_id,
                private_key=Ed25519PrivateKey.generate(),
            )


def test_canonical_config_preserves_non_mapping_signature_for_strict_validation() -> None:
    payload = canonical_mcp_config({"version": 1, "signature": "invalid"})
    assert b'"signature":"invalid"' in payload


def test_signature_binds_key_id_and_trust_rejects_public_key_aliases(tmp_path: Path) -> None:
    private_key = Ed25519PrivateKey.generate()
    signed = sign_mcp_config_document(
        _document(tmp_path / "server"), key_id="alpha", private_key=private_key
    )
    rewritten = {**signed, "signature": {**signed["signature"], "key_id": "beta"}}
    beta_bundle = _trust_bundle(private_key)
    beta_bundle["keys"][0]["key_id"] = "beta"
    with pytest.raises(McpConfigError, match="verification failed"):
        verify_mcp_config_signature(rewritten, beta_bundle)

    public_key = beta_bundle["keys"][0]["public_key"]
    aliased_bundle = {
        "version": 1,
        "keys": [
            {"key_id": "alpha", "public_key": public_key, "revoked": True},
            {"key_id": "beta", "public_key": public_key, "revoked": False},
        ],
    }
    with pytest.raises(McpConfigError, match="duplicate MCP config trust public key material"):
        verify_mcp_config_signature(rewritten, aliased_bundle)


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        (lambda document: document.pop("signature"), "requires a signed"),
        (lambda document: document["signature"].pop("value"), "contain exactly"),
        (lambda document: document["signature"].update({"extra": 1}), "contain exactly"),
        (lambda document: document["signature"].update({"version": 2}), "version must be 1"),
        (lambda document: document["signature"].update({"version": True}), "version must be 1"),
        (lambda document: document["signature"].update({"version": 1.0}), "version must be 1"),
        (lambda document: document["signature"].update({"algorithm": "rsa"}), "algorithm"),
        (lambda document: document["signature"].update({"key_id": ""}), "key_id"),
        (lambda document: document["signature"].update({"key_id": " ops "}), "key_id"),
        (lambda document: document["signature"].update({"value": 1}), "base64 text"),
        (lambda document: document["signature"].update({"value": "***"}), "not valid base64"),
    ],
)
def test_signature_envelope_is_strict(tmp_path: Path, mutation: Any, match: str) -> None:
    private_key = Ed25519PrivateKey.generate()
    document = sign_mcp_config_document(
        _document(tmp_path / "server"), key_id="ops", private_key=private_key
    )
    mutation(document)
    with pytest.raises(McpConfigError, match=match):
        verify_mcp_config_signature(document, _trust_bundle(private_key))


@pytest.mark.parametrize(
    ("bundle", "match"),
    [
        ({"version": 1, "keys": [], "extra": True}, "unknown fields"),
        ({"version": 2, "keys": []}, "version must be 1"),
        ({"version": True, "keys": []}, "version must be 1"),
        ({"version": 1.0, "keys": []}, "version must be 1"),
        ({"version": 1, "keys": []}, "non-empty 'keys' list"),
        ({"version": 1, "keys": [1]}, "invalid shape"),
        (
            {"version": 1, "keys": [{"key_id": "", "public_key": ""}]},
            "non-empty key_id",
        ),
        (
            {"version": 1, "keys": [{"key_id": " ops ", "public_key": ""}]},
            "without surrounding whitespace",
        ),
        (
            {
                "version": 1,
                "keys": [
                    {"key_id": "ops", "public_key": base64.b64encode(b"x" * 32).decode()},
                    {"key_id": "ops", "public_key": base64.b64encode(b"y" * 32).decode()},
                ],
            },
            "duplicate MCP config trust key",
        ),
        (
            {
                "version": 1,
                "keys": [
                    {
                        "key_id": "ops",
                        "public_key": base64.b64encode(b"x" * 32).decode(),
                        "revoked": True,
                    },
                    {"key_id": "ops", "public_key": base64.b64encode(b"y" * 32).decode()},
                ],
            },
            "duplicate MCP config trust key",
        ),
        (
            {
                "version": 1,
                "keys": [{"key_id": "ops", "public_key": "", "revoked": "yes"}],
            },
            "revoked must be boolean",
        ),
        (
            {"version": 1, "keys": [{"key_id": "ops", "public_key": 1}]},
            "public_key must be base64 text",
        ),
        (
            {"version": 1, "keys": [{"key_id": "ops", "public_key": "***"}]},
            "public_key is not valid base64",
        ),
        (
            {
                "version": 1,
                "keys": [
                    {
                        "key_id": "ops",
                        "public_key": base64.b64encode(b"short").decode(),
                    }
                ],
            },
            "32 raw Ed25519 bytes",
        ),
    ],
)
def test_trust_bundle_schema_is_strict(tmp_path: Path, bundle: dict[str, Any], match: str) -> None:
    private_key = Ed25519PrivateKey.generate()
    document = sign_mcp_config_document(
        _document(tmp_path / "server"), key_id="ops", private_key=private_key
    )
    with pytest.raises(McpConfigError, match=match):
        verify_mcp_config_signature(document, bundle)


def test_signature_reports_missing_crypto_dependency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    private_key = Ed25519PrivateKey.generate()
    document = sign_mcp_config_document(
        _document(tmp_path / "server"), key_id="ops", private_key=private_key
    )
    real_import = builtins.__import__

    def block_crypto(name: str, *args: Any, **kwargs: Any) -> Any:
        if name.startswith("cryptography"):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", block_crypto)
    with pytest.raises(McpConfigError, match=r"synapse-channel\[mcp\]"):
        verify_mcp_config_signature(document, _trust_bundle(private_key))
