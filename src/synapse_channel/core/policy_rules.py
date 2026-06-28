# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — policy rule families evaluated over a release receipt
"""The policy rule families that turn receipt evidence into decisions.

Each rule is a pure function ``(receipt, rule_cfg, subject) -> PolicyDecision``
registered in :data:`RULE_EVALUATORS`. The engine looks rules up by name, so
adding a rule family is one function plus one table entry. Every rule explains
its decision with the receipt fields it relied on, and an unconfigured rule
simply does not run — absent configuration never silently fails a release.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from synapse_channel.core.policy_engine import (
    FAIL,
    NOT_APPLICABLE,
    PASS,
    WARN,
    PolicyConfig,
    PolicyDecision,
)
from synapse_channel.core.receipts import ReleaseReceipt

RuleEvaluator = Callable[[ReleaseReceipt, dict[str, Any], str], PolicyDecision]

_PYTHON_SUFFIXES = (".py", ".pyi")


def _items(receipt: ReleaseReceipt, key: str) -> list[str]:
    """Return a receipt list field as a list of strings, empty when absent."""
    value = receipt.get(key, [])
    if not isinstance(value, Sequence) or isinstance(value, str):
        return []
    return [str(item) for item in value]


def _command_satisfied(command: str, evidence: list[str], known_failures: list[str]) -> bool:
    """Return whether a declared command appears in evidence or is acknowledged."""
    return any(command in entry for entry in evidence) or any(
        command in entry for entry in known_failures
    )


def required_tests(
    receipt: ReleaseReceipt, rule_cfg: dict[str, Any], subject: str
) -> PolicyDecision:
    """Every declared test command must appear in evidence or a known failure."""
    commands = [str(command) for command in rule_cfg.get("commands", [])]
    if not commands:
        return PolicyDecision(
            "required_tests", NOT_APPLICABLE, subject, "no required test commands configured"
        )
    evidence = _items(receipt, "evidence")
    known = _items(receipt, "known_failures")
    missing = [command for command in commands if not _command_satisfied(command, evidence, known)]
    if missing:
        return PolicyDecision(
            "required_tests",
            FAIL,
            subject,
            f"required test command(s) absent from evidence: {', '.join(missing)}",
            evidence=(f"receipt:{subject}",),
            next_action="run the listed command(s) and attach their result as release evidence",
        )
    return PolicyDecision(
        "required_tests",
        PASS,
        subject,
        f"all {len(commands)} required test command(s) present in evidence",
        evidence=(f"receipt:{subject}",),
    )


def strict_type_checking(
    receipt: ReleaseReceipt, rule_cfg: dict[str, Any], subject: str
) -> PolicyDecision:
    """Require a typecheck command behind code changes that touch Python files."""
    python_cfg = rule_cfg.get("python", rule_cfg)
    command = str(python_cfg.get("command", "")).split("{")[0].strip()
    if not command:
        return PolicyDecision(
            "strict_type_checking", NOT_APPLICABLE, subject, "no typecheck command configured"
        )
    changed = _items(receipt, "changed_files")
    touches_python = any(path.endswith(_PYTHON_SUFFIXES) for path in changed)
    if not touches_python:
        return PolicyDecision(
            "strict_type_checking", NOT_APPLICABLE, subject, "no changed Python files to typecheck"
        )
    if any(command in entry for entry in _items(receipt, "evidence")):
        return PolicyDecision(
            "strict_type_checking",
            PASS,
            subject,
            "typecheck evidence present for changed Python files",
            evidence=(f"receipt:{subject}",),
        )
    return PolicyDecision(
        "strict_type_checking",
        FAIL,
        subject,
        "changed Python files but no typecheck evidence",
        next_action=f"run '{command}' on the changed files and attach the result",
    )


def owner_approval(
    receipt: ReleaseReceipt, rule_cfg: dict[str, Any], subject: str
) -> PolicyDecision:
    """Require each configured owner to appear in the receipt's approvals."""
    required = [str(owner) for owner in rule_cfg.get("owners", [])]
    if not required:
        # CODEOWNERS-sourced approval mapping is a later tranche; an explicit
        # owners list is the supported first-tranche form.
        return PolicyDecision(
            "owner_approval", NOT_APPLICABLE, subject, "no explicit required owners configured"
        )
    approvals = _items(receipt, "approvals")
    missing = [owner for owner in required if not any(owner in entry for entry in approvals)]
    if missing:
        return PolicyDecision(
            "owner_approval",
            FAIL,
            subject,
            f"missing approval from required owner(s): {', '.join(missing)}",
            next_action="obtain and record the listed owner approval(s) on the release receipt",
        )
    return PolicyDecision(
        "owner_approval",
        PASS,
        subject,
        f"all {len(required)} required owner approval(s) present",
        evidence=(f"receipt:{subject}",),
    )


