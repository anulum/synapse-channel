# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — push fleet scorecard gauges through OTLP HTTP
"""Convert fleet scorecard points to OTel gauges and push them over OTLP.

The core scorecard remains independent of OpenTelemetry. This adapter lazily
loads the optional SDK, records every point into a one-shot in-memory reader,
and hands the resulting ``MetricsData`` to the official OTLP/HTTP exporter.
Export failure is visible, and both the exporter and meter provider are shut
down on every path.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from synapse_channel.core.fleet_scorecard_metrics import MetricPoint
from synapse_channel.otel_export import DEFAULT_EXPORT_TIMEOUT

OTEL_METRICS_EXTRA_HINT = (
    "OTLP metric export needs the optional 'otel' extra: pip install 'synapse-channel[otel]'"
)
"""Install hint raised when the OpenTelemetry metrics SDK is unavailable."""

METER_NAME = "synapse-channel.fleet-scorecard"
"""Instrumentation scope attached to exported fleet scorecard gauges."""

MetricExporterFactory = Callable[[str, float], Any]
"""Build a metric exporter from endpoint and timeout; injectable for tests."""


@dataclass(frozen=True)
class _OtelMetricModules:
    """Lazily imported OpenTelemetry modules used by the metric adapter."""

    sdk_metrics: Any
    sdk_export: Any
    resources: Any
    otlp_http: Any


def push_metric_points(
    points: Sequence[MetricPoint],
    endpoint: str,
    *,
    service_name: str,
    timeout: float = DEFAULT_EXPORT_TIMEOUT,
    exporter_factory: MetricExporterFactory | None = None,
    import_module: Callable[[str], Any] = importlib.import_module,
) -> int:
    """Push scorecard points as synchronous gauges to an OTLP/HTTP collector.

    Parameters
    ----------
    points : collections.abc.Sequence[MetricPoint]
        Numeric observations from the pure fleet scorecard.
    endpoint : str
        Collector's full OTLP metrics URL, normally ending ``/v1/metrics``.
    service_name : str
        OpenTelemetry ``service.name`` resource value.
    timeout : float, optional
        Export timeout in seconds.
    exporter_factory : MetricExporterFactory or None, optional
        Builds an exporter from ``(endpoint, timeout)``; the official exporter
        is used when omitted.
    import_module : Callable[[str], Any], optional
        Import hook for deterministic missing-extra tests.

    Returns
    -------
    int
        Number of metric points exported. Empty input returns ``0`` without
        importing the optional SDK or contacting the endpoint.

    Raises
    ------
    ValueError
        If ``endpoint`` is blank, ``service_name`` is blank, or ``timeout`` is
        not positive.
    RuntimeError
        If the optional SDK is unavailable, the SDK produces no metrics data,
        or the exporter reports failure.
    """
    if not points:
        return 0
    if not endpoint.strip():
        msg = "OTLP metrics endpoint must not be blank"
        raise ValueError(msg)
    if not service_name.strip():
        msg = "OTLP service name must not be blank"
        raise ValueError(msg)
    if timeout <= 0:
        msg = "OTLP metric export timeout must be positive"
        raise ValueError(msg)

    modules = _require_otel_metrics(import_module)
    reader = modules.sdk_export.InMemoryMetricReader()
    provider = modules.sdk_metrics.MeterProvider(
        metric_readers=[reader],
        resource=modules.resources.Resource.create({"service.name": service_name}),
    )
    try:
        meter = provider.get_meter(METER_NAME)
        instruments: dict[tuple[str, str, str], Any] = {}
        for point in points:
            key = (point.name, point.unit, point.description)
            gauge = instruments.get(key)
            if gauge is None:
                gauge = meter.create_gauge(
                    point.name,
                    unit=point.unit,
                    description=point.description,
                )
                instruments[key] = gauge
            gauge.set(point.value, dict(point.attributes))
        metrics_data = reader.get_metrics_data()
        if metrics_data is None:
            msg = "OpenTelemetry metrics reader produced no data"
            raise RuntimeError(msg)
        exporter = (
            modules.otlp_http.OTLPMetricExporter(endpoint=endpoint, timeout=timeout)
            if exporter_factory is None
            else exporter_factory(endpoint, timeout)
        )
        try:
            result = exporter.export(metrics_data)
        finally:
            exporter.shutdown()
        if result is not modules.sdk_export.MetricExportResult.SUCCESS:
            msg = f"OTLP metric export to {endpoint} failed: {result}"
            raise RuntimeError(msg)
        return len(points)
    finally:
        provider.shutdown()


def _require_otel_metrics(
    import_module: Callable[[str], Any] = importlib.import_module,
) -> _OtelMetricModules:
    """Import the metric SDK modules or raise the optional-extra hint."""
    try:
        return _OtelMetricModules(
            sdk_metrics=import_module("opentelemetry.sdk.metrics"),
            sdk_export=import_module("opentelemetry.sdk.metrics.export"),
            resources=import_module("opentelemetry.sdk.resources"),
            otlp_http=import_module("opentelemetry.exporter.otlp.proto.http.metric_exporter"),
        )
    except ImportError as exc:
        raise RuntimeError(OTEL_METRICS_EXTRA_HINT) from exc
