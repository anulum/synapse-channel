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

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from hub_e2e_helpers import running_hub
from mcp_http_helpers import access_token, provision, tls_files
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
            async with httpx.AsyncClient(verify=tls, trust_env=False, timeout=5) as client:
                url = f"https://127.0.0.1:{port}/mcp"

                async def ready() -> None:
                    while process.returncode is None:
                        try:
                            response = await client.get(url)
                            assert response.status_code == 401
                            return
                        except httpx.ConnectError:
                            await asyncio.sleep(0.02)
                    raise RuntimeError("CLI stopped before HTTPS readiness")

                await asyncio.wait_for(ready(), 8)
                client.headers["Authorization"] = "Bearer " + bearer
                async with streamable_http_client(url, http_client=client) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        assert (await session.initialize()).protocolVersion == "2025-11-25"
                        result = await session.call_tool(
                            "synapse_task_declare",
                            {"task_id": "ALPHA/cli", "title": "CLI task"},
                            meta={"synapse/operation-id": "cli-declaration"},
                        )
                        assert not result.isError
                        assert hub.blackboard.tasks["ALPHA/cli"].created_by == "ALPHA/alice"
                        result = await session.call_tool(
                            "synapse_task_declare",
                            {"task_id": "BETA/cli", "title": "denied"},
                            meta={"synapse/operation-id": "cli-denied"},
                        )
                        assert result.isError
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
