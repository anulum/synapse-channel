# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — TLS/WSS hub deployment tests

from __future__ import annotations

import asyncio
import ssl
import subprocess
from pathlib import Path

import pytest
from websockets.asyncio.client import connect

from hub_e2e_helpers import _await_listening, _free_port, read_until_type
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.tls import HubTLSConfigError, build_server_ssl_context


def _write_self_signed_cert(tmp_path: Path) -> tuple[Path, Path]:
    certfile = tmp_path / "hub-cert.pem"
    keyfile = tmp_path / "hub-key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=localhost",
            "-addext",
            "subjectAltName=DNS:localhost,IP:127.0.0.1",
            "-keyout",
            str(keyfile),
            "-out",
            str(certfile),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return certfile, keyfile


def test_server_ssl_context_requires_complete_chain(tmp_path: Path) -> None:
    certfile, keyfile = _write_self_signed_cert(tmp_path)

    assert build_server_ssl_context(certfile=None, keyfile=None) is None
    with pytest.raises(HubTLSConfigError, match="requires both"):
        build_server_ssl_context(certfile=certfile, keyfile=None)
    with pytest.raises(HubTLSConfigError, match="requires both"):
        build_server_ssl_context(certfile=None, keyfile=keyfile)


def test_server_ssl_context_reports_invalid_chain(tmp_path: Path) -> None:
    certfile = tmp_path / "bad-cert.pem"
    keyfile = tmp_path / "bad-key.pem"
    certfile.write_text("not a certificate\n", encoding="utf-8")
    keyfile.write_text("not a key\n", encoding="utf-8")

    with pytest.raises(HubTLSConfigError, match="could not load hub TLS certificate chain"):
        build_server_ssl_context(certfile=certfile, keyfile=keyfile)


async def test_hub_serves_real_wss_connection(tmp_path: Path) -> None:
    certfile, keyfile = _write_self_signed_cert(tmp_path)
    server_context = build_server_ssl_context(certfile=certfile, keyfile=keyfile)
    client_context = ssl.create_default_context()
    client_context.check_hostname = False
    client_context.verify_mode = ssl.CERT_NONE

    hub = SynapseHub(hub_id="syn-wss")
    port = _free_port()
    task = asyncio.create_task(hub.serve("localhost", port, ssl_context=server_context))
    try:
        await _await_listening(port)
        async with connect(f"wss://localhost:{port}", ssl=client_context) as websocket:
            welcome = await read_until_type(websocket, "welcome")
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert welcome["hub_id"] == "syn-wss"


def test_hub_tls_public_docs_describe_native_wss() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")
    cli_docs = Path("docs/cli.md").read_text(encoding="utf-8")
    deployment_docs = Path("docs/deployment.md").read_text(encoding="utf-8")
    changelog = Path("CHANGELOG.md").read_text(encoding="utf-8")

    for document in (readme, cli_docs, deployment_docs, changelog):
        assert "--tls-certfile" in document
        assert "--tls-keyfile" in document
        assert "wss://" in document

    assert "does not replace `--token`" in deployment_docs
