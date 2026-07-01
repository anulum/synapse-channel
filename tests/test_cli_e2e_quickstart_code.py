# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
"""End-to-end journey: the quickstart's copy-paste Python snippet must run.

``docs/quickstart.md`` tells a new user to start a hub and then ``Coordinate from
code`` with a self-contained ``SynapseAgent`` snippet. Other doc tests only assert
that snippet's *text* is present. This journey extracts the exact fenced block,
points it at a throwaway hub, and runs it as a fresh subprocess — so the code a
reader pastes is proven to connect, claim, checkpoint, and release for real. It
also guards the ``connect()``-is-once-only shape the snippet was written to teach:
if the welcome-then-verbs ordering regresses, ``wait_until_ready`` returns false,
the snippet raises, and this test fails.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from cli_e2e_helpers import isolated_hub, run_cli

_REPO_ROOT = Path(__file__).resolve().parents[1]
_QUICKSTART = _REPO_ROOT / "docs" / "quickstart.md"
_DEFAULT_URI = "ws://localhost:8876"


def _coordinate_from_code_snippet() -> str:
    """Return the ``Coordinate from code`` Python block from the quickstart guide."""
    doc = _QUICKSTART.read_text(encoding="utf-8")
    section = doc.split("## Coordinate from code", 1)
    assert len(section) == 2, "quickstart is missing the 'Coordinate from code' section"
    match = re.search(r"```python\n(.*?)```", section[1], re.DOTALL)
    assert match is not None, "no python code block under 'Coordinate from code'"
    snippet = match.group(1)
    # The block is only worth running if it still teaches the whole verb path.
    for token in ("SynapseAgent", "wait_until_ready", "claim(", "save_checkpoint(", "release("):
        assert token in snippet, f"quickstart snippet no longer contains {token!r}"
    assert _DEFAULT_URI in snippet, "quickstart snippet no longer targets the default hub URI"
    return snippet


def test_quickstart_coordinate_from_code_snippet_runs_against_a_hub(tmp_path: Path) -> None:
    """The pasted quickstart snippet connects and coordinates on a real hub."""
    snippet = _coordinate_from_code_snippet().replace(_DEFAULT_URI, "ws://localhost:{port}")

    with isolated_hub(tmp_path) as hub:
        runnable = snippet.format(port=hub.port)
        completed = subprocess.run(  # noqa: S603 - fixed interpreter, doc snippet
            [sys.executable, "-c", runnable],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        # verbose SynapseAgent prints its connection banner once ready.
        assert "Online" in completed.stdout, completed.stdout

        # The hub durably recorded the snippet's claim and checkpoint — proof the
        # verbs coordinated, not merely that the process exited zero.
        timeline = run_cli("event-query", str(hub.db_path), "task refactor-parser timeline")
        assert timeline.ok(), timeline.output
        assert "kind=claim" in timeline.stdout, timeline.output
        assert "kind=checkpoint" in timeline.stdout, timeline.output
        assert "owner=ALPHA" in timeline.stdout, timeline.output
