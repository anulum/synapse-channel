# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — deterministic read-only setup planning
"""Derive an immutable, non-executable plan from one exact inspection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from synapse_channel.setup_contract import (
    SETUP_SCHEMA_VERSION,
    SetupCheckStatus,
    SetupEffectAuthority,
    SetupEffectDisruption,
    SetupPlan,
    SetupPlannedEffect,
    document_digest,
    validated_setup_target,
)
from synapse_channel.setup_profiles import SetupProfile, build_setup_spec

_CHECK_STATUSES = frozenset({"pass", "warn", "fail", "unavailable"})


@dataclass(frozen=True, slots=True)
class EffectRule:
    """One package-owned mapping from a failed check to a future effect."""

    check_id: str
    effect_id: str
    authority: SetupEffectAuthority
    disruption: SetupEffectDisruption
    reversible: bool


_EFFECT_RULES = (
    EffectRule(
        "package",
        "install_synapse_package",
        "operator_confirmation",
        "environment_change",
        True,
    ),
    EffectRule(
        "python",
        "select_supported_python",
        "operator_confirmation",
        "environment_change",
        True,
    ),
    EffectRule(
        "platform",
        "select_supported_platform",
        "unsupported",
        "host_migration",
        False,
    ),
    EffectRule(
        "executable",
        "expose_synapse_entrypoint",
        "operator_confirmation",
        "environment_change",
        True,
    ),
    EffectRule(
        "identity",
        "configure_coordination_identity",
        "operator_confirmation",
        "configuration_change",
        True,
    ),
    EffectRule(
        "hub",
        "establish_local_loopback_hub",
        "operator_restart_authority",
        "service_start",
        True,
    ),
    EffectRule(
        "waiter",
        "establish_identity_waiter",
        "operator_confirmation",
        "service_start",
        True,
    ),
)


def _validated_statuses(
    profile: SetupProfile, inspection: dict[str, object]
) -> dict[str, SetupCheckStatus]:
    """Return exact profile check statuses or reject an ambiguous inspection."""
    if (
        inspection.get("schema_version") != SETUP_SCHEMA_VERSION
        or inspection.get("document_kind") != "inspection"
        or inspection.get("profile") != profile.profile_id
        or inspection.get("profile_version") != profile.version
        or inspection.get("read_only") is not True
    ):
        raise ValueError("inspection does not match the selected setup profile")
    checks = inspection.get("checks")
    if not isinstance(checks, list):
        raise ValueError("inspection checks must be a list")

    expected = {item.requirement_id: item.required for item in profile.requirements}
    statuses: dict[str, SetupCheckStatus] = {}
    for item in checks:
        if not isinstance(item, dict):
            raise ValueError("inspection check must be an object")
        check_id = item.get("id")
        status = item.get("status")
        required = item.get("required")
        if (
            not isinstance(check_id, str)
            or check_id not in expected
            or check_id in statuses
            or status not in _CHECK_STATUSES
            or not isinstance(required, bool)
            or required is not expected[check_id]
        ):
            raise ValueError("inspection checks do not match the selected setup profile")
        statuses[check_id] = cast(SetupCheckStatus, status)
    if set(statuses) != set(expected):
        raise ValueError("inspection is missing required profile checks")

    required_ready = all(
        statuses[requirement.requirement_id] == "pass"
        for requirement in profile.requirements
        if requirement.required
    )
    if inspection.get("ready") is not required_ready:
        raise ValueError("inspection readiness contradicts its required checks")
    return statuses


def build_setup_plan(profile: SetupProfile, inspection: dict[str, object]) -> dict[str, object]:
    """Build a deterministic, digest-bound plan that cannot be applied."""
    statuses = _validated_statuses(profile, inspection)
    target = validated_setup_target(inspection.get("target"))
    effects: list[SetupPlannedEffect] = []
    for rule in _EFFECT_RULES:
        status = statuses[rule.check_id]
        if status == "pass":
            continue
        blocked = status == "unavailable" or rule.authority == "unsupported"
        effects.append(
            SetupPlannedEffect(
                effect_id=rule.effect_id,
                trigger_check=rule.check_id,
                observed_status=status,
                disposition="blocked" if blocked else "planned",
                authority=rule.authority,
                disruption=rule.disruption,
                reversible=rule.reversible,
                verification_check=rule.check_id,
            )
        )

    warnings = ["apply_not_available"]
    if any(effect.disposition == "blocked" for effect in effects):
        warnings.append("manual_remediation_required")
    plan = SetupPlan(
        profile=profile.profile_id,
        profile_version=profile.version,
        inspection_digest=document_digest(inspection),
        profile_digest=document_digest(build_setup_spec(profile)),
        target=target,
        ready=bool(inspection["ready"]),
        effects=tuple(effects),
        warnings=tuple(warnings),
    )
    return plan.as_dict()
