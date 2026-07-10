# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — OTLP fleet-scorecard metric adapter regressions
"""Exercise gauge conversion and the official OTLP/HTTP metrics boundary."""

from __future__ import annotations

from typing import Any

import pytest

from synapse_channel.core.fleet_scorecard_metrics import MetricPoint
from synapse_channel.otel_metrics_export import (
    METER_NAME,
    OTEL_METRICS_EXTRA_HINT,
    _require_otel_metrics,
    push_metric_points,
)


def _points() -> tuple[MetricPoint, ...]:
    return (
        MetricPoint(
            "synapse.fleet.accounting.calls",
            3,
            "{call}",
            "Opt-in calls.",
        ),
        MetricPoint(
            "synapse.fleet.accounting.calls",
            2,
            "{call}",
            "Opt-in calls.",
            (("agent", "alice"),),
        ),
        MetricPoint(
            "synapse.fleet.conflicts",
            1,
            "{pair}",
            "Overlapping claim pairs.",
        ),
    )


class _RecordingExporter:
    """Metric exporter that captures one SDK ``MetricsData`` batch."""

    def __init__(self, result: Any) -> None:
        self.result = result
        self.exported: list[Any] = []
        self.shutdowns = 0

    def export(self, data: Any) -> Any:
        self.exported.append(data)
        return self.result

    def shutdown(self) -> None:
        self.shutdowns += 1


def test_successful_push_preserves_resources_instruments_and_dimensions() -> None:
    from opentelemetry.sdk.metrics.export import MetricExportResult

    exporter = _RecordingExporter(MetricExportResult.SUCCESS)

    count = push_metric_points(
        _points(),
        "http://collector:4318/v1/metrics",
        service_name="hub-eu",
        exporter_factory=lambda endpoint, timeout: exporter,
    )

    assert count == 3
    assert exporter.shutdowns == 1
    assert len(exporter.exported) == 1
    data = exporter.exported[0]
    resource = data.resource_metrics[0]
    assert resource.resource.attributes["service.name"] == "hub-eu"
    scope = resource.scope_metrics[0]
    assert scope.scope.name == METER_NAME
    by_name = {metric.name: metric for metric in scope.metrics}
    calls = by_name["synapse.fleet.accounting.calls"]
    assert calls.unit == "{call}"
    assert calls.description == "Opt-in calls."
    values = {
        (tuple(sorted(point.attributes.items())), point.value) for point in calls.data.data_points
    }
    assert values == {((), 3), ((("agent", "alice"),), 2)}
    assert by_name["synapse.fleet.conflicts"].data.data_points[0].value == 1


def test_failed_push_is_visible_and_still_shuts_the_exporter_down() -> None:
    from opentelemetry.sdk.metrics.export import MetricExportResult

    exporter = _RecordingExporter(MetricExportResult.FAILURE)

    with pytest.raises(RuntimeError, match="OTLP metric export .* failed"):
        push_metric_points(
            _points(),
            "http://collector:4318/v1/metrics",
            service_name="hub-eu",
            exporter_factory=lambda endpoint, timeout: exporter,
        )
    assert exporter.shutdowns == 1


def test_default_factory_exports_to_a_real_local_collector() -> None:
    """The official exporter sends a protobuf batch to a live HTTP receiver."""
    import threading
    from http import HTTPStatus
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    received: list[tuple[str, str, int]] = []

    class _Collector(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or "0")
            body = self.rfile.read(length) if length else b""
            received.append((self.path, str(self.headers.get("Content-Type")), len(body)))
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, _format: str, *_args: Any) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Collector)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        count = push_metric_points(
            _points(),
            f"http://127.0.0.1:{port}/v1/metrics",
            service_name="hub-local",
            timeout=5.0,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert count == 3
    assert received == [("/v1/metrics", "application/x-protobuf", received[0][2])]
    assert received[0][2] > 0


def test_empty_input_skips_the_optional_sdk_and_network() -> None:
    def _refuse(name: str) -> Any:
        raise AssertionError(name)

    assert (
        push_metric_points(
            (),
            "",
            service_name="",
            timeout=0.0,
            import_module=_refuse,
        )
        == 0
    )


@pytest.mark.parametrize(
    ("endpoint", "service_name", "timeout", "match"),
    [
        (" ", "hub", 1.0, "endpoint must not be blank"),
        ("http://collector/v1/metrics", " ", 1.0, "service name must not be blank"),
        ("http://collector/v1/metrics", "hub", 0.0, "timeout must be positive"),
    ],
)
def test_invalid_export_configuration_is_refused(
    endpoint: str,
    service_name: str,
    timeout: float,
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        push_metric_points(
            _points(),
            endpoint,
            service_name=service_name,
            timeout=timeout,
        )


def test_missing_extra_raises_the_install_hint() -> None:
    def _refuse(name: str) -> Any:
        raise ImportError(name)

    with pytest.raises(RuntimeError, match="synapse-channel\\[otel\\]"):
        push_metric_points(
            _points(),
            "http://collector/v1/metrics",
            service_name="hub",
            import_module=_refuse,
        )


def test_reader_without_metrics_data_fails_visible(monkeypatch: pytest.MonkeyPatch) -> None:
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader

    monkeypatch.setattr(InMemoryMetricReader, "get_metrics_data", lambda _self: None)

    with pytest.raises(RuntimeError, match="reader produced no data"):
        push_metric_points(
            _points(),
            "http://collector/v1/metrics",
            service_name="hub",
        )


def test_require_otel_metrics_returns_the_installed_sdk() -> None:
    modules = _require_otel_metrics()

    assert hasattr(modules.sdk_metrics, "MeterProvider")
    assert hasattr(modules.otlp_http, "OTLPMetricExporter")
    assert OTEL_METRICS_EXTRA_HINT.startswith("OTLP metric export needs")
