# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — shard timing history selector policy tests
"""The shard timing history must come from a successful push to main.

A pull-request or manually dispatched run on main also reports branch=main,
so the selector must name the push event explicitly; otherwise one such run
could seed the shard schedule of every later main run.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
_SELECTOR = re.compile(
    r'gh api "repos/\$GITHUB_REPOSITORY/actions/workflows/([\w.-]+)/runs\?([^"]+)"'
)


def _selectors(workflow: str) -> list[tuple[str, dict[str, list[str]]]]:
    """Return every previous-run selector in one workflow's run steps."""
    document = yaml.safe_load((ROOT / ".github/workflows" / workflow).read_text(encoding="utf-8"))
    found = []
    for job in document["jobs"].values():
        for step in job.get("steps", []):
            for match in _SELECTOR.finditer(str(step.get("run", ""))):
                found.append((match.group(1), parse_qs(urlsplit("?" + match.group(2)).query)))
    return found


@pytest.mark.parametrize("workflow", ["ci.yml", "cross-os.yml"])
def test_timing_history_comes_from_one_successful_main_push(workflow: str) -> None:
    selectors = _selectors(workflow)
    assert selectors, f"{workflow} has no timing history selector"
    for selected_workflow, query in selectors:
        assert selected_workflow == workflow
        assert query == {
            "branch": ["main"],
            "event": ["push"],
            "status": ["success"],
            "per_page": ["1"],
        }
