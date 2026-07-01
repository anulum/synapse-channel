# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — advisory policy-check CLI over a release receipt
"""``synapse policy-check`` — evaluate a release receipt against a policy file.

The command reads a release-receipt JSON (the evidence ``synapse release
--receipt-json`` produces) and a policy file, then prints a deterministic
decision report. It is advisory by default: it always exits ``0`` so it can run
as evidence-producing tooling. Only an enforcement-mode policy with a ``fail``
decision exits non-zero, and only when ``--enforce`` opts into that gate.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from synapse_channel.core.policy_engine import (
    PolicyDecision,
    PolicyError,
    gate_blocks,
    load_policy,
    overall_status,
)
from synapse_channel.core.policy_rules import evaluate_policy
from synapse_channel.core.release_verification import check_receipt_merkle_commitment

_STATUS_GLYPH = {"pass": "✓", "warn": "!", "fail": "✗", "not_applicable": "·"}


def _load_receipt(path: str | Path) -> dict[str, Any]:
    """Load a release-receipt JSON file, raising PolicyError on any problem."""
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise PolicyError(f"receipt file does not exist: {path}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PolicyError(f"invalid receipt JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise PolicyError("receipt JSON must be an object")
    return data


def _print_text_report(report: dict[str, Any]) -> None:
    """Print a human-readable decision report."""
    print(f"policy-check {report['subject']} [{report['mode']}] -> {report['overall']}")
    for decision in report["decisions"]:
        glyph = _STATUS_GLYPH.get(decision["status"], "?")
        print(f"  {glyph} {decision['rule']}: {decision['reason']}")
        if decision["next_action"] and decision["status"] in ("warn", "fail"):
            print(f"      next: {decision['next_action']}")
    if report["blocked"]:
        print("BLOCKED: enforcement policy has at least one failing rule.")


def _merkle_decision(receipt: dict[str, Any], db_path: str, subject: str) -> PolicyDecision:
    """Re-verify the receipt's coordination-log commitment as a policy decision."""
    check = check_receipt_merkle_commitment(receipt, db_path)
    evidence = tuple(
        f"{label}: {value}"
        for label, value in (
            ("recorded root", check.recorded_root),
            ("recomputed root", check.recomputed_root),
        )
        if value
    )
    next_action = ""
    if check.status == "fail":
        next_action = (
            "treat the coordination log (or the receipt) as tampered; audit it with "
            "`synapse merkle root` and `synapse event-query` before trusting the release"
        )
    elif check.status == "not_applicable":
        next_action = "create receipts with `synapse verify-release --merkle-db` to commit the log"
    return PolicyDecision(
        rule="merkle_commitment",
        status=check.status,
        subject=subject,
        reason=check.reason,
        evidence=evidence,
        next_action=next_action,
    )


def _cmd_policy_check(args: argparse.Namespace) -> int:
    """Run ``synapse policy-check`` and return its exit code."""
    try:
        config = load_policy(args.policy)
        receipt = _load_receipt(args.receipt_json)
    except PolicyError as exc:
        print(f"policy-check error: {exc}")
        return 2
    decisions = evaluate_policy(receipt, config, subject=args.task)  # type: ignore[arg-type]
    if args.merkle_db:
        try:
            decisions.append(_merkle_decision(receipt, args.merkle_db, args.task))
        except ValueError as exc:
            print(f"policy-check error: {exc}")
            return 2
    blocked = bool(args.enforce) and gate_blocks(decisions, config)
    report = {
        "subject": args.task or str(receipt.get("task_id", "")) or "<unknown>",
        "mode": config.mode,
        "overall": overall_status(decisions),
        "blocked": blocked,
        "decisions": [decision.as_dict() for decision in decisions],
    }
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        _print_text_report(report)
    return 1 if blocked else 0


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``policy-check`` subparser."""
    parser = subparsers.add_parser(
        "policy-check",
        help="Evaluate a release receipt against a policy file (advisory by default).",
    )
    parser.add_argument("task", help="Task id / subject label for the report.")
    parser.add_argument(
        "--policy", required=True, help="Policy file (.json always; .toml on 3.11+ or with tomli)."
    )
    parser.add_argument(
        "--receipt-json",
        required=True,
        help="Release-receipt JSON file (from 'synapse release --receipt-json').",
    )
    parser.add_argument("--json", action="store_true", help="Emit the decision report as JSON.")
    parser.add_argument(
        "--enforce",
        action="store_true",
        help="Exit non-zero when an enforcement-mode policy has a failing rule.",
    )
    parser.add_argument(
        "--merkle-db",
        default="",
        metavar="FILE",
        help="Hub event store to recompute the receipt's coordination-log commitment "
        "against (written by `synapse verify-release --merkle-db`); adds a "
        "merkle_commitment decision that fails when the committed log prefix changed.",
    )
    parser.set_defaults(func=_cmd_policy_check)
