# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the declared operations and one operation's chain, from the event store
"""Serve ``/operations.json``: the declared operations, or one operation's chain.

The feed reads the dashboard's configured event store with
:mod:`synapse_channel.core.operation_report`, as ``synapse operation report``
does, so it states what the hub journalled and nothing more. Without ``operation``
it lists the declared operations newest first, one bounded page at a time, each
with its state, its task, its declarer and its latest journalled row. With
``operation=ID`` it returns that operation's whole chain, or ``present: false``
when nothing is recorded under the id.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from synapse_channel.core.operation_report import (
    OperationReport,
    operation_report_to_json,
    run_operation_report,
    run_operation_reports,
)

OPERATIONS_PATH: Final = "/operations.json"
"""Read-only dashboard endpoint for the declared operations and their chains."""

DEFAULT_OPERATIONS_LIMIT: Final = 50
"""Operations in one page when the request names no ``limit``."""

MAX_OPERATIONS_LIMIT: Final = 200
"""The largest page one request can ask for."""

MAX_OPERATION_ID_LENGTH: Final = 512
"""Longest operation id accepted at the HTTP boundary."""


def _row(report: OperationReport) -> dict[str, object]:
    """Return one operation's list row: its state and where its chain stands."""
    declared = report.chain[0]
    latest = report.chain[-1]
    return {
        "operation_id": report.operation_id,
        "task_id": report.task_id,
        "declarer": declared.actor,
        "declared_seq": declared.seq,
        "state": report.state,
        "latest_seq": latest.seq,
        "latest_kind": latest.kind,
        "latest_at": latest.at,
    }


def build_operations_feed(
    db_path: str | Path,
    *,
    before: int | None = None,
    limit: int = DEFAULT_OPERATIONS_LIMIT,
    key_file: str | Path | None = None,
) -> dict[str, object]:
    """Return one page of the declared operations, newest declaration first.

    Parameters
    ----------
    db_path : str or pathlib.Path
        The hub event store.
    before : int or None, optional
        An exclusive declaration-sequence cursor; ``None`` starts at the newest.
    limit : int, optional
        The page size, held between 1 and :data:`MAX_OPERATIONS_LIMIT`.
    key_file : str, pathlib.Path or None, optional
        The owner-only key of an encrypted store.

    Returns
    -------
    dict
        ``operations`` (the page), ``total`` (every declared operation),
        ``next_before`` (the cursor of the next page, or ``None``), ``limit``,
        ``as_of_seq`` and ``custody``.
    """
    bounded = max(1, min(int(limit), MAX_OPERATIONS_LIMIT))
    reports = run_operation_reports(db_path, key_file=key_file)
    newest_first = sorted(reports, key=lambda report: report.chain[0].seq, reverse=True)
    eligible = [report for report in newest_first if before is None or report.chain[0].seq < before]
    page = eligible[:bounded]
    more = len(eligible) > bounded
    return {
        "operations": [_row(report) for report in page],
        "total": len(reports),
        "next_before": page[-1].chain[0].seq if more and page else None,
        "limit": bounded,
        "as_of_seq": reports[0].as_of_seq if reports else 0,
        "custody": "not recorded here",
    }


def build_operation_chain_feed(
    db_path: str | Path, operation_id: str, *, key_file: str | Path | None = None
) -> dict[str, object]:
    """Return one operation's chain, with ``present`` saying whether anything is recorded."""
    report = run_operation_report(db_path, operation_id, key_file=key_file)
    return {**operation_report_to_json(report), "present": bool(report.chain)}
