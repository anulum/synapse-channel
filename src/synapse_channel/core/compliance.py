# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — framework-labelled record observations
"""Compose retained-log evidence; no automatic regulatory classification."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from synapse_channel.core.record_keeping import (
    RecordKeepingEntry as RecordKeepingEntry,
)
from synapse_channel.core.record_keeping import (
    RecordKeepingExport as RecordKeepingExport,
)
from synapse_channel.core.record_keeping import (
    build_export as build_export,
)
from synapse_channel.core.record_keeping import (
    export_to_json as export_to_json,
)
from synapse_channel.core.record_keeping import (
    purpose_for_kind as purpose_for_kind,
)
from synapse_channel.core.record_keeping import (
    render_human as render_human,
)
from synapse_channel.core.record_keeping import (
    utc_event_time,
)
from synapse_channel.core.record_keeping_source import DEFAULT_MAX_EVENTS, DEFAULT_MAX_PAYLOAD_BYTES
from synapse_channel.core.record_retention import (
    RetentionPosture as RetentionPosture,
)
from synapse_channel.core.record_retention import (
    assess_retention as assess_retention,
)
from synapse_channel.core.record_retention import (
    render_retention as render_retention,
)
from synapse_channel.core.record_retention import (
    retention_to_json as retention_to_json,
)

FRAMEWORKS = ("eu-ai-act",)
ARTICLES = ("12(1)-(2)", "19(1)", "26(6)")


@dataclass(frozen=True, slots=True)
class ComplianceReport:
    """Labelled observations, with no compliance verdict.

    Attributes
    ----------
    framework : str
        Supported label ``eu-ai-act``.
    export : RecordKeepingExport
        Complete selected snapshot and its commitment.
    retention : RetentionPosture
        The same snapshot's observed calendar retention reach.
    """

    framework: str
    export: RecordKeepingExport
    retention: RetentionPosture


def build_report(
    db_path: str | Path,
    *,
    framework: str,
    now: float,
    through_seq: int | None = None,
    key_file: str | Path | None = None,
    row_mac_key_file: str | Path | None = None,
    max_events: int = DEFAULT_MAX_EVENTS,
    max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES,
) -> ComplianceReport:
    """Read and assess one snapshot under a supported framework label.

    Parameters
    ----------
    db_path : str or pathlib.Path
        Existing event journal, never provisioned by this reader.
    framework : str
        ``eu-ai-act``; other labels are refused before reading storage.
    now : float
        Finite reference Unix timestamp for the calendar comparison.
    through_seq : int or None, optional
        Inclusive sequence ceiling passed to the snapshot reader.
    key_file, row_mac_key_file : str or pathlib.Path or None, optional
        Existing operator-selected storage and row-authentication keys.
    max_events, max_payload_bytes : int, optional
        Complete export budgets; exhaustion refuses the report.

    Returns
    -------
    ComplianceReport
        Observations from a single consistent snapshot.

    Raises
    ------
    ValueError, OSError, RuntimeError
        Unsupported label, invalid clock, storage, authentication or budgets.
    """
    if framework not in FRAMEWORKS:
        raise ValueError("unsupported record-keeping framework")
    utc_event_time(now)
    export = build_export(
        db_path,
        through_seq=through_seq,
        key_file=key_file,
        row_mac_key_file=row_mac_key_file,
        max_events=max_events,
        max_payload_bytes=max_payload_bytes,
    )
    return ComplianceReport(framework, export, assess_retention(export, now=now))


def report_to_json(report: ComplianceReport) -> dict[str, object]:
    """Return the full selected metadata and retention observation together."""
    return {
        "framework": report.framework,
        "framework_label": "EU AI Act",
        "articles_addressed": list(ARTICLES),
        "record_keeping": export_to_json(report.export),
        "retention": retention_to_json(report.retention),
    }


def render_report(report: ComplianceReport) -> str:
    """Render both observations with their explicit evidence boundaries."""
    return "\n\n".join(
        [
            "EU AI Act record observations — no compliance verdict",
            render_human(report.export),
            render_retention(report.retention),
        ]
    )
