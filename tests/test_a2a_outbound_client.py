# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — outbound A2A client against a second peer process
"""Dual-process outbound client: discover → send → get against live serve."""

from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from a2a_server_helpers import RecordingAgent, _default_bridge, _free_port
from synapse_channel import cli
from synapse_channel.a2a_client import A2AOutboundClient, parse_a2a_endpoint
from synapse_channel.a2a_credentials import A2APlaintextBearerError
from synapse_channel.a2a_http import build_a2a_handler
from synapse_channel.a2a_server import A2ABridge
from synapse_channel.a2a_store import A2ATaskStore


def _serve(bridge: A2ABridge) -> tuple[ThreadingHTTPServer, int]:
    port = _free_port()
    bridge.allowed_authorities = (f"127.0.0.1:{port}",)
    interfaces = bridge.agent_card.get("supportedInterfaces")
    if isinstance(interfaces, list) and interfaces and isinstance(interfaces[0], dict):
        interfaces[0]["url"] = f"http://127.0.0.1:{port}"
    server = ThreadingHTTPServer(("127.0.0.1", port), build_a2a_handler(bridge))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, port


def test_parse_a2a_endpoint_https() -> None:
    scheme, host, port, prefix = parse_a2a_endpoint("https://127.0.0.1:9443/a2a")
    assert scheme == "https"
    assert host == "127.0.0.1"
    assert port == 9443
    assert prefix == "/a2a"


def test_outbound_client_refuses_bearer_to_named_plaintext_peer() -> None:
    with pytest.raises(A2APlaintextBearerError, match="literal loopback"):
        A2AOutboundClient("http://peer.example:8877", token="never echoed")


def test_outbound_client_explicit_plaintext_override_allows_configuration() -> None:
    client = A2AOutboundClient(
        "http://peer.example:8877",
        token="secret",
        allow_insecure_http=True,
    )
    assert client.host == "peer.example"


def test_outbound_client_discover_send_get_twice() -> None:
    bridge = A2ABridge(
        agent=RecordingAgent(),
        agent_card=_default_bridge().agent_card,
        target="WORKER",
        store=A2ATaskStore(),
    )
    server, port = _serve(bridge)
    endpoint = f"http://127.0.0.1:{port}"
    client = A2AOutboundClient(endpoint, timeout=5.0)
    receipts = []
    try:
        for index in (1, 2):
            text = f"outbound-probe-{index}"
            receipt = client.discover_send_get(text)
            receipts.append(receipt)
            assert receipt["scheme"] == "http"
            assert receipt["task_id"]
            assert receipt["message_text"] == text
            task = receipt["task"]
            assert isinstance(task, dict)
            assert task.get("id") == receipt["task_id"]
            # Payload content present on history or status message
            blob = json.dumps(task)
            assert text in blob or text in json.dumps(receipt["send_response"])
    finally:
        server.shutdown()
        server.server_close()
    assert receipts[0]["task_id"] != receipts[1]["task_id"]
    assert any("outbound-probe-1" in text for _t, text in bridge.agent.messages)
    assert any("outbound-probe-2" in text for _t, text in bridge.agent.messages)


def test_cli_a2a_client_writes_receipt(tmp_path: Path) -> None:
    bridge = A2ABridge(
        agent=RecordingAgent(),
        agent_card=_default_bridge().agent_card,
        target="WORKER",
        store=A2ATaskStore(),
        auth_token="file-bearer",
    )
    server, port = _serve(bridge)
    out = tmp_path / "outbound.json"
    token_file = tmp_path / "a2a.token"
    token_file.write_text("file-bearer\n", encoding="utf-8")
    token_file.chmod(0o600)
    try:
        code = cli.main(
            [
                "a2a-client",
                "--endpoint-url",
                f"http://127.0.0.1:{port}",
                "--a2a-token-file",
                str(token_file),
                "--message",
                "cli-outbound",
                "--output",
                str(out),
            ]
        )
    finally:
        server.shutdown()
        server.server_close()
    assert code == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["task_id"]
    assert data["message_text"] == "cli-outbound"


def test_cli_a2a_client_refuses_remote_plaintext_bearer_before_io(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = cli.main(
        [
            "a2a-client",
            "--endpoint-url",
            "http://peer.example:8877",
            "--a2a-token",
            "never-print-this-bearer",
        ]
    )
    assert code == 2
    error = capsys.readouterr().err
    assert "plaintext HTTP" in error
    assert "never-print-this-bearer" not in error


def test_cli_a2a_client_parser_exposes_safe_file_and_unsafe_override() -> None:
    args = cli.build_parser().parse_args(
        [
            "a2a-client",
            "--endpoint-url",
            "https://peer.example",
            "--a2a-token-file",
            "/run/secrets/a2a",
            "--a2a-allow-insecure-http",
        ]
    )
    assert args.a2a_token_file == "/run/secrets/a2a"
    assert args.a2a_allow_insecure_http is True
