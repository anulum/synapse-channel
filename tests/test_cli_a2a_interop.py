# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — module-owned A2A interoperability CLI tests
"""Exercise the A2A interoperability CLI through its production parser and HTTP edge."""

from __future__ import annotations

import argparse
import json
import threading
from collections.abc import Iterator
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from a2a_server_helpers import _default_bridge, _free_port
from synapse_channel import cli, cli_a2a_interop
from synapse_channel.a2a_http import build_a2a_handler
from synapse_channel.a2a_interop_trace import RECEIPT_SCHEMA
from synapse_channel.a2a_server import A2ABridge


@pytest.fixture
def live_bridge() -> Iterator[tuple[A2ABridge, int]]:
    """Run the production A2A HTTP handler on a disposable loopback port."""
    bridge = _default_bridge()
    server = ThreadingHTTPServer(("127.0.0.1", _free_port()), build_a2a_handler(bridge))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield bridge, server.server_port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


def test_add_parsers_registers_defaults_and_operator_overrides() -> None:
    """The module-owned parser exposes every interoperability control with stable defaults."""
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    cli_a2a_interop.add_parsers(subparsers)

    defaults = parser.parse_args(["a2a-interop-trace"])
    assert defaults.func is cli_a2a_interop._cmd_a2a_interop_trace
    assert defaults.endpoint_url is None
    assert defaults.host == "127.0.0.1"
    assert defaults.port == 8877
    assert defaults.a2a_token is None
    assert defaults.message == "synapse interop probe"
    assert defaults.timeout == 5.0
    assert defaults.output is None

    configured = parser.parse_args(
        [
            "a2a-interop-trace",
            "--endpoint-url",
            "http://127.0.0.1:9000/a2a",
            "--host",
            "ignored.example",
            "--port",
            "9001",
            "--a2a-token",
            "fixture-token",
            "--message",
            "contract probe",
            "--timeout",
            "1.5",
            "--output",
            "receipt.json",
        ]
    )
    assert configured.endpoint_url == "http://127.0.0.1:9000/a2a"
    assert configured.host == "ignored.example"
    assert configured.port == 9001
    assert configured.a2a_token == "fixture-token"
    assert configured.message == "contract probe"
    assert configured.timeout == 1.5
    assert configured.output == "receipt.json"


def test_cli_endpoint_url_prints_live_interop_receipt(
    live_bridge: tuple[A2ABridge, int], capsys: pytest.CaptureFixture[str]
) -> None:
    """The full CLI discovers, sends, and reads a task through a live bridge URL."""
    bridge, port = live_bridge

    code = cli.main(
        [
            "a2a-interop-trace",
            "--endpoint-url",
            f"http://127.0.0.1:{port}",
            "--message",
            "module-owned endpoint probe",
        ]
    )

    assert code == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["schema"] == RECEIPT_SCHEMA
    assert receipt["discovery"]["http_status"] == 200
    assert receipt["task_lifecycle"]["send_http_status"] == 200
    assert receipt["task_lifecycle"]["get_http_status"] == 200
    assert any("module-owned endpoint probe" in text for _target, text in bridge.agent.messages)


def test_cli_host_port_writes_live_receipt(
    live_bridge: tuple[A2ABridge, int],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The host/port path writes the same real receipt contract to an operator file."""
    _bridge, port = live_bridge
    output = tmp_path / "interop-receipt.json"

    code = cli.main(
        [
            "a2a-interop-trace",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--output",
            str(output),
        ]
    )

    assert code == 0
    assert capsys.readouterr().out.strip() == f"wrote interop receipt: {output}"
    receipt = json.loads(output.read_text(encoding="utf-8"))
    assert receipt["schema"] == RECEIPT_SCHEMA
    assert receipt["task_lifecycle"]["task_id"]


@pytest.mark.parametrize(
    ("arguments", "diagnostic"),
    [
        (["--endpoint-url", "ftp://peer.example/a2a"], "supports http:// endpoints only"),
        # Windows often surfaces a closed loopback port as a short-timeout rather
        # than the POSIX "connection refused" wording; accept either form.
        (
            ["--host", "127.0.0.1", "--port", "1", "--timeout", "0.1"],
            "connection refused|timed out",
        ),
    ],
)
def test_cli_reports_invalid_or_unreachable_bridge(
    arguments: list[str], diagnostic: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Invalid configuration and a refused live connection fail through the CLI boundary."""
    import re

    assert cli.main(["a2a-interop-trace", *arguments]) == 1
    error = capsys.readouterr().err
    assert error.startswith("a2a-interop-trace:")
    assert re.search(diagnostic, error.lower()) is not None
