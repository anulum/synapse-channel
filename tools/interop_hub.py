# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real hub, MCP and A2A processes for the official-client interop lane
"""Provision and run the real Synapse processes an interop journey talks to.

Everything here is the shipped command line: ``synapse hub``, ``synapse mcp``
(stdio and Streamable HTTP) and ``synapse a2a-serve``, each started as its own
process from the hub's interpreter. The official clients run elsewhere and
reach these processes only through their protocols.

The HTTP profile is provisioned the way an operator would: an issuer key, two
subjects with their own native identity keys enrolled in the hub's identity
trust file, a grant document naming their seats, a loopback TLS certificate,
and short-lived signed access tokens.
"""

from __future__ import annotations

import base64
import contextlib
import datetime
import ipaddress
import json
import os
import secrets
import socket
import subprocess  # nosec B404
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import jwt
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.x509.oid import NameOID

from synapse_channel.core.identity_keys import write_signing_key
from synapse_channel.mcp.http_config import MUTATION_TOOLS, READ_TOOLS

PROJECT = "INTEROP"
"""The project every interop identity belongs to."""

HTTP_SUBJECTS = ("alice", "bob")
"""The two authenticated HTTP subjects, in claim order."""

STDIO_NAMES = ("interop/alpha", "interop/beta")
"""The two stdio identities: names asserted under one hub token."""

ISSUER = "https://issuer.interop.test"
RESOURCE = "https://mcp.interop.test/mcp"
_ISSUER_KID = "interop-issuer-1"
_READY_SECONDS = 20.0
_STOP_SECONDS = 10.0


class InteropProcessError(RuntimeError):
    """A Synapse process of the lane did not start or stopped early."""


def free_port() -> int:
    """Return a loopback port that was free a moment ago."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


def _await_port(port: int, process: subprocess.Popen[bytes], label: str) -> None:
    deadline = time.monotonic() + _READY_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise InteropProcessError(f"{label} exited with code {process.returncode}")
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            return
        except OSError:
            time.sleep(0.05)
    raise InteropProcessError(f"{label} did not listen on port {port}")


def clean_environment() -> dict[str, str]:
    """Return this process's environment without any Synapse identity or token.

    An operator's ambient ``SYN_*`` or ``SYNAPSE_*`` setting would otherwise
    choose the identity or the token of a lane process.
    """
    return {
        key: value for key, value in os.environ.items() if not key.startswith(("SYN_", "SYNAPSE_"))
    }


def synapse_command(*arguments: str) -> list[str]:
    """Return the command that runs the shipped CLI with this interpreter."""
    return [sys.executable, "-m", "synapse_channel.cli", *arguments]


@contextlib.contextmanager
def running(
    command: list[str], *, port: int, label: str, log: Path
) -> Iterator[subprocess.Popen[bytes]]:
    """Run ``command`` until it listens on ``port``; always stop and reap it.

    The process's output goes to ``log``, a new file kept for diagnosis; it is
    never copied into the evidence record.
    """
    with log.open("xb") as output:
        # A fixed command: this interpreter running the shipped Synapse CLI.
        process = subprocess.Popen(  # noqa: S603  # nosec B603
            command,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            env=clean_environment(),
        )
    try:
        _await_port(port, process, label)
        yield process
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=_STOP_SECONDS)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=_STOP_SECONDS)


def _owner_only_text(path: Path, text: str) -> Path:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(text)
    return path


@dataclass(frozen=True)
class Provisioned:
    """Everything one run's processes and clients need."""

    directory: Path
    hub_token: str
    hub_token_file: Path
    identity_trust: Path
    grants: Path
    certificate: Path
    certificate_key: Path
    issuer_key: Ed25519PrivateKey
    issued: list[str] = field(default_factory=list)

    def access_token(self, subject: str, *, audience: str = RESOURCE, lifetime: int = 300) -> str:
        """Issue a real short-lived EdDSA access token for ``subject`` and ``audience``."""
        now = int(time.time())
        claims = {
            "iss": ISSUER,
            "aud": audience,
            "sub": subject,
            "client_id": "synapse-interop-lane",
            "scope": "synapse:read synapse:mutate",
            "jti": f"{subject}-{secrets.token_hex(8)}",
            "iat": now,
            "exp": now + lifetime,
        }
        token = jwt.encode(claims, self.issuer_key, algorithm="EdDSA", headers={"kid": _ISSUER_KID})
        self.issued.append(token)
        return token

    def secrets(self) -> list[str]:
        """Return the secret strings a record must never contain.

        They are the hub token and every access token issued so far.
        """
        return [self.hub_token, *self.issued]


