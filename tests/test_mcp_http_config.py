# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — remote MCP operator policy loading
"""Exercise actual owner-only policy files and explicit principal authority."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mcp_http_helpers import write_policy
from synapse_channel.mcp.http_config import READ_TOOLS, HttpConfigError, load_http_auth_config


def test_owner_policy_defaults_to_reads(tmp_path: Path) -> None:
    """A real owner-only policy grants one exact seat and no mutation tools."""
    path = tmp_path / "policy.json"
    write_policy(path, Ed25519PrivateKey.generate())
    config = load_http_auth_config(path)
    grant = config.subjects["alice"].projects["ALPHA"]
    assert grant.seat == "ALPHA/alice"
    assert grant.task_prefix == "ALPHA/"
    assert grant.tools == READ_TOOLS


@pytest.mark.parametrize(
    "field,value",
    [
        ("issuer", "http://issuer.example.test"),
        ("resource", "https://user:private-content@example.test/mcp"),
        ("resource", "https://example.test/mcp?private-content=1"),
        ("resource", "https://example.test/mcp#private-content"),
        ("resource", "https://example.test:bad/mcp"),
        ("public_keys", {}),
        ("public_keys", {"issuer-1": "private-content"}),
        ("subjects", {}),
        ("subjects", {"": {"projects": {}}}),
        ("max_token_age_seconds", True),
        ("max_token_age_seconds", 0),
        ("private-content", "unknown-policy-field"),
    ],
)
def test_invalid_policy_has_content_free_error(tmp_path: Path, field: str, value: object) -> None:
    """Untrusted policy values cannot escape validation into diagnostics."""
    path = tmp_path / "policy.json"
    write_policy(path, Ed25519PrivateKey.generate())
    payload: dict[str, object] = json.loads(path.read_text())
    payload[field] = value
    path.write_text(json.dumps(payload))
    with pytest.raises(HttpConfigError) as failure:
        load_http_auth_config(path)
    assert str(failure.value) == "remote MCP authentication configuration is invalid"
    assert "private-content" not in str(failure.value)


@pytest.mark.parametrize(
    "replacement",
    [
        '"seat": "BETA/alice"',
        '"seat": "ALPHA/"',
        '"task_prefix": "ALPHA"',
        '"task_prefix": "BETA/"',
        '"task_prefix": "/"',
        '"tools": ["synapse_memory_recall"]',
    ],
)
def test_seat_and_tool_scope_validation(tmp_path: Path, replacement: str) -> None:
    """Cross-project seats, task namespaces and local filesystem tools are refused."""
    path = tmp_path / "policy.json"
    write_policy(path, Ed25519PrivateKey.generate())
    text = path.read_text()
    if replacement.startswith('"seat"'):
        text = text.replace('"seat": "ALPHA/alice"', replacement)
    elif replacement.startswith('"task_prefix"'):
        text = text.replace('"task_prefix": "ALPHA/"', replacement)
    else:
        text = text.replace('"task_prefix": "ALPHA/"', '"task_prefix": "ALPHA/", ' + replacement)
    path.write_text(text)
    with pytest.raises(HttpConfigError):
        load_http_auth_config(path)


def test_ambiguous_and_unsafe_policy_files(tmp_path: Path) -> None:
    """Duplicate JSON, symlinks, hardlinks and real group-readable files fail closed."""
    path = tmp_path / "policy.json"
    write_policy(path, Ed25519PrivateKey.generate())
    original = path.read_text()
    path.write_text(original.replace('"issuer":', '"issuer": "private-content", "issuer":', 1))
    with pytest.raises(HttpConfigError):
        load_http_auth_config(path)

    path.write_text(original)
    path.chmod(0o640)
    with pytest.raises(HttpConfigError):
        load_http_auth_config(path)
    path.chmod(0o600)
    alias = tmp_path / "alias.json"
    alias.symlink_to(path)
    with pytest.raises(HttpConfigError):
        load_http_auth_config(alias)
    os.link(path, tmp_path / "hardlink.json")
    with pytest.raises(HttpConfigError):
        load_http_auth_config(path)


@pytest.mark.parametrize("case", ["project", "key-id", "key-type", "subject", "seat-collision"])
def test_invalid_operator_identity_bindings(tmp_path: Path, case: str) -> None:
    """Refuse malformed project identities, issuer keys and shared principal seats."""
    path = tmp_path / "policy.json"
    write_policy(path, Ed25519PrivateKey.generate())
    payload = load_http_auth_config(path).model_dump(mode="json")
    if case == "project":
        payload["subjects"]["alice"]["projects"]["ALPHA/BETA"] = payload["subjects"]["alice"][
            "projects"
        ].pop("ALPHA")
    elif case == "key-id":
        payload["public_keys"][""] = payload["public_keys"].pop("issuer-1")
    elif case == "key-type":
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        payload["public_keys"]["issuer-1"] = (
            key.public_key()
            .public_bytes(
                serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
            )
            .decode()
        )
    elif case == "subject":
        payload["subjects"][""] = payload["subjects"].pop("alice")
    else:
        payload["subjects"]["mallory"] = payload["subjects"]["alice"]
    path.write_text(json.dumps(payload))
    with pytest.raises(HttpConfigError):
        load_http_auth_config(path)
