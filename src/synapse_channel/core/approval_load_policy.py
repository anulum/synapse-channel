# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the declared approval-load policy: per-approver and per-role thresholds
"""Load and canonicalise the hub's opt-in approval-load policy.

The policy says how fast approval requests may reach an approver, and when a
decision or a batch deserves an auditor's attention. It does not say who may
approve; that stays in the role grants. Each rule names four thresholds:

``request_rate`` and ``window_seconds``
    At most ``request_rate`` admitted requests addressed to the rule's subject in
    any trailing ``window_seconds``.
``min_review_seconds``
    A decision made sooner than this after its request is flagged for audit.
``max_batch``
    A batch holding more requests than this is flagged for audit.

Rules are keyed either by an approver role (the ``roles`` section) or by one
approver identity (the ``approvers`` section), both ``<project>/<name>``
addresses. The file is JSON and must be owner-only, as every authorisation
input of the hub is::

    {
      "schema": "synapse-approval-load-policy.v1",
      "roles": {"P/person": {"request_rate": 20, "window_seconds": 3600,
                             "min_review_seconds": 60, "max_batch": 10}},
      "approvers": {}
    }

A malformed file refuses hub start; it never degrades to "no policy".
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from synapse_channel.core.errors import SynapseError
from synapse_channel.core.secure_path import SecurePathError, assert_owner_only_file_path

APPROVAL_LOAD_POLICY_SCHEMA = "synapse-approval-load-policy.v1"
"""The only accepted ``schema`` value of a policy file."""

MAX_POLICY_BYTES = 65_536
"""Largest policy file read; a larger one is refused rather than truncated."""

_RULE_FIELDS = ("request_rate", "window_seconds", "min_review_seconds", "max_batch")
"""The four thresholds every rule must declare, and nothing else."""


class ApprovalLoadPolicyError(SynapseError, ValueError):
    """Raised when an approval-load policy file or rule is malformed."""

    code = "approval_load_policy"


@dataclass(frozen=True)
class ApprovalLoadRule:
    """The thresholds one rule declares for its role or approver.

    Attributes
    ----------
    request_rate : int
        Admitted requests allowed per window; at least 1.
    window_seconds : float
        Length of the trailing window; positive and finite.
    min_review_seconds : float
        Decision time below which a decision is flagged; non-negative and finite.
    max_batch : int
        Requests per batch above which the batch is flagged; at least 1.
    """

    request_rate: int
    window_seconds: float
    min_review_seconds: float
    max_batch: int

    def to_json_obj(self) -> dict[str, int | float]:
        """Return the rule's canonical JSON form."""
        return {
            "request_rate": self.request_rate,
            "window_seconds": self.window_seconds,
            "min_review_seconds": self.min_review_seconds,
            "max_batch": self.max_batch,
        }


