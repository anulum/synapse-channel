# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real HTTPS MCP test server
"""Run finite SDK HTTP servers with a verified ephemeral TLS certificate."""

from __future__ import annotations

import asyncio
import datetime
import ipaddress
import json
import socket
import ssl
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import jwt
import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.x509.oid import NameOID
from starlette.applications import Starlette

from synapse_channel.core.identity_keys import write_signing_key
from synapse_channel.core.message_auth import (
    EventSignatureKey,
    EventSignatureTrustBundle,
    MessageReplayCache,
)
from synapse_channel.mcp.http_config import MUTATION_TOOLS, READ_TOOLS


def write_policy(path: Path, key: Ed25519PrivateKey, *, revoked: bool = False) -> None:
    """Write genuine issuer verification material and current operator grants."""
    public_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    policy = {
        "issuer": "https://issuer.example.test",
        "resource": "https://mcp.example.test/mcp",
        "public_keys": {"issuer-1": public_pem.decode()},
        "subjects": {
            "alice": {
                "projects": {
                    "ALPHA": {
                        "seat": "ALPHA/alice",
                        "identity_key_file": str(path.parent / "alice.pem"),
                        "identity_key_id": "alice-1",
                        "task_prefix": "ALPHA/",
                    }
                }
            }
        },
        "revoked_token_ids": ["token-1"] if revoked else [],
    }
    path.write_text(json.dumps(policy), encoding="utf-8")
    path.chmod(0o600)


def access_token(key: Ed25519PrivateKey, **changes: object) -> str:
    """Issue a real signed short-lived token for the configured MCP audience."""
    now = int(time.time())
    claims: dict[str, object] = {
        "iss": "https://issuer.example.test",
        "aud": "https://mcp.example.test/mcp",
        "sub": "alice",
        "client_id": "inspector",
        "scope": "synapse:read",
        "jti": "token-1",
        "iat": now,
        "exp": now + 120,
    }
    claims.update(changes)
    return jwt.encode(claims, key, algorithm="EdDSA", headers={"kid": "issuer-1"})


def tls_files(directory: Path) -> tuple[Path, Path, ssl.SSLContext]:
    """Issue a real loopback certificate and owner-only key for finite test servers."""
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.datetime.now(datetime.timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(hours=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    certificate_file, key_file = directory / "https.pem", directory / "https-key.pem"
    certificate_file.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    key_file.chmod(0o600)
    context = ssl.create_default_context(cafile=str(certificate_file))
    return certificate_file, key_file, context


@asynccontextmanager
async def https_server(
    app: Starlette, directory: Path, *, plaintext: bool = False
) -> AsyncIterator[tuple[str, ssl.SSLContext]]:
    """Serve verified loopback HTTPS, or plaintext to test its production refusal."""
    certificate_file, key_file, context = tls_files(directory)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(16)
        listener.setblocking(False)
        port = listener.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=port,
                ssl_certfile=None if plaintext else str(certificate_file),
                ssl_keyfile=None if plaintext else str(key_file),
                log_config=None,
                access_log=False,
                timeout_graceful_shutdown=2,
            )
        )
        serving = asyncio.create_task(server.serve(sockets=[listener]))
        try:

            async def wait_started() -> None:
                """Observe actual server readiness and propagate startup failure."""
                while not server.started:
                    if serving.done():
                        await serving
                        raise RuntimeError("HTTPS server stopped before startup")
                    await asyncio.sleep(0.01)

            await asyncio.wait_for(wait_started(), 5)
            scheme = "http" if plaintext else "https"
            yield f"{scheme}://127.0.0.1:{port}", context
        finally:
            server.should_exit = True
            try:
                await asyncio.wait_for(serving, 5)
            finally:
                if not serving.done():
                    serving.cancel()


def provision(path: Path, issuer: Ed25519PrivateKey) -> EventSignatureTrustBundle:
    """Provision independent Alice/Bob hub keys and a foreign-project issuer subject."""
    write_policy(path, issuer)
    policy: dict[str, Any] = json.loads(path.read_text())
    keys: dict[str, EventSignatureKey] = {}
    for subject in ("alice", "bob"):
        key = Ed25519PrivateKey.generate()
        key_path = path.parent / (subject + ".pem")
        write_signing_key(key_path, key)
        key_id = subject + "-1"
        seat = "ALPHA/" + subject
        keys[key_id] = EventSignatureKey.from_private_key(
            key_id=key_id,
            private_key=key,
            senders=frozenset({seat}),
        )
        policy["subjects"][subject] = {
            "projects": {
                "ALPHA": {
                    "seat": seat,
                    "identity_key_file": str(key_path),
                    "identity_key_id": key_id,
                    "task_prefix": "ALPHA/",
                    "tools": sorted(READ_TOOLS | MUTATION_TOOLS),
                }
            }
        }
    policy["subjects"]["foreign"] = {
        "projects": {
            "BETA": {
                "seat": "BETA/foreign",
                "identity_key_file": str(path.parent / "foreign.pem"),
                "identity_key_id": "foreign-1",
                "task_prefix": "BETA/",
            }
        }
    }
    path.write_text(json.dumps(policy))
    return EventSignatureTrustBundle(
        keys=keys,
        replay_cache=MessageReplayCache(window_seconds=30, max_entries=128),
    )
