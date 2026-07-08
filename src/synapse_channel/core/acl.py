# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — deny-by-default ACL model and shadow-mode evaluator
"""A deny-by-default ACL model and a non-blocking shadow evaluator.

An ACL rule grants one permission verb on a structured target pattern inside a
project namespace, with a decision reason for receipts and postmortems. The
evaluator denies by default: an access is allowed only when a rule matches the
permission, the target kind and pattern, and the namespace. Target patterns are
structured (kind plus a glob value), never ad hoc substring checks, so a path
claim, channel id, agent id, or A2A endpoint is compared consistently.

This tranche is shadow-only: :func:`evaluate_access` returns a would-allow or
would-deny decision that callers record as evidence; it never blocks a frame.
See :doc:`../../docs/identity-and-acl` for the design.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

MESSAGE = "message"
CLAIM = "claim"
RELEASE = "release"
BOARD = "board"
METRICS = "metrics"
DASHBOARD = "dashboard"
A2A = "a2a"
NAMESPACE = "namespace"
SANDBOX = "sandbox"
OBSERVE = "observe"

PERMISSIONS = frozenset(
    {MESSAGE, CLAIM, RELEASE, BOARD, METRICS, DASHBOARD, A2A, NAMESPACE, SANDBOX, OBSERVE}
)
"""The auditable permission vocabulary an ACL rule may grant.

``SANDBOX`` grants a sandboxed tool one capability — a filesystem or network target — so
a tool's grants are evaluated through the same deny-by-default path as any other access,
not a parallel one. See :mod:`synapse_channel.core.sandbox_policy`.

``OBSERVE`` grants an identity the right to *receive* directed messages it is not a party
to — a live monitor or auditor. It is only consulted when directed-message routing is on
(``--private-directed-messages``); with routing off every socket sees all traffic and the
grant is moot. Durable consumers (the relay log, the journal, a feeds-backed dashboard,
the federation follower) read the retained feed and never need it.
"""

WOULD_ALLOW = "would_allow"
WOULD_DENY = "would_deny"


class AclError(ValueError):
    """Raised when an ACL policy file is malformed."""


@dataclass(frozen=True)
class Target:
    """A structured access target: a kind and a concrete value.

    Parameters
    ----------
    kind : str
        Target category, such as ``path``, ``channel``, ``agent``, ``endpoint``,
        ``board``, ``metrics``, ``dashboard``, or ``namespace``.
    value : str
        Concrete target value compared against a rule's glob pattern.
    """

    kind: str
    value: str


@dataclass(frozen=True)
class AclRule:
    """One deny-by-default ACL grant.

    Parameters
    ----------
    permission : str
        Permission verb this rule grants (see :data:`PERMISSIONS`).
    target_kind : str
        Target kind the rule applies to.
    target_pattern : str
        Glob pattern matched case-sensitively against a target value.
    namespace : str
        Project namespace the rule is scoped to; blank matches any namespace.
    reason : str
        Human-readable decision reason recorded on a matching decision.
    """

    permission: str
    target_kind: str
    target_pattern: str
    namespace: str = ""
    reason: str = ""

    def matches(self, *, project: str, permission: str, target: Target) -> bool:
        """Return whether this rule grants ``permission`` on ``target``."""
        return (
            self.permission == permission
            and self.target_kind == target.kind
            and (not self.namespace or self.namespace == project)
            and fnmatchcase(target.value, self.target_pattern)
        )


@dataclass(frozen=True)
class AclPolicy:
    """An ordered, deny-by-default set of ACL rules."""

    rules: list[AclRule] = field(default_factory=list)


@dataclass(frozen=True)
class AclDecision:
    """A shadow-mode access decision: what enforcement would have done."""

    decision: str
    subject: str
    permission: str
    target: Target
    reason: str
    matched_rule: int | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return the JSON-serialisable form of the decision."""
        return {
            "decision": self.decision,
            "subject": self.subject,
            "permission": self.permission,
            "target": {"kind": self.target.kind, "value": self.target.value},
            "reason": self.reason,
            "matched_rule": self.matched_rule,
        }


def evaluate_access(
    *, subject: str, project: str, permission: str, target: Target, policy: AclPolicy
) -> AclDecision:
    """Return the deny-by-default shadow decision for one access request.

    The first rule that matches the permission, target kind and pattern, and
    namespace allows the access; if none match, the access would be denied.

    Returns
    -------
    AclDecision
        ``would_allow`` with the matching rule index, or ``would_deny`` when no
        rule grants the access (including an unknown permission verb).
    """
    if permission not in PERMISSIONS:
        return AclDecision(
            WOULD_DENY, subject, permission, target, f"unknown permission '{permission}'"
        )
    for index, rule in enumerate(policy.rules):
        if rule.matches(project=project, permission=permission, target=target):
            return AclDecision(
                WOULD_ALLOW,
                subject,
                permission,
                target,
                rule.reason or f"granted by rule {index}",
                matched_rule=index,
            )
    return AclDecision(
        WOULD_DENY, subject, permission, target, "no rule grants this access (deny by default)"
    )


def load_acl_policy(path: str | Path) -> AclPolicy:
    """Load and validate an ACL policy from a JSON rules file.

    Parameters
    ----------
    path : str or pathlib.Path
        JSON file holding ``{"rules": [{permission, target_kind, target_pattern,
        namespace?, reason?}, ...]}``.

    Returns
    -------
    AclPolicy
        The validated policy.

    Raises
    ------
    AclError
        When the file is missing, not JSON, or a rule is malformed or names an
        unknown permission.
    """
    target = Path(path)
    try:
        raw = target.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise AclError(f"ACL policy file does not exist: {target}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AclError(f"invalid ACL JSON: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("rules"), list):
        raise AclError("ACL policy must be an object with a 'rules' list")
    return AclPolicy(rules=[_parse_rule(entry, index) for index, entry in enumerate(data["rules"])])


def _parse_rule(entry: object, index: int) -> AclRule:
    """Parse one ACL rule object, validating its permission and target."""
    if not isinstance(entry, dict):
        raise AclError(f"ACL rule {index} must be an object")
    permission = str(entry.get("permission", "")).strip()
    if permission not in PERMISSIONS:
        raise AclError(f"ACL rule {index} has unknown permission '{permission}'")
    target_kind = str(entry.get("target_kind", "")).strip()
    target_pattern = str(entry.get("target_pattern", "")).strip()
    if not target_kind or not target_pattern:
        raise AclError(f"ACL rule {index} needs non-empty target_kind and target_pattern")
    return AclRule(
        permission=permission,
        target_kind=target_kind,
        target_pattern=target_pattern,
        namespace=str(entry.get("namespace", "")).strip(),
        reason=str(entry.get("reason", "")).strip(),
    )