@dataclass(frozen=True)
class ApprovalLoadPolicy:
    """The validated policy: rules keyed by approver role and by approver identity.

    Attributes
    ----------
    roles : Mapping[str, ApprovalLoadRule]
        Rules for ``<project>/<role>`` addresses.
    approvers : Mapping[str, ApprovalLoadRule]
        Rules for ``<project>/<name>`` identities.
    """

    roles: Mapping[str, ApprovalLoadRule]
    approvers: Mapping[str, ApprovalLoadRule]

    @property
    def projects(self) -> frozenset[str]:
        """Return the projects any rule names; their requests must be addressed."""
        keys = (*self.roles, *self.approvers)
        return frozenset(key.partition("/")[0] for key in keys)

    @property
    def longest_window_seconds(self) -> float:
        """Return the longest declared window, the span a restart must recount."""
        rules = (*self.roles.values(), *self.approvers.values())
        return max(rule.window_seconds for rule in rules)

    def to_json_obj(self) -> dict[str, Any]:
        """Return the canonical JSON form, with sections and keys sorted."""
        return {
            "schema": APPROVAL_LOAD_POLICY_SCHEMA,
            "roles": {key: self.roles[key].to_json_obj() for key in sorted(self.roles)},
            "approvers": {key: self.approvers[key].to_json_obj() for key in sorted(self.approvers)},
        }

    @property
    def sha256(self) -> str:
        """Return the SHA-256 of the canonical JSON form, the policy's identity."""
        canonical = json.dumps(self.to_json_obj(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _address(value: object, section: str) -> str:
    """Return a validated ``<project>/<name>`` key or raise."""
    key = str(value)
    project, separator, name = key.partition("/")
    if not (separator and project.strip() and name.strip()) or key != key.strip():
        raise ApprovalLoadPolicyError(f"{section} key must be '<project>/<name>', got {key!r}")
    return key


def _positive_int(value: object, label: str) -> int:
    """Return an integer of at least 1 or raise; a boolean is not an integer here."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ApprovalLoadPolicyError(f"{label} must be an integer of at least 1")
    return value


def _seconds(value: object, label: str, *, positive: bool) -> float:
    """Return a finite number of seconds, positive or non-negative, or raise."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ApprovalLoadPolicyError(f"{label} must be a number of seconds")
    number = float(value)
    if not math.isfinite(number) or number < 0 or (positive and number == 0):
        bound = "positive" if positive else "non-negative"
        raise ApprovalLoadPolicyError(f"{label} must be a finite, {bound} number of seconds")
    return number


def _rule(value: object, label: str) -> ApprovalLoadRule:
    """Return one validated rule, refusing a missing or an unknown field."""
    if not isinstance(value, Mapping):
        raise ApprovalLoadPolicyError(f"{label} must be a mapping of the four thresholds")
    if set(value) != set(_RULE_FIELDS):
        raise ApprovalLoadPolicyError(f"{label} must declare exactly {', '.join(_RULE_FIELDS)}")
    return ApprovalLoadRule(
        request_rate=_positive_int(value["request_rate"], f"{label}.request_rate"),
        window_seconds=_seconds(value["window_seconds"], f"{label}.window_seconds", positive=True),
        min_review_seconds=_seconds(
            value["min_review_seconds"], f"{label}.min_review_seconds", positive=False
        ),
        max_batch=_positive_int(value["max_batch"], f"{label}.max_batch"),
    )


def _section(data: Mapping[str, Any], section: str) -> dict[str, ApprovalLoadRule]:
    """Return one validated section; an absent section is empty."""
    raw = data.get(section, {})
    if not isinstance(raw, Mapping):
        raise ApprovalLoadPolicyError(f"{section} must be a mapping")
    rules: dict[str, ApprovalLoadRule] = {}
    for key, value in raw.items():
        address = _address(key, section)
        rules[address] = _rule(value, f"{section}[{address!r}]")
    return rules


def parse_approval_load_policy(data: object) -> ApprovalLoadPolicy:
    """Validate a decoded policy document.

    Parameters
    ----------
    data : object
        The decoded JSON document.

    Returns
    -------
    ApprovalLoadPolicy
        The validated policy.

    Raises
    ------
    ApprovalLoadPolicyError
        When the schema is wrong, a section or rule is malformed, an unknown
        top-level key is present, or no rule is declared at all.
    """
    if not isinstance(data, Mapping):
        raise ApprovalLoadPolicyError("approval-load policy must be a JSON object")
    if data.get("schema") != APPROVAL_LOAD_POLICY_SCHEMA:
        raise ApprovalLoadPolicyError(
            f"approval-load policy schema must be {APPROVAL_LOAD_POLICY_SCHEMA!r}"
        )
    unknown = set(data) - {"schema", "roles", "approvers"}
    if unknown:
        raise ApprovalLoadPolicyError(f"approval-load policy has unknown keys: {sorted(unknown)}")
    policy = ApprovalLoadPolicy(
        roles=_section(data, "roles"), approvers=_section(data, "approvers")
    )
    if not policy.roles and not policy.approvers:
        raise ApprovalLoadPolicyError("approval-load policy declares no rule")
    return policy


def load_approval_load_policy(path: str | Path) -> ApprovalLoadPolicy:
    """Read and validate an owner-only policy file without following a final link.

    Parameters
    ----------
    path : str or pathlib.Path
        The policy file; ``~`` is expanded.

    Returns
    -------
    ApprovalLoadPolicy
        The validated policy.

    Raises
    ------
    ApprovalLoadPolicyError
        When the file is missing, not owner-only, a link, too large, changed
        while it was opened, not UTF-8 JSON, or not a valid policy.
    """
    file = Path(path).expanduser()
    try:
        assert_owner_only_file_path(file, purpose="approval-load policy", require_single_link=True)
        before = os.lstat(file)
        descriptor = os.open(file, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except (SecurePathError, OSError) as exc:
        raise ApprovalLoadPolicyError(f"approval-load policy {file}: {exc}") from exc
    try:
        after = os.fstat(descriptor)
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino) or not stat.S_ISREG(
            after.st_mode
        ):
            raise ApprovalLoadPolicyError(
                f"approval-load policy {file} changed while it was opened"
            )
        chunks: list[bytes] = []
        total = 0
        while total <= MAX_POLICY_BYTES:
            chunk = os.read(descriptor, MAX_POLICY_BYTES + 1 - total)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        raw = b"".join(chunks)
    finally:
        os.close(descriptor)
    if len(raw) > MAX_POLICY_BYTES:
        raise ApprovalLoadPolicyError(
            f"approval-load policy {file} exceeds {MAX_POLICY_BYTES} bytes"
        )
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApprovalLoadPolicyError(
            f"approval-load policy {file} is not UTF-8 JSON: {exc}"
        ) from exc
    return parse_approval_load_policy(data)
