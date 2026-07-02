# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the shipped policy-check GitHub Action stays wired to the real CLI

"""Contract tests for the root ``action.yml`` composite GitHub Action.

The action cannot run outside GitHub's runner, so these tests pin what can
drift silently: the declared inputs against the flags the wrapped
``synapse policy-check`` parser actually accepts, the env-mediated quoting
discipline (no ``${{ inputs.* }}`` interpolated into a ``run:`` body, where it
would be a shell-injection vector), the supply-chain pin staying identical to
the one the repo's own CI uses, and the output plumbing through
``GITHUB_OUTPUT``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, cast

import yaml

from synapse_channel.cli import build_parser

ROOT = Path(__file__).resolve().parents[1]
ACTION = ROOT / "action.yml"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def _action() -> dict[str, Any]:
    return cast("dict[str, Any]", yaml.safe_load(ACTION.read_text(encoding="utf-8")))


def _run_steps(action: dict[str, Any]) -> list[dict[str, Any]]:
    return [step for step in action["runs"]["steps"] if "run" in step]


def test_action_is_composite_with_required_inputs() -> None:
    action = _action()
    assert action["runs"]["using"] == "composite"
    inputs = action["inputs"]
    for name in ("task", "policy", "receipt-json"):
        assert inputs[name]["required"] is True, name
    for name in ("enforce", "merkle-db", "trusted-signing-keys", "version", "python-version"):
        assert inputs[name]["required"] is False, name
    assert inputs["enforce"]["default"] == "true"


def test_action_flags_exist_on_the_real_policy_check_parser() -> None:
    """Every long flag the action passes must parse; a rename would break users."""
    action_script = "\n".join(step["run"] for step in _run_steps(_action()))
    flags = set(re.findall(r"--[a-z-]+", action_script)) - {
        "--disable-pip-version-check",
    }
    parser = build_parser()
    args = parser.parse_args(
        [
            "policy-check",
            "T",
            "--policy",
            "p.json",
            "--receipt-json",
            "r.json",
            "--json",
            "--enforce",
            "--merkle-db",
            "hub.db",
            "--trusted-signing-key",
            "hub.pub",
        ]
    )
    accepted = {"--policy", "--receipt-json", "--json", "--enforce", "--merkle-db", "--trusted-signing-key"}
    assert flags == accepted
    assert args.enforce is True
    assert args.trusted_signing_keys == ["hub.pub"]


def test_action_run_bodies_never_interpolate_inputs_directly() -> None:
    """Inputs must reach bash through env, never via ``${{ }}`` in the script."""
    for step in _run_steps(_action()):
        assert "${{" not in step["run"], step.get("name", "unnamed step")
        assert step["shell"] == "bash"


def test_action_inputs_are_all_mapped_into_env() -> None:
    """Every declared input feeds a step (env or a with-block), so none is dead."""
    action = _action()
    steps = action["runs"]["steps"]
    consumed: set[str] = set()
    for step in steps:
        for value in list(step.get("env", {}).values()) + list(step.get("with", {}).values()):
            consumed.update(re.findall(r"inputs\.([a-z-]+)", str(value)))
    assert consumed == set(action["inputs"])


def test_action_setup_python_pin_matches_the_repo_ci() -> None:
    """The composite reuses the exact setup-python SHA the repo's CI verified."""
    action_uses = [
        step["uses"] for step in _action()["runs"]["steps"] if "uses" in step
    ]
    assert len(action_uses) == 1
    ci_text = CI_WORKFLOW.read_text(encoding="utf-8")
    pinned = set(re.findall(r"uses: (actions/setup-python@[0-9a-f]{40})", ci_text))
    assert len(pinned) == 1
    assert action_uses[0].split(" #")[0].strip() in pinned


def test_action_emits_the_report_output() -> None:
    action = _action()
    assert action["outputs"]["report"]["value"] == "${{ steps.check.outputs.report }}"
    check = next(step for step in action["runs"]["steps"] if step.get("id") == "check")
    assert "GITHUB_OUTPUT" in check["run"]
    # The exit status must be the CLI's own, preserved across the output write.
    assert 'exit "$status"' in check["run"]


def test_action_forwards_each_trusted_key_line_as_its_own_flag() -> None:
    check = next(step for step in _run_steps(_action()) if step.get("id") == "check")
    assert 'args+=(--trusted-signing-key "$key")' in check["run"]
    assert "while IFS= read -r key" in check["run"]
