# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — required-check completeness contract
"""Pin the aggregating ``ci`` gate that branch protection requires.

Branch protection requires a single ``ci`` status context. GitHub derives a
check context from the *job* name, so a workflow merely *named* ``ci`` never
posts it — the context only exists if a job is named ``ci``. This locks that
gate in place and forces every future job to be gated, so the required check
stays meaningful instead of silently drifting back into a phantom.
"""

from __future__ import annotations

import re
from pathlib import Path

CI_WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"


def _jobs_section() -> str:
    return CI_WORKFLOW.read_text(encoding="utf-8").split("\njobs:\n", 1)[1]


def _gate_block() -> str:
    section = _jobs_section()
    assert "\n  ci:\n" in section, "the required `ci` gate job is missing from ci.yml"
    return section.split("\n  ci:\n", 1)[1]


def test_ci_gate_job_names_the_required_context_and_always_runs() -> None:
    block = _gate_block()
    # The job name is the status context branch protection matches on.
    assert "name: ci" in block
    # It must post even when a gated job fails, or a red PR would show no `ci`
    # context at all and merge as if unguarded.
    assert "if: always()" in block


def test_ci_gate_needs_every_other_job() -> None:
    section = _jobs_section()
    all_jobs = set(re.findall(r"^  ([a-z][\w-]*):$", section, flags=re.MULTILINE))
    needs = re.search(r"needs:\s*\[([^\]]+)\]", _gate_block())
    assert needs is not None, "the ci gate must declare the jobs it aggregates"
    gated = {name.strip() for name in needs.group(1).split(",")}
    assert gated == all_jobs - {"ci"}


def test_ci_gate_fails_unless_each_result_is_success() -> None:
    block = _gate_block()
    assert 'if [ "${result}" != "success" ]' in block
    assert "exit 1" in block
