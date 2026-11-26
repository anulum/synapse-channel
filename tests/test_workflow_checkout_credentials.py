# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — no workflow persists a checkout credential
"""Keep every checkout credential-free, and the one write token inside the step that pushes.

``actions/checkout`` writes the job token into ``.git/config`` unless told not to,
and every later step of the job, including build tools and third-party actions,
can then read it. Every checkout therefore sets ``persist-credentials: false``.
The only job that pushes, the metrics snapshot, receives the token in its push
step alone, through gh's credential helper.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = ROOT / ".github" / "workflows"
METRICS_WORKFLOW = WORKFLOW_DIR / "pypi-downloads.yml"
TOKEN = "${{ github.token }}"


def _steps() -> list[tuple[str, str, dict[str, Any]]]:
    """Return ``(workflow, job, step)`` for every step of every workflow."""
    workflows = sorted([*WORKFLOW_DIR.glob("*.yml"), *WORKFLOW_DIR.glob("*.yaml")])
    assert workflows, "no workflow was found"
    found: list[tuple[str, str, dict[str, Any]]] = []
    for path in workflows:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        for job_name, job in document["jobs"].items():
            found.extend((path.name, job_name, step) for step in job.get("steps", []))
    return found


def test_every_checkout_refuses_to_persist_its_credential() -> None:
    """No ``actions/checkout`` step leaves the job token in the checkout's git config."""
    checkouts = [
        (workflow, job, step)
        for workflow, job, step in _steps()
        if str(step.get("uses", "")).startswith("actions/checkout@")
    ]
    assert checkouts, "no checkout step was found"
    persisted = [
        (workflow, job, step.get("name"))
        for workflow, job, step in checkouts
        if (step.get("with") or {}).get("persist-credentials") is not False
    ]
    assert persisted == []


def test_the_metrics_push_alone_receives_the_write_token() -> None:
    """The push step holds the token through gh's helper; the step that runs Python never does."""
    text = METRICS_WORKFLOW.read_text(encoding="utf-8")
    steps: list[dict[str, Any]] = yaml.safe_load(text)["jobs"]["snapshot"]["steps"]
    assert text.count("github.token") == 1
    assert "secrets.GITHUB_TOKEN" not in text
    (push,) = [step for step in steps if "git push" in step.get("run", "")]
    assert push["if"] == "steps.snapshot.outputs.committed == 'true'"
    assert push["env"]["GH_TOKEN"] == TOKEN
    assert push["env"]["GIT_CONFIG_COUNT"] == "2"
    assert [push["env"]["GIT_CONFIG_KEY_0"], push["env"]["GIT_CONFIG_VALUE_0"]] == [
        "credential.helper",
        "",
    ]
    assert [push["env"]["GIT_CONFIG_KEY_1"], push["env"]["GIT_CONFIG_VALUE_1"]] == [
        "credential.helper",
        "!gh auth git-credential",
    ]
    assert "python" not in push["run"]
    snapshot = next(step for step in steps if step.get("id") == "snapshot")
    assert "python" in snapshot["run"]
    assert "git push" not in snapshot["run"]
    assert 'echo "committed=true" >> "$GITHUB_OUTPUT"' in snapshot["run"]
    holders = [step.get("name") for step in steps if TOKEN in str(step.get("env", {}))]
    assert holders == [push["name"]]
