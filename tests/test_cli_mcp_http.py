# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — private HTTPS MCP installed-command tests
"""Exercise the installed MCP command through actual TLS and native hub sockets."""

from __future__ import annotations

import asyncio
import os
import signal
import socket
import sys
from pathlib import Path

import httpx2
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from hub_e2e_helpers import running_hub
from mcp_http_helpers import access_token, operation_meta, provision, tls_files
from synapse_channel.core.auth import TokenAuthenticator
from synapse_channel.core.hub import SynapseHub


def command() -> list[str]:
    """Use the installed console script with its actual Python interpreter."""
    script = Path(sys.prefix) / ("Scripts/synapse.exe" if os.name == "nt" else "bin/synapse")
    return [str(script), "mcp", "--transport", "streamable-http"]


def profile(directory: Path, uri: str, port: int) -> list[str]:
    """Describe a real owner-provisioned private listener without any secret in argv."""
    return [
        "--project",
        "ALPHA",
        "--uri",
        uri,
        "--http-auth-file",
        str(directory / "grants.json"),
        "--tls-cert-file",
        str(directory / "https.pem"),
        "--tls-key-file",
        str(directory / "https-key.pem"),
        "--http-port",
        str(port),
        "--http-allowed-host",
        "127.0.0.1:*",
    ]


