# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — executable public fastest-safe-trial contract
"""Bind every published fastest-safe-trial block to one real CLI journey."""

from __future__ import annotations

import json
import shlex
from pathlib import Path

import pytest

from cli_e2e_helpers import run_cli

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PUBLIC_GUIDES = (
    _REPO_ROOT / "README.md",
    _REPO_ROOT / "docs" / "quickstart.md",
    _REPO_ROOT / "docs" / "cli.md",
    _REPO_ROOT / "docs" / "installation.md",
)
_HEADING = "Fastest safe trial path"
_EXPECTED_COMMANDS = (
    "python -m pip install synapse-channel",
    "synapse doctor",
    "synapse demo --output ./synapse-golden-demo",
)


def _fastest_safe_trial_commands(path: Path) -> tuple[str, ...]:
    """Return the first Bash command block under the golden-path heading."""
    text = path.read_text(encoding="utf-8")
    heading_at = text.find(_HEADING)
    assert heading_at >= 0, f"{path} is missing the {_HEADING!r} heading"
    section = text[heading_at:]
    opening = section.find("```bash\n")
    assert opening >= 0, f"{path} is missing the golden-path Bash block"
    body_start = opening + len("```bash\n")
    closing = section.find("\n```", body_start)
    assert closing >= 0, f"{path} has an unterminated golden-path Bash block"
    return tuple(line for line in section[body_start:closing].splitlines() if line)


@pytest.mark.real_hub
def test_published_fastest_safe_trial_runs_the_exact_golden_demo(tmp_path: Path) -> None:
    """All public copies select and execute the complete golden loop."""
    published = tuple(_fastest_safe_trial_commands(path) for path in _PUBLIC_GUIDES)
    assert published == (_EXPECTED_COMMANDS,) * len(_PUBLIC_GUIDES)

    demo = shlex.split(_EXPECTED_COMMANDS[-1])
    assert demo[:2] == ["synapse", "demo"]
    output = tmp_path / "synapse-golden-demo"
    result = run_cli("demo", "--output", str(output), timeout=90)
    assert result.ok(), result.output
    assert "success: coordination demo completed" in result.stdout

    evidence = json.loads((output / "golden-demo.json").read_text(encoding="utf-8"))
    assert evidence["completed"] is True
    assert evidence["guard"]["before_handoff"]["allowed"] is False
    assert evidence["guard"]["after_handoff"]["allowed"] is True
    assert evidence["receipt"]["epistemic_status"] == "supported"
    assert (output / "golden-demo-dashboard.html").is_file()


def test_optional_a2a_is_a_follow_on_in_every_public_copy() -> None:
    """The first-value path never treats an A2A bridge as a prerequisite."""
    for path in _PUBLIC_GUIDES:
        text = path.read_text(encoding="utf-8")
        commands = _fastest_safe_trial_commands(path)
        assert all("a2a" not in command for command in commands)
        assert "Optional A2A interoperability is a follow-on" in " ".join(text.split())
