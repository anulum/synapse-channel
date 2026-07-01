# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
"""End-to-end journey for the ``synapse supervisor`` stall watch.

The supervisor is a long-running, LLM-free process that watches the shared plan
and re-offers work that has gone quiet — it sets a stalled ``in_progress`` task's
status back to ``open`` so another agent can pick it up, and announces the
re-offer on the bus. This drives it as a subprocess against an isolated hub with
a tiny idle ceiling, leaves a task in progress, and observes both effects the way
an operator would: the board flips the task back to open, and the durable log
carries the supervisor's re-offer announcement.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from cli_e2e_helpers import isolated_hub, isolated_supervisor, run_cli


def _board_shows(hub_uri: str, marker: str, *, timeout: float = 8.0) -> bool:
    """Poll the board until a line containing ``marker`` appears."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        board = run_cli("board", uri=hub_uri, timeout=5.0)
        if board.ok() and marker in board.stdout:
            return True
        time.sleep(0.2)
    return False


def _supervisor_assessments(db_path: Path, name: str) -> list[str]:
    """Return the assessment notes the supervisor posted to the durable log."""
    drained = run_cli("ingest", str(db_path), "--kind", "ledger_progress")
    assert drained.ok(), drained.output
    texts = []
    for line in drained.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        payload = json.loads(line)["payload"]
        if payload.get("author") == name:
            texts.append(str(payload["text"]))
    return texts


def test_supervisor_reoffers_a_stalled_task(tmp_path: Path) -> None:
    with isolated_hub(tmp_path) as hub:
        declared = run_cli("task", "declare", "T1", "--title", "stalled work", uri=hub.uri)
        assert declared.ok(), declared.output
        in_progress = run_cli("task", "update", "T1", "--status", "in_progress", uri=hub.uri)
        assert in_progress.ok(), in_progress.output

        with isolated_supervisor(hub.uri, idle_seconds=1.0, interval=0.5) as supervisor:
            # The stalled in_progress task is re-offered: the board returns it to open.
            assert _board_shows(hub.uri, "[open] T1"), "supervisor did not re-offer T1"

        # And the supervisor recorded the re-offer as an assessment on the log.
        announcements = _supervisor_assessments(hub.db_path, supervisor)
        assert any("re-offering" in text for text in announcements), announcements