async def stopped(process: asyncio.subprocess.Process) -> tuple[bytes, bytes]:
    """Drain a finite child, terminating and reaping it if graceful shutdown fails."""
    try:
        return await asyncio.wait_for(process.communicate(), 8)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["--http-host", "0.0.0.0"],
        ["--http-host", "localhost"],
        ["--http-port", "0"],
        ["--http-port", "65536"],
        ["--http-allowed-host", "*"],
        ["--http-allowed-origin", "https://*"],
        ["--name", "ALPHA/unprovisioned"],
        ["--role", "ALPHA/operator"],
        ["--inbox-feed", "/private/feed"],
        ["--inbox-cursor", "/private/cursor"],
        ["--token", "argv-secret-must-not-be-logged"],
    ],
)
async def test_unsafe_or_missing_http_profile_is_refused(
    tmp_path: Path, arguments: list[str]
) -> None:
    """Malformed exposure authority is refused before credentials or network are used."""
    complete = profile(tmp_path, "ws://127.0.0.1:1", 8888) if arguments else []
    process = await asyncio.create_subprocess_exec(
        *command(),
        *complete,
        *arguments,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await stopped(process)
    assert process.returncode == 2
    assert stdout == b""
    assert b"invalid private HTTPS profile" in stderr
    assert b"argv-secret-must-not-be-logged" not in stderr


async def test_http_provisioning_cannot_silently_select_stdio(tmp_path: Path) -> None:
    """Selecting stdio with project grants refuses before ambient identity or hub effects."""
    process = await asyncio.create_subprocess_exec(
        *command(),
        *profile(tmp_path, "ws://127.0.0.1:1", 8888),
        "--transport",
        "stdio",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await stopped(process)
    assert process.returncode == 2
    assert stdout == b""
    assert stderr.strip() == (
        b"synapse mcp: HTTPS provisioning requires --transport streamable-http"
    )


@pytest.mark.parametrize(
    "failure",
    ["missing-key", "public-key-file", "invalid-certificate", "invalid-grants", "unreachable-hub"],
)
async def test_tls_and_grant_failures_have_content_free_diagnostics(
    tmp_path: Path, failure: str
) -> None:
    """Real invalid files fail closed without printing key or policy material."""
    issuer = Ed25519PrivateKey.generate()
    provision(tmp_path / "grants.json", issuer)
    certificate, key, _ = tls_files(tmp_path)
    if failure == "missing-key":
        key.unlink()
    elif failure == "public-key-file":
        key.chmod(0o644)
    elif failure == "invalid-certificate":
        certificate.write_text("certificate-private-marker")
    elif failure == "invalid-grants":
        (tmp_path / "grants.json").write_text("policy-private-marker")
    process = await asyncio.create_subprocess_exec(
        *command(),
        *profile(tmp_path, "ws://127.0.0.1:1", 8888),
        "--ready-timeout",
        "0.05",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await stopped(process)
    assert process.returncode == 1
    assert b"HTTPS startup or operation failed" in stderr
    assert b"private-marker" not in stdout + stderr
    assert b"PRIVATE KEY" not in stdout + stderr


@pytest.mark.parametrize(
    "shutdown", ["interrupt", "terminate"] + (["inherited-ignore"] if os.name != "nt" else [])
)
async def test_installed_http_command_mutates_only_the_provisioned_project(
    tmp_path: Path, shutdown: str
) -> None:
    """Actual CLI HTTPS preserves authenticated mutations, refusal and secret-free output."""
    issuer = Ed25519PrivateKey.generate()
    trust = provision(tmp_path / "grants.json", issuer)
    _, key, tls = tls_files(tmp_path)
    key_material = key.read_bytes()
    bearer = access_token(issuer, scope="synapse:read synapse:mutate")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    hub = SynapseHub(identity_trust_bundle=trust, require_identity_binding=True)
    async with running_hub(hub) as (_, uri):
        previous = signal.getsignal(signal.SIGTERM)
        try:
            if shutdown == "inherited-ignore":
                signal.signal(signal.SIGTERM, signal.SIG_IGN)
            process = await asyncio.create_subprocess_exec(
                *command(),
                *profile(tmp_path, uri, port),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        finally:
            signal.signal(signal.SIGTERM, previous)
        try:
            async with httpx2.AsyncClient(verify=tls, trust_env=False, timeout=5) as client:
                url = f"https://127.0.0.1:{port}/mcp"

                async def ready() -> None:
                    while process.returncode is None:
                        try:
                            response = await client.get(url)
                            assert response.status_code == 401
                            return
                        except httpx2.ConnectError:
                            await asyncio.sleep(0.02)
                    raise RuntimeError("CLI stopped before HTTPS readiness")

                await asyncio.wait_for(ready(), 8)
                client.headers["Authorization"] = "Bearer " + bearer
                async with streamable_http_client(url, http_client=client) as (read, write):
                    async with ClientSession(read, write) as session:
                        assert (await session.initialize()).protocol_version == "2025-11-25"
                        result = await session.call_tool(
                            "synapse_task_declare",
                            {"task_id": "ALPHA/cli", "title": "CLI task"},
                            meta=operation_meta("cli-declaration"),
                        )
                        assert not result.is_error
                        assert hub.blackboard.tasks["ALPHA/cli"].created_by == "ALPHA/alice"
                        result = await session.call_tool(
                            "synapse_task_declare",
                            {"task_id": "BETA/cli", "title": "denied"},
                            meta=operation_meta("cli-denied"),
                        )
                        assert result.is_error
                        assert "BETA/cli" not in hub.blackboard.tasks
        finally:
            if process.returncode is None:
                if os.name == "nt" or shutdown != "interrupt":
                    process.terminate()
                else:
                    process.send_signal(signal.SIGINT)
            stdout, stderr = await stopped(process)
        if os.name != "nt":
            assert process.returncode == (-signal.SIGTERM if shutdown == "terminate" else 0)
        assert bearer.encode() not in stdout + stderr
        assert key_material not in stdout + stderr
        assert b"Traceback" not in stdout + stderr
        assert "ALPHA/alice" not in hub.online_agents()


async def test_installed_http_command_reads_its_hub_token_file(tmp_path: Path) -> None:
    """The documented --token-file reaches a tokened hub through the real CLI entry."""
    issuer = Ed25519PrivateKey.generate()
    trust = provision(tmp_path / "grants.json", issuer)
    _, _key, tls = tls_files(tmp_path)
    token_file = tmp_path / "hub.token"
    token_file.write_text("cli-hub-token-" + "7" * 32 + "\n", encoding="utf-8")
    token_file.chmod(0o600)
    bearer = access_token(issuer, scope="synapse:read synapse:mutate")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    hub = SynapseHub(
        identity_trust_bundle=trust,
        require_identity_binding=True,
        authenticator=TokenAuthenticator(["cli-hub-token-" + "7" * 32]),
    )
    async with running_hub(hub) as (_, uri):
        environment = {key: value for key, value in os.environ.items() if key != "SYNAPSE_TOKEN"}
        process = await asyncio.create_subprocess_exec(
            *command(),
            *profile(tmp_path, uri, port),
            "--token-file",
            str(token_file),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=environment,
        )
        try:
            async with httpx2.AsyncClient(verify=tls, trust_env=False, timeout=5) as client:
                url = f"https://127.0.0.1:{port}/mcp"

                async def ready() -> None:
                    while process.returncode is None:
                        try:
                            await client.get(url)
                            return
                        except httpx2.ConnectError:
                            await asyncio.sleep(0.02)
                    raise RuntimeError("CLI stopped before HTTPS readiness")

                await asyncio.wait_for(ready(), 8)
                client.headers["Authorization"] = "Bearer " + bearer
                async with streamable_http_client(url, http_client=client) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        result = await session.call_tool(
                            "synapse_task_declare",
                            {"task_id": "ALPHA/token-file", "title": "token file"},
                            meta=operation_meta("token-file-declaration"),
                        )
                        assert not result.is_error
                        assert hub.blackboard.tasks["ALPHA/token-file"].created_by == "ALPHA/alice"
        finally:
            if process.returncode is None:
                process.terminate()
            stdout, stderr = await stopped(process)
        assert b"cli-hub-token" not in stdout + stderr
        assert b"invalid private HTTPS profile" not in stderr


async def test_ambient_token_environment_is_still_refused(tmp_path: Path) -> None:
    """SYNAPSE_TOKEN is not a source for the private profile; it refuses before startup."""
    environment = dict(os.environ, SYNAPSE_TOKEN="env-secret-must-not-be-logged")
    process = await asyncio.create_subprocess_exec(
        *command(),
        *profile(tmp_path, "ws://127.0.0.1:1", 8888),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=environment,
    )
    stdout, stderr = await stopped(process)
    assert process.returncode == 2
    assert b"invalid private HTTPS profile" in stderr
    assert b"env-secret-must-not-be-logged" not in stdout + stderr
