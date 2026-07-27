# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — independent-client A2A interop traces against a live server

from __future__ import annotations

import json
import stat
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from a2a_server_helpers import RecordingAgent, _default_bridge, _free_port
from synapse_channel import a2a_interop_trace as interop_module
from synapse_channel import cli
from synapse_channel.a2a_conformance import conformance_rows
from synapse_channel.a2a_credentials import A2APlaintextBearerError
from synapse_channel.a2a_http import build_a2a_handler
from synapse_channel.a2a_interop_trace import (
    RECEIPT_SCHEMA,
    A2AInteropTraceError,
    parse_endpoint,
    run_local_interop_trace,
    write_interop_receipt,
)
from synapse_channel.a2a_outbound_response import A2A_MAX_JSON_MEMBERS
from synapse_channel.a2a_server import A2ABridge
from synapse_channel.a2a_store import A2ATaskStore


def _serve_bridge(bridge: A2ABridge) -> tuple[ThreadingHTTPServer, int, threading.Thread]:
    port = _free_port()
    bridge.allowed_authorities = (f"127.0.0.1:{port}",)
    interfaces = bridge.agent_card.get("supportedInterfaces")
    if isinstance(interfaces, list) and interfaces and isinstance(interfaces[0], dict):
        interfaces[0]["url"] = f"http://127.0.0.1:{port}"
    server = ThreadingHTTPServer(("127.0.0.1", port), build_a2a_handler(bridge))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, port, thread


def test_parse_endpoint_accepts_http_url() -> None:
    scheme, host, port, prefix = parse_endpoint("http://127.0.0.1:8877/a2a")
    assert scheme == "http"
    assert host == "127.0.0.1"
    assert port == 8877
    assert prefix == "/a2a"


def test_parse_endpoint_accepts_https_url() -> None:
    scheme, host, port, prefix = parse_endpoint("https://127.0.0.1:9443")
    assert scheme == "https"
    assert host == "127.0.0.1"
    assert port == 9443
    assert prefix == ""


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


def test_interop_trace_refuses_bearer_to_named_plaintext_peer() -> None:
    with pytest.raises(A2APlaintextBearerError, match="literal loopback"):
        run_local_interop_trace(host="peer.example", token="never echoed")


def test_interop_request_wraps_wide_json_without_echoing_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = json.dumps({"items": ["peer-secret"] * A2A_MAX_JSON_MEMBERS}).encode()

    class Response:
        status = 200
        headers: dict[str, str] = {}

        def read(self, amount: int) -> bytes:
            return body[:amount]

    class Connection:
        def request(self, *_args: object, **_kwargs: object) -> None:
            return None

        def getresponse(self) -> Response:
            return Response()

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        "synapse_channel.a2a_interop_trace.http.client.HTTPConnection",
        lambda *_args, **_kwargs: Connection(),
    )

    with pytest.raises(A2AInteropTraceError) as caught:
        interop_module._request("127.0.0.1", 8877, "GET", "/agent-card.json")
    assert str(A2A_MAX_JSON_MEMBERS) in str(caught.value)
    assert "peer-secret" not in str(caught.value)


def test_interop_missing_text_error_does_not_echo_message_or_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses: list[tuple[int, dict[str, object] | str]] = [
        (200, {"name": "card"}),
        (
            200,
            {
                "task": {
                    "id": "peer-secret-task-id",
                    "status": {"state": "TASK_STATE_WORKING"},
                    "history": [],
                },
                "peer-secret-field": "peer-secret-result",
            },
        ),
    ]
    monkeypatch.setattr(interop_module, "_request", lambda *_args, **_kwargs: responses.pop(0))

    with pytest.raises(A2AInteropTraceError) as caught:
        run_local_interop_trace(message_text="peer-secret-message")
    assert "peer-secret-message" not in str(caught.value)
    assert "peer-secret-task-id" not in str(caught.value)
    assert "peer-secret-result" not in str(caught.value)


def test_interop_task_mismatch_error_does_not_echo_identifiers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = "probe"
    responses: list[tuple[int, dict[str, object] | str]] = [
        (200, {"name": "card"}),
        (
            200,
            {
                "task": {
                    "id": "peer-secret-sent-id",
                    "status": {"state": "TASK_STATE_WORKING"},
                    "history": [{"parts": [{"text": message}]}],
                }
            },
        ),
        (200, {"id": "peer-secret-got-id"}),
    ]
    monkeypatch.setattr(interop_module, "_request", lambda *_args, **_kwargs: responses.pop(0))

    with pytest.raises(A2AInteropTraceError) as caught:
        run_local_interop_trace(message_text=message)
    assert str(caught.value) == "task id mismatch between send and get responses"
    assert "peer-secret-sent-id" not in str(caught.value)
    assert "peer-secret-got-id" not in str(caught.value)


def test_cli_a2a_interop_trace_writes_receipt(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bridge = A2ABridge(
        agent=RecordingAgent(),
        agent_card=_default_bridge().agent_card,
        target="WORKER",
        store=A2ATaskStore(),
        auth_token="file-bearer",
    )
    server, port, _thread = _serve_bridge(bridge)
    out = tmp_path / "cli-receipt.json"
    token_file = tmp_path / "a2a.token"
    token_file.write_text("file-bearer\n", encoding="utf-8")
    token_file.chmod(0o600)
    try:
        code = cli.main(
            [
                "a2a-interop-trace",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--a2a-token-file",
                str(token_file),
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
    if sys.platform != "win32":
        assert stat.S_IMODE(out.stat().st_mode) == 0o600


def test_cli_a2a_interop_refuses_remote_plaintext_bearer_before_io(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = cli.main(
        [
            "a2a-interop-trace",
            "--host",
            "peer.example",
            "--a2a-token",
            "never-print-this-bearer",
        ]
    )
    assert code == 2
    error = capsys.readouterr().err
    assert "plaintext HTTP" in error
    assert "never-print-this-bearer" not in error


def test_cli_a2a_interop_parser_exposes_safe_file_and_unsafe_override() -> None:
    args = cli.build_parser().parse_args(
        [
            "a2a-interop-trace",
            "--a2a-token-file",
            "/run/secrets/a2a",
            "--a2a-allow-insecure-http",
        ]
    )
    assert args.a2a_token_file == "/run/secrets/a2a"
    assert args.a2a_allow_insecure_http is True


def test_conformance_marks_independent_interop_partial() -> None:
    row = next(r for r in conformance_rows() if r.item == "Independent interoperability")
    assert row.status == "partial"
    assert "a2a-interop-trace" in row.synapse_surface
    # Evidence names the dual-peer CLI and official-sdk surfaces, not a raw
    # stdlib transport string (http.client is an implementation detail).
    assert "a2a-client" in row.evidence or "a2a-sdk" in row.evidence
    assert "interop" in row.evidence.lower() or "TCK" in row.evidence