def evidence_freshness(
    receipt: ReleaseReceipt, rule_cfg: dict[str, Any], subject: str
) -> PolicyDecision:
    """Check the receipt's newest evidence is within the configured age."""
    max_age = rule_cfg.get("max_age_seconds")
    if max_age is None:
        return PolicyDecision(
            "evidence_freshness", NOT_APPLICABLE, subject, "no max_age_seconds configured"
        )
    freshness = receipt.get("freshness_seconds")
    if freshness is None:
        return PolicyDecision(
            "evidence_freshness",
            WARN,
            subject,
            "receipt does not record evidence freshness",
            next_action="attach freshness_seconds when releasing so age can be checked",
        )
    if float(freshness) > float(max_age):
        return PolicyDecision(
            "evidence_freshness",
            WARN,
            subject,
            f"evidence age {float(freshness):.0f}s exceeds limit {float(max_age):.0f}s",
            evidence=(f"receipt:{subject}",),
            next_action="rerun the checks and attach fresh evidence",
        )
    return PolicyDecision(
        "evidence_freshness",
        PASS,
        subject,
        f"evidence age {float(freshness):.0f}s within limit {float(max_age):.0f}s",
        evidence=(f"receipt:{subject}",),
    )


def no_merge_without_receipt(
    receipt: ReleaseReceipt, rule_cfg: dict[str, Any], subject: str
) -> PolicyDecision:
    """Require a receipt to carry at least one piece of evidence."""
    if not rule_cfg.get("required", False):
        return PolicyDecision(
            "no_merge_without_receipt", NOT_APPLICABLE, subject, "receipt not required by policy"
        )
    if _items(receipt, "evidence"):
        return PolicyDecision(
            "no_merge_without_receipt",
            PASS,
            subject,
            "release receipt carries evidence",
            evidence=(f"receipt:{subject}",),
        )
    return PolicyDecision(
        "no_merge_without_receipt",
        FAIL,
        subject,
        "release receipt has no evidence entries",
        next_action="attach release evidence before merging",
    )


def known_failure_acknowledgement(
    receipt: ReleaseReceipt, rule_cfg: dict[str, Any], subject: str
) -> PolicyDecision:
    """Each declared known failure must name a non-trivial reason."""
    del rule_cfg
    known = _items(receipt, "known_failures")
    if not known:
        return PolicyDecision(
            "known_failure_acknowledgement", NOT_APPLICABLE, subject, "no known failures declared"
        )
    vague = [failure for failure in known if len(failure.strip()) < 8]
    if vague:
        return PolicyDecision(
            "known_failure_acknowledgement",
            WARN,
            subject,
            f"{len(vague)} known failure(s) lack a described reason",
            next_action="state each known failure's scope, reason, owner, and follow-up",
        )
    return PolicyDecision(
        "known_failure_acknowledgement",
        PASS,
        subject,
        f"all {len(known)} known failure(s) described",
        evidence=(f"receipt:{subject}",),
    )


def generated_artifact_parity(
    receipt: ReleaseReceipt, rule_cfg: dict[str, Any], subject: str
) -> PolicyDecision:
    """Match changed sources to an updated or justified generated set."""
    del rule_cfg
    changed = _items(receipt, "changed_files")
    if not changed:
        return PolicyDecision(
            "generated_artifact_parity", NOT_APPLICABLE, subject, "no changed files in receipt"
        )
    if _items(receipt, "generated_artifacts") or _items(receipt, "known_failures"):
        return PolicyDecision(
            "generated_artifact_parity",
            PASS,
            subject,
            "changed files have a declared generated set or a known-failure justification",
            evidence=(f"receipt:{subject}",),
        )
    return PolicyDecision(
        "generated_artifact_parity",
        WARN,
        subject,
        "changed files but no generated artifacts or justification declared",
        next_action="declare regenerated artifacts, or justify their absence as a known failure",
    )


RULE_EVALUATORS: dict[str, RuleEvaluator] = {
    "required_tests": required_tests,
    "strict_type_checking": strict_type_checking,
    "owner_approval": owner_approval,
    "evidence_freshness": evidence_freshness,
    "no_merge_without_receipt": no_merge_without_receipt,
    "known_failure_acknowledgement": known_failure_acknowledgement,
    "generated_artifact_parity": generated_artifact_parity,
}
"""Rule family name to evaluator; the engine dispatches configured rules here."""


def evaluate_policy(
    receipt: ReleaseReceipt, config: PolicyConfig, *, subject: str = ""
) -> list[PolicyDecision]:
    """Evaluate every configured rule against a receipt, in configuration order.

    Parameters
    ----------
    receipt : ReleaseReceipt
        The release receipt whose evidence is judged.
    config : PolicyConfig
        The validated policy; only the rules it configures are evaluated.
    subject : str, optional
        Subject label for the decisions; defaults to the receipt's task id.

    Returns
    -------
    list[PolicyDecision]
        One decision per configured rule, in order. An unknown rule name yields a
        ``warn`` rather than silently passing.
    """
    task_subject = subject or str(receipt.get("task_id", "")) or "<unknown>"
    decisions: list[PolicyDecision] = []
    for rule_name, rule_cfg in config.rules.items():
        cfg = rule_cfg if isinstance(rule_cfg, dict) else {}
        evaluator = RULE_EVALUATORS.get(rule_name)
        if evaluator is None:
            decisions.append(
                PolicyDecision(
                    rule_name,
                    WARN,
                    task_subject,
                    f"unknown policy rule '{rule_name}'",
                    next_action="remove or correct the rule name in the policy file",
                )
            )
            continue
        decisions.append(evaluator(receipt, cfg, task_subject))
    return decisions