def _certificate(directory: Path) -> tuple[Path, Path]:
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
        .not_valid_after(now + datetime.timedelta(hours=2))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    certificate_file = directory / "https.pem"
    certificate_file.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_text = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode("ascii")
    return certificate_file, _owner_only_text(directory / "https-key.pem", key_text)


def provision(directory: Path) -> Provisioned:
    """Write the hub token, identity trust, grants and TLS files into ``directory``."""
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    hub_token = secrets.token_urlsafe(32)
    issuer_key = Ed25519PrivateKey.generate()
    trust_keys: list[dict[str, object]] = []
    subjects: dict[str, object] = {}
    for subject in HTTP_SUBJECTS:
        identity = Ed25519PrivateKey.generate()
        key_file = directory / f"{subject}.pem"
        write_signing_key(key_file, identity)
        seat = f"{PROJECT}/{subject}"
        trust_keys.append(
            {
                "key_id": f"{subject}-1",
                "public_key": base64.b64encode(identity.public_key().public_bytes_raw()).decode(),
                "senders": [seat],
            }
        )
        subjects[subject] = {
            "projects": {
                PROJECT: {
                    "seat": seat,
                    "identity_key_file": str(key_file),
                    "identity_key_id": f"{subject}-1",
                    "task_prefix": f"{PROJECT}/",
                    "tools": sorted(READ_TOOLS | MUTATION_TOOLS),
                }
            }
        }
    issuer_pem = issuer_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    grants = {
        "issuer": ISSUER,
        "resource": RESOURCE,
        "public_keys": {_ISSUER_KID: issuer_pem.decode("ascii")},
        "subjects": subjects,
    }
    certificate, certificate_key = _certificate(directory)
    return Provisioned(
        directory=directory,
        hub_token=hub_token,
        hub_token_file=_owner_only_text(directory / "hub.token", hub_token + "\n"),
        identity_trust=_owner_only_text(
            directory / "identity-trust.json", json.dumps({"keys": trust_keys})
        ),
        grants=_owner_only_text(directory / "grants.json", json.dumps(grants)),
        certificate=certificate,
        certificate_key=certificate_key,
        issuer_key=issuer_key,
    )


def hub_command(
    provisioned: Provisioned, db: Path, port: int, *, identity_binding: bool
) -> list[str]:
    """Return the hub command: a journal and the hub token.

    With ``identity_binding`` the hub also requires every seat to prove its
    enrolled identity key, which the HTTP subjects do. Without it the hub admits
    any name that holds the token, which is how the stdio identities connect.
    """
    command = synapse_command(
        "hub",
        "--port",
        str(port),
        "--db",
        str(db),
        "--token-file",
        str(provisioned.hub_token_file),
        "--identity-pins",
        str(provisioned.directory / "pins.json"),
    )
    if identity_binding:
        command += [
            "--identity-trust",
            str(provisioned.identity_trust),
            "--require-identity-binding",
        ]
    return command


def stdio_mcp_command(provisioned: Provisioned, hub_uri: str, name: str) -> list[str]:
    """Return the stdio MCP server command for one asserted name."""
    return synapse_command(
        "mcp",
        "--uri",
        hub_uri,
        "--name",
        name,
        "--token-file",
        str(provisioned.hub_token_file),
    )


def http_mcp_command(provisioned: Provisioned, hub_uri: str, port: int) -> list[str]:
    """Return the Streamable HTTP MCP server command of the private profile."""
    return synapse_command(
        "mcp",
        "--transport",
        "streamable-http",
        "--project",
        PROJECT,
        "--uri",
        hub_uri,
        "--token-file",
        str(provisioned.hub_token_file),
        "--http-auth-file",
        str(provisioned.grants),
        "--tls-cert-file",
        str(provisioned.certificate),
        "--tls-key-file",
        str(provisioned.certificate_key),
        "--http-host",
        "127.0.0.1",
        "--http-port",
        str(port),
        "--http-allowed-host",
        f"127.0.0.1:{port}",
    )
