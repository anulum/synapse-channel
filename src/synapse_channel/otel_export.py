# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — push causality span records to an OTLP endpoint via the OTel SDK
"""Convert causality span records to OpenTelemetry SDK spans and push them over OTLP.

:mod:`synapse_channel.core.causality_otel` is pure — it projects the event log
onto plain span *records* with no OpenTelemetry import. This adapter is the one
place that touches the SDK: it converts those records into
``opentelemetry.sdk.trace.ReadableSpan`` objects (deterministic ids preserved,
cross-task causality carried as span links) and hands them to the official
OTLP/HTTP exporter. The SDK and exporter live behind the optional ``otel``
extra (``pip install 'synapse-channel[otel]'``); importing this module without
the extra stays safe — the dependency is resolved lazily per call and a missing
install raises a clear hint, the same pattern as the ``wasm`` extra.

The push is fail-visible: the exporter's verdict is checked and a failed export
raises instead of pretending success, and the exporter is always shut down. The
endpoint is used verbatim — an OTLP/HTTP collector expects the full traces URL,
typically ``http://host:4318/v1/traces``.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from synapse_channel.core.causality_otel import SERVICE_NAME, OtelProjection, OtelSpanRecord

OTEL_EXTRA_HINT = "OTLP export needs the optional 'otel' extra: pip install 'synapse-channel[otel]'"
"""Install hint raised when the OpenTelemetry SDK is not importable."""

DEFAULT_EXPORT_TIMEOUT = 10.0
"""Seconds the OTLP exporter waits for the collector before giving up."""

ExporterFactory = Callable[[str, float], Any]
"""Build a span exporter for an endpoint and timeout — injectable for tests."""


@dataclass(frozen=True)
class _OtelModules:
    """The lazily imported OpenTelemetry modules the conversion needs."""

    trace_api: Any
    sdk_trace: Any
    sdk_export: Any
    resources: Any
    otlp_http: Any


def _require_otel(
    import_module: Callable[[str], Any] = importlib.import_module,
) -> _OtelModules:
    """Import the SDK modules, or raise a clear install hint when absent."""
    try:
        return _OtelModules(
            trace_api=import_module("opentelemetry.trace"),
            sdk_trace=import_module("opentelemetry.sdk.trace"),
            sdk_export=import_module("opentelemetry.sdk.trace.export"),
            resources=import_module("opentelemetry.sdk.resources"),
            otlp_http=import_module("opentelemetry.exporter.otlp.proto.http.trace_exporter"),
        )
    except ImportError as exc:
        raise RuntimeError(OTEL_EXTRA_HINT) from exc


def push_projection(
    projection: OtelProjection,
    endpoint: str,
    *,
    timeout: float = DEFAULT_EXPORT_TIMEOUT,
    exporter_factory: ExporterFactory | None = None,
    import_module: Callable[[str], Any] = importlib.import_module,
) -> int:
    """Push a span projection to an OTLP/HTTP collector.

    Parameters
    ----------
    projection : OtelProjection
        The span records built by
        :func:`~synapse_channel.core.causality_otel.run_otel_projection`.
    endpoint : str
        The collector's full traces URL, e.g. ``http://localhost:4318/v1/traces``;
        used verbatim.
    timeout : float, optional
        Seconds to wait for the collector. Defaults to
        :data:`DEFAULT_EXPORT_TIMEOUT`.
    exporter_factory : ExporterFactory or None, optional
        Builds the exporter from ``(endpoint, timeout)``; the official
        ``OTLPSpanExporter`` when ``None``. Injectable for tests.
    import_module : Callable[[str], Any], optional
        Import hook, injectable for the missing-extra path.

    Returns
    -------
    int
        The number of spans exported.

    Raises
    ------
    RuntimeError
        If the ``otel`` extra is not installed or the exporter reports failure.
    """
    modules = _require_otel(import_module)
    spans = sdk_spans(projection, modules=modules)
    exporter = (
        modules.otlp_http.OTLPSpanExporter(endpoint=endpoint, timeout=timeout)
        if exporter_factory is None
        else exporter_factory(endpoint, timeout)
    )
    try:
        result = exporter.export(spans)
    finally:
        exporter.shutdown()
    if result is not modules.sdk_export.SpanExportResult.SUCCESS:
        msg = f"OTLP export to {endpoint} failed: {result}"
        raise RuntimeError(msg)
    return len(spans)


def sdk_spans(
    projection: OtelProjection,
    *,
    modules: _OtelModules | None = None,
) -> list[Any]:
    """Convert span records into SDK ``ReadableSpan`` objects.

    Parameters
    ----------
    projection : OtelProjection
        The pure span records to convert.
    modules : _OtelModules or None, optional
        The imported SDK modules; resolved via :func:`_require_otel` when
        ``None``.

    Returns
    -------
    list
        ``ReadableSpan`` objects, ids and links preserved from the records.
    """
    otel = _require_otel() if modules is None else modules
    resource = otel.resources.Resource.create({"service.name": SERVICE_NAME})
    return [_sdk_span(span, otel, resource) for span in projection.spans]


def _sdk_span(span: OtelSpanRecord, otel: _OtelModules, resource: Any) -> Any:
    """Convert one span record into a ``ReadableSpan``."""
    parent = (
        _span_context(span.trace_id_hex, span.parent_span_id_hex, otel)
        if span.parent_span_id_hex
        else None
    )
    links = [
        otel.trace_api.Link(
            _span_context(link.trace_id_hex, link.span_id_hex, otel),
            {"synapse.relation": link.relation, "synapse.detail": link.detail},
        )
        for link in span.links
    ]
    return otel.sdk_trace.ReadableSpan(
        name=span.name,
        context=_span_context(span.trace_id_hex, span.span_id_hex, otel),
        parent=parent,
        resource=resource,
        attributes=dict(span.attributes),
        links=links,
        kind=otel.trace_api.SpanKind.INTERNAL,
        start_time=span.start_ns,
        end_time=span.end_ns,
    )


def _span_context(trace_id_hex: str, span_id_hex: str, otel: _OtelModules) -> Any:
    """Build a sampled ``SpanContext`` from deterministic hex ids."""
    return otel.trace_api.SpanContext(
        trace_id=int(trace_id_hex, 16),
        span_id=int(span_id_hex, 16),
        is_remote=False,
        trace_flags=otel.trace_api.TraceFlags(otel.trace_api.TraceFlags.SAMPLED),
    )
