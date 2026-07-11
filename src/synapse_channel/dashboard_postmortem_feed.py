# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — task postmortem projection for the dashboard
"""Build a read-only JSON postmortem from the configured dashboard event store."""

from __future__ import annotations

from pathlib import Path
from typing import Final

from synapse_channel.core.postmortem import postmortem_to_json, run_task_postmortem

POSTMORTEM_PATH: Final = "/postmortem.json"
"""Read-only dashboard endpoint for one replayable task report."""

MAX_POSTMORTEM_TASK_ID_LENGTH: Final = 512
"""Maximum accepted task identifier length at the HTTP boundary."""


def build_postmortem_feed(
    db_path: str | Path,
    task_id: str,
    *,
    key_file: str | Path | None = None,
) -> dict[str, object]:
    """Return one replayable task report with an honest presence indicator."""
    report = run_task_postmortem(db_path, task_id, key_file=key_file)
    document = postmortem_to_json(report)
    document["present"] = bool(report.timeline)
    document["note"] = (
        "replayable task evidence from the configured durable event store; "
        "an empty timeline means no matching task event was recorded"
    )
    return document
