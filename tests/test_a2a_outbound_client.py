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

from a2a_server_helpers import RecordingAgent, _default_bridge, _free_port
from synapse_channel import cli
from synapse_channel.a2a_client import A2AOutboundClient, parse_a2a_endpoint
from synapse_channel.a2a_http import build_a2a_handler
from synapse_channel.a2a_server import A2ABridge
from synapse_channel.a2a_store import A2ATaskStore


def _serve(bridge: A2ABridge) -> tuple[ThreadingHTTPServer, int]:
    port = _free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), build_a2a_handler(bridge))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, port


def test_parse_a2a_endpoint_https() -> None:
    scheme, host, port, prefix = parse_a2a_endpoint("https://127.0.0.1:9443/a2a")
    assert scheme == "https"
    assert host == "127.0.0.1"
    assert port == 9443
    assert prefix == "/a2a"


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
    )
    server, port = _serve(bridge)
    out = tmp_path / "outbound.json"
    try:
        code = cli.main(
            [
                "a2a-client",
                "--endpoint-url",
                f"http://127.0.0.1:{port}",
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
