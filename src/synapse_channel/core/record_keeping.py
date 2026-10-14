# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — snapshot-bound record-keeping bundle
"""Commit retained events and expose indicative traceability categories.

The commitment folds with logarithmic state. Collected entry metadata takes
linear memory; event and payload budgets bound each call. A root requires the
original selected journal for recomputation and is not a signed witness.
"""

from __future__ import annotations

import contextlib
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import MappingProxyType

from synapse_channel.core.event_row_mac import load_or_create_row_mac_key
from synapse_channel.core.merkle import MerkleRoot, RunningRoot, root_to_json
from synapse_channel.core.record_keeping_source import (
    DEFAULT_MAX_EVENTS,
    DEFAULT_MAX_PAYLOAD_BYTES,
    iter_record_source,
)

SCHEMA = "eu-ai-act-record-keeping.v1"
RISK_INDICATOR = "risk_indicator"
OPERATION_RECORD = "operation_record"
GENERAL_RECORD = "general_record"
PURPOSE_ARTICLE: Mapping[str, str] = MappingProxyType(
    {
        RISK_INDICATOR: "Indicative risk-related coordination evidence; Article 12(2)(a)",
        OPERATION_RECORD: "Indicative operation evidence; Article 12(2)(c)",
        GENERAL_RECORD: "Other coordination evidence without a per-event article mapping",
    }
)
SCOPE_NOTICE = (
    "This bundle measures retained coordination records. Indicative categories "
    "can support an operator's Article 12(1)-(2) evidence where applicable. "
    "It does not classify an AI system, certify compliance, establish complete "
    "historical logging or replace applicable retention and data-protection rules. "
    "It does not implement the Article 12(3) biometric logging requirements."
)
_RISK_KINDS = frozenset(
    {
        "finding",
        "identity_pin_reclaim",
        "dead_letter_escalation",
        "delivery_receipt_expired",
    }
)
_OPERATION_KINDS = frozenset(
    {
        "claim",
        "release",
        "task_update",
        "ledger_task",
        "ledger_progress",
        "checkpoint",
        "handoff",
        "resource",
        "sandbox_run",
        "operator_relay",
        "mailbox_watermark",
        "dead_letter_forwarding",
        "delivery_receipt_requested",
        "delivery_receipt_immediate",
        "delivery_receipt_deferred",
    }
)


def purpose_for_kind(kind: str) -> str:
    """Return an indicative category; unknown kinds remain general records.

    Parameters
    ----------
    kind : str
        Persisted coordination kind, not an assessment of regulatory risk.

    Returns
    -------
    str
        A risk indicator, operation record or general record.
    """
    if kind in _RISK_KINDS:
        return RISK_INDICATOR
    return OPERATION_RECORD if kind in _OPERATION_KINDS else GENERAL_RECORD


def utc_event_time(timestamp: float) -> datetime:
    """Convert a finite Unix timestamp to UTC, including dates before 1970.

    Parameters
    ----------
    timestamp : float
        Seconds from the Unix epoch.

    Returns
    -------
    datetime
        Timezone-aware UTC time.

    Raises
    ------
    ValueError
        The value cannot be represented by the supported UTC calendar.
    """
    try:
        if (
            isinstance(timestamp, bool)
            or not isinstance(timestamp, int | float)
            or not math.isfinite(timestamp)
        ):
            raise ValueError("timestamp must be a finite number")
        return datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=timestamp)
    except (OverflowError, TypeError) as exc:
        raise ValueError("timestamp is outside the supported UTC calendar") from exc


@dataclass(frozen=True, slots=True)
class RecordKeepingEntry:
    """Retained event metadata, without its private payload.

    Attributes
    ----------
    seq, ts, kind : int, float, str
        Journal sequence, Unix append timestamp and event kind.
    iso_time : str
        UTC calendar representation of the append timestamp.
    purpose : str
        Indicative coordination category.
    row_mac_verified : bool
        The trusted supplied row key verified this particular row.
    """

    seq: int
    ts: float
    iso_time: str
    kind: str
    purpose: str
    row_mac_verified: bool


@dataclass(frozen=True, slots=True)
class RecordKeepingExport:
    """One consistent snapshot's commitment and retained metadata.

    Attributes
    ----------
    schema : str
        Bundle format identifier.
    merkle : MerkleRoot
        RFC 6962 commitment over exactly the selected full events.
    entries : tuple of RecordKeepingEntry
        Metadata in ascending sequence order.
    purpose_counts : Mapping[str, int]
        Immutable category counts.
    first_ts, last_ts : float or None
        Chronological minimum and maximum, independent of sequence order.
    """

    schema: str
    merkle: MerkleRoot
    entries: tuple[RecordKeepingEntry, ...]
    purpose_counts: Mapping[str, int]
    first_ts: float | None
    last_ts: float | None


