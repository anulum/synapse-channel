# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — CLI rendering for mutation-governance posture
"""Register and render ``synapse adapters mutation-status``."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from synapse_channel.mutation_governance import inspect_mutation_governance


def _command(args: argparse.Namespace) -> int:
    home = Path(args.home).expanduser() if args.home else Path.home()
    project = Path(args.project).expanduser() if args.project else Path.cwd()
    opencode_config_root = home / ".config" if args.home else None
    report = inspect_mutation_governance(
        home=home,
        project=project,
        opencode_config_root=opencode_config_root,
    )
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False, sort_keys=True))
        return 0

    print("Mutation governance posture (read-only)")
    print(f"Overall enforcement: {report.overall_enforcement}")
    print(f"{'provider':<10} {'runtime':<12} {'configuration':<16} enforcement")
    for provider in report.providers:
        runtime = "detected" if provider.runtime_detected else "not-detected"
        print(
            f"{provider.provider:<10} {runtime:<12} "
            f"{provider.configuration_state:<16} {provider.enforcement_status}"
        )
        print(f"  configuration detail: {provider.configuration_detail}")
        print(f"  covered tools: {', '.join(provider.covered_write_tools)}")
        print(f"  residuals: {'; '.join(provider.residuals)}")

    gate = report.staged_gate
    print("Staged Git claim gate")
    print(f"  repository: {gate.repository or 'not-a-repository'}")
    print(f"  configuration: {gate.configuration_state}")
    print(f"  hook: {gate.hook_state}")
    print(f"  gate: {gate.gate_status}")
    print(f"  enforcement: {gate.enforcement_status}")
    print("Unmediated residuals")
    for residual in report.unmediated_residuals:
        print(f"  - {residual}")
    return 0


def add_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the read-only mutation-governance posture command."""
    parser = subparsers.add_parser(
        "mutation-status",
        help="Report provider hooks, staged gate readiness, and residual write paths.",
    )
    parser.add_argument("--home", default=None, help="Override the provider home root.")
    parser.add_argument("--project", default=None, help="Override the inspected project root.")
    parser.add_argument("--json", action="store_true", help="Emit the stable JSON report.")
    parser.set_defaults(func=_command)
