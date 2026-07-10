# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real subprocess and OTLP collector fleet-scorecard journey
"""Run the packaged scorecard CLI against a live hub and HTTP collector."""

from __future__ import annotations

import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import (
    ExportMetricsServiceRequest,
)
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

from cli_e2e_helpers import isolated_hub, run_cli


def test_packaged_command_pushes_real_trace_and_metric_batches(tmp_path: Path) -> None:
    received: dict[str, tuple[str, bytes]] = {}

    class _Collector(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or "0")
            body = self.rfile.read(length) if length else b""
            received[self.path] = (str(self.headers.get("Content-Type")), body)
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, _format: str, *_args: Any) -> None:
            return None

    collector = ThreadingHTTPServer(("127.0.0.1", 0), _Collector)
    thread = threading.Thread(target=collector.serve_forever, daemon=True)
    thread.start()
    try:
        with isolated_hub(tmp_path) as hub:
            declared = run_cli("task", "declare", "BUILD", "--title", "build", uri=hub.uri)
            assert declared.ok(), declared.output
            locked = run_cli("lock", "BUILD", "--paths", "src/app.py", "--", "true", uri=hub.uri)
            assert locked.ok(), locked.output
            usage = run_cli(
                "accounting",
                "record",
                "--name",
                "worker-a",
                "--task",
                "BUILD",
                "--model",
                "model-a",
                "--input-tokens",
                "25",
                "--output-tokens",
                "10",
                uri=hub.uri,
            )
            assert usage.ok(), usage.output

            port = collector.server_address[1]
            exported = run_cli(
                "fleet-scorecard",
                str(hub.db_path),
                "--service-name",
                "e2e-hub",
                "--endpoint",
                f"http://127.0.0.1:{port}",
            )
            assert exported.ok(), exported.output
            assert "metric points" in exported.stdout
            assert "spans" in exported.stdout
    finally:
        collector.shutdown()
        collector.server_close()
        thread.join(timeout=2.0)

    assert set(received) == {"/v1/traces", "/v1/metrics"}
    for content_type, body in received.values():
        assert content_type == "application/x-protobuf"
        assert body

    traces = ExportTraceServiceRequest()
    traces.ParseFromString(received["/v1/traces"][1])
    spans = [
        span
        for resource in traces.resource_spans
        for scope in resource.scope_spans
        for span in scope.spans
    ]
    assert spans
    assert any(span.name == "BUILD" for span in spans)

    metrics = ExportMetricsServiceRequest()
    metrics.ParseFromString(received["/v1/metrics"][1])
    metric_names = {
        metric.name
        for resource in metrics.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
    }
    assert {
        "synapse.fleet.accounting.calls",
        "synapse.fleet.causality.spans",
        "synapse.fleet.conflicts",
        "synapse.fleet.reliability.findings",
    } <= metric_names

    resource_attributes = {
        attribute.key: attribute.value.string_value
        for resource in metrics.resource_metrics
        for attribute in resource.resource.attributes
    }
    assert resource_attributes["service.name"] == "e2e-hub"
