# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — OTLP export adapter regressions

from __future__ import annotations

from typing import Any

import pytest

from synapse_channel.core.causality_otel import (
    SERVICE_NAME,
    build_otel_projection,
    span_id_for_event,
    trace_id_for_task,
)
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import StoredEvent
from synapse_channel.otel_export import (
    OTEL_EXTRA_HINT,
    _require_otel,
    push_projection,
    sdk_spans,
)


def _events() -> tuple[StoredEvent, ...]:
    return (
        StoredEvent(
            seq=1,
            ts=1.0,
            kind=EventKind.CLAIM,
            payload={
                "task_id": "B",
                "owner": "alice",
                "status": "claimed",
                "paths": ["src/x"],
                "worktree": "w",
            },
        ),
        StoredEvent(seq=2, ts=2.0, kind=EventKind.RELEASE, payload={"task_id": "B"}),
        StoredEvent(
            seq=3,
            ts=3.0,
            kind=EventKind.CLAIM,
            payload={
                "task_id": "C",
                "owner": "carol",
                "status": "claimed",
                "paths": ["src/x"],
                "worktree": "w",
            },
        ),
    )


class _RecordingExporter:
    """Fake exporter capturing the spans it is asked to export."""

    def __init__(self, result: Any) -> None:
        self.result = result
        self.exported: list[Any] = []
        self.shutdowns = 0

    def export(self, spans: Any) -> Any:
        self.exported.extend(spans)
        return self.result

    def shutdown(self) -> None:
        self.shutdowns += 1


class TestSdkSpans:
    def test_records_convert_to_readable_spans_with_ids_preserved(self) -> None:
        from opentelemetry.sdk.trace import ReadableSpan

        spans = sdk_spans(build_otel_projection(_events()))

        assert all(isinstance(span, ReadableSpan) for span in spans)
        by_name = {span.name: span for span in spans}
        claim = by_name["claim B"]
        assert format(claim.context.trace_id, "032x") == trace_id_for_task("B")
        assert format(claim.context.span_id, "016x") == span_id_for_event(1)
        assert claim.parent is not None
        assert claim.resource.attributes["service.name"] == SERVICE_NAME

    def test_contention_link_survives_the_conversion(self) -> None:
        spans = sdk_spans(build_otel_projection(_events()))

        freed = next(span for span in spans if span.name == "claim C")
        assert len(freed.links) == 1
        link = freed.links[0]
        assert format(link.context.span_id, "016x") == span_id_for_event(2)
        assert link.attributes["synapse.relation"] == "contention"

    def test_root_spans_have_no_parent(self) -> None:
        spans = sdk_spans(build_otel_projection(_events()))

        roots = [span for span in spans if span.parent is None]
        assert sorted(span.name for span in roots) == ["B", "C"]


class TestPushProjection:
    def test_successful_push_reports_the_span_count(self) -> None:
        from opentelemetry.sdk.trace.export import SpanExportResult

        exporter = _RecordingExporter(SpanExportResult.SUCCESS)

        count = push_projection(
            build_otel_projection(_events()),
            "http://collector:4318/v1/traces",
            exporter_factory=lambda endpoint, timeout: exporter,
        )

        assert count == 5
        assert len(exporter.exported) == 5
        assert exporter.shutdowns == 1

    def test_failed_push_raises_and_still_shuts_down(self) -> None:
        from opentelemetry.sdk.trace.export import SpanExportResult

        exporter = _RecordingExporter(SpanExportResult.FAILURE)

        with pytest.raises(RuntimeError, match="OTLP export .* failed"):
            push_projection(
                build_otel_projection(_events()),
                "http://collector:4318/v1/traces",
                exporter_factory=lambda endpoint, timeout: exporter,
            )
        assert exporter.shutdowns == 1

    def test_default_factory_builds_the_official_otlp_exporter(self) -> None:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        built: list[Any] = []
        original_init = OTLPSpanExporter.__init__

        def _capture(self: Any, *args: Any, **kwargs: Any) -> None:
            built.append(kwargs)
            original_init(self, *args, **kwargs)
            self._captured = True

        class _Refuses(RuntimeError):
            pass

        # Building the real exporter is enough; exporting would hit the network,
        # so the factory path is exercised with a monkeypatched export.
        import unittest.mock

        with unittest.mock.patch.object(OTLPSpanExporter, "__init__", _capture):
            with unittest.mock.patch.object(OTLPSpanExporter, "export", side_effect=_Refuses):
                with pytest.raises(_Refuses):
                    push_projection(
                        build_otel_projection(_events()),
                        "http://127.0.0.1:1/v1/traces",
                        timeout=0.1,
                    )
        assert built == [{"endpoint": "http://127.0.0.1:1/v1/traces", "timeout": 0.1}]

    def test_missing_extra_raises_the_install_hint(self) -> None:
        def _refuse(name: str) -> Any:
            raise ImportError(name)

        with pytest.raises(RuntimeError, match="synapse-channel\\[otel\\]"):
            push_projection(
                build_otel_projection(_events()),
                "http://collector:4318/v1/traces",
                import_module=_refuse,
            )


def test_require_otel_returns_the_real_modules_when_installed() -> None:
    modules = _require_otel()

    assert hasattr(modules.trace_api, "SpanContext")
    assert hasattr(modules.otlp_http, "OTLPSpanExporter")
    assert OTEL_EXTRA_HINT.startswith("OTLP export needs")
