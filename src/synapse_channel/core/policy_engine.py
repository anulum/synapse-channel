# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — advisory policy engine over release-receipt evidence
"""Advisory policy decisions over a release receipt's evidence.

The policy engine answers one narrow question: is a task ready to proceed under
the operator's declared rules? It reads an existing release receipt (the
evidence Synapse already produces) and a small policy file, then returns a
deterministic, serialisable list of ``pass`` / ``warn`` / ``fail`` /
``not_applicable`` decisions, each with the exact evidence it relied on and a
concrete next action.

This first tranche is advisory and read-only: it produces evidence, it does not
merge code, gate releases, or call external services. Enforcement mode only
changes the overall exit decision (a configured ``fail`` is a hard stop); the
per-rule statuses are the same regardless of mode. The engine is pure — it takes
a receipt and a config and returns decisions — so the CLI owns all I/O and the
rules stay unit-testable. See ``docs/policy-engine`` for the design.
"""

from __future__ import annotations

import importlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PASS = "pass"
WARN = "warn"
FAIL = "fail"
NOT_APPLICABLE = "not_applicable"

_STATUS_SEVERITY = {NOT_APPLICABLE: 0, PASS: 1, WARN: 2, FAIL: 3}
"""Ordering used to fold many decisions into the single worst status."""

ADVISORY = "advisory"
ENFORCEMENT = "enforcement"
_MODES = frozenset({ADVISORY, ENFORCEMENT})


@dataclass(frozen=True)
class PolicyDecision:
    """One deterministic, serialisable policy decision.

    Parameters
    ----------
    rule : str
        Rule family that produced the decision.
    status : str
        One of ``pass``, ``warn``, ``fail``, ``not_applicable``.
    subject : str
        Task id (or other subject) the decision is about.
    reason : str
        Human-readable explanation of the decision.
    evidence : tuple[str, ...]
        Evidence references the decision relied on.
    next_action : str
        Concrete action that would move a ``warn``/``fail`` toward ``pass``.
    """

    rule: str
    status: str
    subject: str
    reason: str
    evidence: tuple[str, ...] = ()
    next_action: str = ""

    def as_dict(self) -> dict[str, Any]:
        """Return the JSON-serialisable form of the decision."""
        return {
            "rule": self.rule,
            "status": self.status,
            "subject": self.subject,
            "reason": self.reason,
            "evidence": list(self.evidence),
            "next_action": self.next_action,
        }


@dataclass(frozen=True)
class PolicyConfig:
    """A validated policy: a schema version, a mode, and per-rule settings."""

    version: int
    mode: str
    rules: dict[str, Any] = field(default_factory=dict)


class PolicyError(ValueError):
    """Raised when a policy file is malformed or unsupported."""


def load_policy(path: str | Path) -> PolicyConfig:
    """Load and validate a policy file (``.json``, or ``.toml`` where supported).

    Parameters
    ----------
    path : str or pathlib.Path
        Policy file path. ``.json`` is always supported (standard library);
        ``.toml`` is supported on Python 3.11+ (``tomllib``) or when ``tomli`` is
        installed.

    Returns
    -------
    PolicyConfig
        The validated configuration.

    Raises
    ------
    PolicyError
        When the file cannot be parsed, the version is unsupported, or the mode
        is not ``advisory``/``enforcement``.
    """
    target = Path(path)
    try:
        raw = target.read_bytes()
    except FileNotFoundError as exc:
        raise PolicyError(f"policy file does not exist: {target}") from exc
    data = _parse_policy_bytes(raw, target.suffix.lower())
    if not isinstance(data, dict):
        raise PolicyError("policy file must contain a mapping at the top level")
    version = data.get("version", 1)
    if version != 1:
        raise PolicyError(f"unsupported policy version: {version!r} (expected 1)")
    mode = str(data.get("mode", ADVISORY)).strip().lower()
    if mode not in _MODES:
        raise PolicyError(f"policy mode must be one of {sorted(_MODES)}, got {mode!r}")
    rules = data.get("rules", {})
    if not isinstance(rules, dict):
        raise PolicyError("policy 'rules' must be a mapping")
    return PolicyConfig(version=1, mode=mode, rules=rules)


def _parse_policy_bytes(raw: bytes, suffix: str) -> Any:
    """Parse policy bytes as JSON or TOML, choosing by file suffix."""
    if suffix == ".json":
        try:
            return json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise PolicyError(f"invalid JSON policy: {exc}") from exc
    if suffix == ".toml":
        loader = _toml_loader()
        try:
            return loader(raw)
        except Exception as exc:  # noqa: BLE001 - surface any TOML parse error uniformly
            raise PolicyError(f"invalid TOML policy: {exc}") from exc
    raise PolicyError(f"unsupported policy file type '{suffix}' (use .json or .toml)")


def _toml_loader() -> Any:
    """Return a ``loads(bytes) -> dict`` TOML loader, or raise PolicyError.

    Prefers the standard-library ``tomllib`` (Python 3.11+) and falls back to the
    ``tomli`` backport; raises when neither is importable so the operator gets a
    clear hint to use a ``.json`` policy instead.
    """
    for module_name in ("tomllib", "tomli"):
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError:
            continue
        return lambda raw, loader=module: loader.loads(raw.decode("utf-8"))
    raise PolicyError(
        "TOML policy files need Python 3.11+ or the 'tomli' package; "
        "use a .json policy file instead"
    )


def overall_status(decisions: list[PolicyDecision]) -> str:
    """Return the single worst status across ``decisions`` (``pass`` when empty)."""
    worst = PASS
    for decision in decisions:
        if _STATUS_SEVERITY.get(decision.status, 0) > _STATUS_SEVERITY[worst]:
            worst = decision.status
    return worst


def gate_blocks(decisions: list[PolicyDecision], config: PolicyConfig) -> bool:
    """Return whether the decisions block under the config's mode.

    Advisory mode never blocks (it only produces evidence); enforcement mode
    blocks when any decision is ``fail``.
    """
    if config.mode != ENFORCEMENT:
        return False
    return any(decision.status == FAIL for decision in decisions)