def build_export(
    db_path: str | Path,
    *,
    through_seq: int | None = None,
    key_file: str | Path | None = None,
    row_mac_key_file: str | Path | None = None,
    max_events: int = DEFAULT_MAX_EVENTS,
    max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES,
) -> RecordKeepingExport:
    """Build a complete selected bundle through an existing read-only snapshot.

    Parameters
    ----------
    db_path : str or pathlib.Path
        Existing SQLite or SQLCipher journal.
    through_seq : int or None, optional
        Inclusive sequence ceiling; zero selects an empty prefix.
    key_file, row_mac_key_file : str or pathlib.Path or None, optional
        Existing operator-selected encryption and row-authentication keys.
        Missing keys are never created. Legacy unverified rows remain explicit.
    max_events, max_payload_bytes : int, optional
        Positive caller budgets. Exhaustion refuses rather than truncates.

    Returns
    -------
    RecordKeepingExport
        All selected entries, their root and chronological extrema.

    Raises
    ------
    ValueError, OSError, RuntimeError
        Invalid input, unavailable storage, failed row verification or budget.
    """
    row_key = (
        None
        if row_mac_key_file is None
        else load_or_create_row_mac_key(
            row_mac_key_file,
            current_max_seq=0,
            log_has_macs=True,
        )
    )
    running = RunningRoot()
    entries: list[RecordKeepingEntry] = []
    counts = dict.fromkeys(PURPOSE_ARTICLE, 0)
    first: float | None = None
    last: float | None = None
    with contextlib.closing(
        iter_record_source(
            db_path,
            through_seq=through_seq,
            key_file=key_file,
            row_mac=row_key,
            max_events=max_events,
            max_payload_bytes=max_payload_bytes,
        )
    ) as source:
        for row in source:
            event = row.event
            stamp = utc_event_time(event.ts).isoformat()
            running.add(event)
            purpose = purpose_for_kind(event.kind)
            counts[purpose] += 1
            entries.append(
                RecordKeepingEntry(
                    event.seq,
                    event.ts,
                    stamp,
                    event.kind,
                    purpose,
                    row.authenticated,
                )
            )
            first = event.ts if first is None else min(first, event.ts)
            last = event.ts if last is None else max(last, event.ts)
    return RecordKeepingExport(
        SCHEMA,
        running.commit(through_seq=through_seq),
        tuple(entries),
        MappingProxyType(counts),
        first,
        last,
    )


def export_to_json(export: RecordKeepingExport) -> dict[str, object]:
    """Return the full metadata bundle, commitment and authentication counts."""
    verified = sum(entry.row_mac_verified for entry in export.entries)
    return {
        "schema": export.schema,
        "scope_notice": SCOPE_NOTICE,
        "merkle": root_to_json(export.merkle),
        "entry_count": len(export.entries),
        "first_ts": export.first_ts,
        "last_ts": export.last_ts,
        "purpose_counts": dict(export.purpose_counts),
        "purpose_legend": dict(PURPOSE_ARTICLE),
        "row_authentication": {
            "verified_events": verified,
            "unverified_events": len(export.entries) - verified,
            "scope": "Row MAC verification is distinct from a Merkle commitment or signed witness.",
        },
        "entries": [
            {
                "seq": entry.seq,
                "ts": entry.ts,
                "iso_time": entry.iso_time,
                "kind": entry.kind,
                "purpose": entry.purpose,
                "row_mac_verified": entry.row_mac_verified,
            }
            for entry in export.entries
        ],
    }


def render_human(export: RecordKeepingExport) -> str:
    """Return a payload-free summary of the selected snapshot and its limits."""
    root = export.merkle
    verified = sum(entry.row_mac_verified for entry in export.entries)
    lines = [
        "Record-keeping export",
        f"Schema: {export.schema}",
        f"Merkle root: {root.root}",
        f"Committed events: {root.tree_size} (seq {root.first_seq}..{root.last_seq})",
        f"Row MACs verified: {verified}; unverified: {len(export.entries) - verified}",
    ]
    if export.first_ts is None or export.last_ts is None:
        lines.append("Time span: empty")
    else:
        lines.append(
            f"Time span: {utc_event_time(export.first_ts).isoformat()} .. "
            f"{utc_event_time(export.last_ts).isoformat()}"
        )
    lines.extend(
        f"- {name}: {export.purpose_counts[name]} — {text}"
        for name, text in PURPOSE_ARTICLE.items()
    )
    return "\n".join([*lines, "", SCOPE_NOTICE])
