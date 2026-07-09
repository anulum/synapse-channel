# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — independent-client A2A interop traces against a live server

from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from a2a_server_helpers import RecordingAgent, _default_bridge, _free_port
from synapse_channel import cli
from synapse_channel.a2a_conformance import conformance_rows
from synapse_channel.a2a_http import build_a2a_handler
from synapse_channel.a2a_interop_trace import (
    RECEIPT_SCHEMA,
    A2AInteropTraceError,
    parse_endpoint,
    run_local_interop_trace,
    write_interop_receipt,
)
from synapse_channel.a2a_server import A2ABridge
from synapse_channel.a2a_store import A2ATaskStore


def _serve_bridge(bridge: A2ABridge) -> tuple[ThreadingHTTPServer, int, threading.Thread]:
    port = _free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), build_a2a_handler(bridge))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, port, thread


def test_parse_endpoint_accepts_http_url() -> None:
    host, port, prefix = parse_endpoint("http://127.0.0.1:8877/a2a")
    assert host == "127.0.0.1"
    assert port == 8877
    assert prefix == "/a2a"


def test_local_interop_trace_against_live_bridge(tmp_path: Path) -> None:
    """Independent http.client discovers the card, sends a message, and GETs the task."""
    bridge = _default_bridge()
    server, port, _thread = _serve_bridge(bridge)
    try:
        receipt = run_local_interop_trace(host="127.0.0.1", port=port, message_text="probe-1")
    finally:
        server.shutdown()
        server.server_close()

    assert receipt["schema"] == RECEIPT_SCHEMA
    assert receipt["client"]["stack"] == "http.client"
    assert receipt["discovery"]["http_status"] == 200
    assert receipt["discovery"]["agent_card_name"]
    assert receipt["task_lifecycle"]["send_http_status"] == 200
    assert receipt["task_lifecycle"]["get_http_status"] == 200
    assert receipt["task_lifecycle"]["task_id"]
    assert receipt["dimensions"]["discovery"] == "recorded"
    assert receipt["dimensions"]["task_lifecycle"] == "recorded"
    # Bridge agent received the independent client's text
    assert any("probe-1" in text for _target, text in bridge.agent.messages)

    out = write_interop_receipt(tmp_path / "receipt.json", receipt)
    loaded = json.loads(out.read_text(encoding="utf-8"))
    assert loaded["task_lifecycle"]["task_id"] == receipt["task_lifecycle"]["task_id"]


def test_interop_trace_fails_closed_when_bridge_down() -> None:
    with pytest.raises((A2AInteropTraceError, OSError)):
        run_local_interop_trace(host="127.0.0.1", port=1, timeout=0.3)


def test_cli_a2a_interop_trace_writes_receipt(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bridge = A2ABridge(
        agent=RecordingAgent(),
        agent_card=_default_bridge().agent_card,
        target="WORKER",
        store=A2ATaskStore(),
    )
    server, port, _thread = _serve_bridge(bridge)
    out = tmp_path / "cli-receipt.json"
    try:
        code = cli.main(
            [
                "a2a-interop-trace",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--output",
                str(out),
            ]
        )
    finally:
        server.shutdown()
        server.server_close()
    assert code == 0
    assert "wrote interop receipt" in capsys.readouterr().out
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["schema"] == RECEIPT_SCHEMA
    assert data["task_lifecycle"]["task_id"]


def test_conformance_marks_independent_interop_partial() -> None:
    row = next(r for r in conformance_rows() if r.item == "Independent interoperability")
    assert row.status == "partial"
    assert "a2a-interop-trace" in row.synapse_surface
    assert "http.client" in row.evidence
