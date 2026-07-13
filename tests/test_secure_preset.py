# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — secure umbrella profile policy tests
"""Policy tests for the ``synapse hub --secure`` umbrella profile."""

from __future__ import annotations

import argparse

import pytest

from synapse_channel.core.errors import SynapseError
from synapse_channel.core.secure import (
    SECURE_AGENT_BURST,
    SECURE_AGENT_RATE,
    SECURE_HOST_BURST,
    SECURE_HOST_RATE,
    SECURE_MAX_CONNECTIONS_PER_HOST,
    SecureHubReport,
    SecureModeError,
    apply_secure_hub_profile,
)


def _complete_args(**overrides: object) -> argparse.Namespace:
    """Return a namespace with every required secure-mode input present."""
    base: dict[str, object] = {
        "secure": True,
        "token": "connect-secret",
        "db": "hub.db",
        "identity_trust": "identity.json",
        "role_grants": "roles.json",
        "message_auth_key": ["k1:secret:agent"],
        "acl_policy": "acl.json",
        "tls_certfile": "cert.pem",
        "tls_keyfile": "key.pem",
        "metrics": False,
        "metrics_token": None,
        "require_message_auth": False,
        "require_acl": False,
        "require_identity_binding": False,
        "require_role_claim": False,
        "private_directed_messages": False,
        "metrics_query_token_ok": True,
        "insecure_off_loopback": True,
        "rate": 0.0,
        "burst": 20.0,
        "host_rate": 0.0,
        "host_burst": 40.0,
        "max_connections_per_host": 0,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def test_disabled_preset_is_a_true_no_op() -> None:
    args = argparse.Namespace(secure=False)
    assert apply_secure_hub_profile(args) is None


def test_complete_preset_forces_every_subordinate_gate() -> None:
    args = _complete_args()

    report = apply_secure_hub_profile(args)

    assert isinstance(report, SecureHubReport)
    assert args.paranoid is True
    assert args.team_secure is True
    assert args.require_message_auth is True
    assert args.require_acl is True
    assert args.require_identity_binding is True
    assert args.require_role_claim is True
    assert args.private_directed_messages is True
    assert args.metrics_query_token_ok is False
    assert args.insecure_off_loopback is False


def test_disabled_rate_values_receive_exact_preset_defaults() -> None:
    args = _complete_args(rate=0.0, host_rate=0.0, max_connections_per_host=0)

    apply_secure_hub_profile(args)

    assert args.rate == SECURE_AGENT_RATE
    assert args.burst == 20.0
    assert args.host_rate == SECURE_HOST_RATE
    assert args.host_burst == 100.0
    assert args.max_connections_per_host == SECURE_MAX_CONNECTIONS_PER_HOST


def test_stricter_positive_limits_survive() -> None:
    args = _complete_args(rate=25.0, host_rate=200.0, max_connections_per_host=4)

    apply_secure_hub_profile(args)

    assert args.rate == 25.0
    assert args.host_rate == 200.0
    assert args.max_connections_per_host == 4


@pytest.mark.parametrize(
    ("attr", "value", "flag"),
    [
        ("rate", 250.0, "--rate"),
        ("host_rate", 900.0, "--host-rate"),
        ("max_connections_per_host", 40, "--max-connections-per-host"),
    ],
)
def test_a_positive_limit_above_the_ceiling_is_refused(attr: str, value: float, flag: str) -> None:
    args = _complete_args(**{attr: value})

    with pytest.raises(SecureModeError, match=flag):
        apply_secure_hub_profile(args)


@pytest.mark.parametrize(
    ("overrides", "flag"),
    [
        # Exact audit reproducers: these were previously accepted as operator-stricter.
        ({"rate": 25.0, "burst": 1_000_000.0}, "--burst"),
        ({"host_rate": 200.0, "host_burst": 2_000_000.0}, "--host-burst"),
        # The boundary itself: one over the ceiling is refused, the ceiling is kept.
        ({"rate": 25.0, "burst": SECURE_AGENT_BURST + 1.0}, "--burst"),
        ({"host_rate": 200.0, "host_burst": SECURE_HOST_BURST + 1.0}, "--host-burst"),
    ],
)
def test_an_operator_burst_above_the_ceiling_is_refused(
    overrides: dict[str, float], flag: str
) -> None:
    """A stricter rate must not smuggle in an unbounded burst allowance."""
    args = _complete_args(**overrides)

    with pytest.raises(SecureModeError, match=flag):
        apply_secure_hub_profile(args)


def test_an_operator_burst_at_the_ceiling_is_preserved() -> None:
    args = _complete_args(
        rate=25.0,
        burst=SECURE_AGENT_BURST,
        host_rate=200.0,
        host_burst=SECURE_HOST_BURST,
    )

    report = apply_secure_hub_profile(args)

    assert args.burst == SECURE_AGENT_BURST
    assert args.host_burst == SECURE_HOST_BURST
    assert report is not None


@pytest.mark.parametrize(
    ("overrides", "flag"),
    [
        ({"rate": float("nan")}, "--rate"),
        ({"rate": float("inf")}, "--rate"),
        ({"host_rate": float("nan")}, "--host-rate"),
        ({"host_rate": float("inf")}, "--host-rate"),
        ({"rate": 25.0, "burst": float("nan")}, "--burst"),
        ({"rate": 25.0, "burst": float("inf")}, "--burst"),
        ({"host_rate": 200.0, "host_burst": float("nan")}, "--host-burst"),
        ({"host_rate": 200.0, "host_burst": float("inf")}, "--host-burst"),
    ],
)
def test_non_finite_rate_or_burst_fails_closed(overrides: dict[str, float], flag: str) -> None:
    """``nan`` compares false against every ceiling and would construct no limiter.

    The audit reproduced ``rate=nan`` reported as operator-stricter while the runtime
    then built no limiter at all (``nan > 0`` is false); every non-finite value must
    fail closed under the preset instead.
    """
    args = _complete_args(**overrides)

    with pytest.raises(SecureModeError, match=flag):
        apply_secure_hub_profile(args)


def test_non_numeric_connection_cap_fails_closed() -> None:
    args = _complete_args(max_connections_per_host=float("nan"))

    with pytest.raises(SecureModeError, match="--max-connections-per-host"):
        apply_secure_hub_profile(args)


def test_disabled_burst_beside_a_stricter_rate_gets_the_preset_default() -> None:
    """An operator-stricter rate with a zeroed burst must never yield a burstless bucket."""
    args = _complete_args(rate=25.0, burst=0.0, host_rate=200.0, host_burst=0.0)

    report = apply_secure_hub_profile(args)

    assert args.burst == SECURE_AGENT_BURST
    assert args.host_burst == SECURE_HOST_BURST
    assert report is not None
    assert "per-agent 25/s burst 20 (operator-stricter)" in report.effective_limits[0]


def test_report_reflects_the_exact_enforced_burst() -> None:
    """Parser-to-report parity: the report names the burst the runtime will enforce."""
    args = _complete_args(rate=25.0, burst=10.0, host_rate=200.0, host_burst=50.0)

    report = apply_secure_hub_profile(args)

    assert report is not None
    assert report.effective_limits[0] == "per-agent 25/s burst 10 (operator-stricter)"
    assert report.effective_limits[1] == "per-host 200/s burst 50 (operator-stricter)"
    assert args.burst == 10.0
    assert args.host_burst == 50.0


@pytest.mark.parametrize(
    ("missing", "fragment"),
    [
        ("token", "--token"),
        ("db", "--db"),
        ("identity_trust", "--identity-trust"),
        ("role_grants", "--role-grants"),
        ("message_auth_key", "--message-auth-key"),
        ("acl_policy", "--acl-policy"),
        ("tls_certfile", "--tls-certfile"),
        ("tls_keyfile", "--tls-keyfile"),
    ],
)
def test_each_required_material_class_has_an_isolated_refusal(missing: str, fragment: str) -> None:
    empty: object = [] if missing == "message_auth_key" else ("" if "file" not in missing else None)
    args = _complete_args(**{missing: empty})

    with pytest.raises(SecureModeError, match=fragment):
        apply_secure_hub_profile(args)


def test_missing_material_is_reported_in_one_aggregate_error() -> None:
    args = _complete_args(token=None, db=None, identity_trust="", role_grants="", tls_certfile=None)

    with pytest.raises(SecureModeError) as excinfo:
        apply_secure_hub_profile(args)

    message = str(excinfo.value)
    for fragment in ("--token", "--db", "--identity-trust", "--role-grants", "--tls-certfile"):
        assert fragment in message


def test_metrics_token_is_conditional_on_metrics() -> None:
    without_metrics = _complete_args(metrics=False, metrics_token=None)
    apply_secure_hub_profile(without_metrics)  # no error

    with_metrics = _complete_args(metrics=True, metrics_token=None)
    with pytest.raises(SecureModeError, match="--metrics-token"):
        apply_secure_hub_profile(with_metrics)


def test_report_names_effective_limits_and_missing_hooks() -> None:
    report = apply_secure_hub_profile(_complete_args())
    assert report is not None

    lines = report.stderr_lines()
    joined = "\n".join(lines)
    assert "secure mode enforced:" in joined
    assert "secure mode effective limits:" in joined
    assert "per-agent 100/s burst 20" in joined
    assert "per-host 500/s burst 100" in joined
    assert "connections/host 10" in joined
    assert "secure mode missing hooks:" in joined
    # The shared "hub token required" line is deduplicated across the two profiles.
    assert lines[0].count("hub token required") == 1


def test_missing_hooks_never_name_a_control_the_umbrella_composes() -> None:
    """The report must not claim identity verification is missing while enforcing it.

    The audit reproduced the secure report copying the paranoid ``missing_hooks``
    verbatim — reporting cryptographic identity as missing with "compose
    --team-secure" while the same report's enforced lines required identity binding.
    """
    report = apply_secure_hub_profile(_complete_args())
    assert report is not None

    for hook in report.missing_hooks:
        assert "--team-secure" not in hook
        assert "cryptographic per-agent identity verification" not in hook
    assert any("identity binding required" in line for line in report.enforced)


def test_missing_hooks_keep_the_genuinely_uncomposed_controls() -> None:
    """Filtering must not over-claim: uncomposed controls stay honestly missing."""
    report = apply_secure_hub_profile(_complete_args())
    assert report is not None

    joined = " ".join(report.missing_hooks)
    assert "at-rest encryption" in joined
    assert "mutual-TLS" in joined
    assert report.missing_hooks, "the report must never claim every control is composed"


def test_error_is_a_synapse_error_with_stable_code() -> None:
    args = _complete_args(token=None)

    with pytest.raises(SecureModeError) as excinfo:
        apply_secure_hub_profile(args)

    assert isinstance(excinfo.value, SynapseError)
    assert excinfo.value.code == "secure_mode"


def test_explicit_zero_rate_is_treated_as_disabled_not_preserved() -> None:
    # A parser default 0 and an explicit --rate 0 are indistinguishable and both
    # mean "disabled", which the preset replaces with its named ceiling.
    args = _complete_args(rate=0.0)

    apply_secure_hub_profile(args)

    assert args.rate == SECURE_AGENT_RATE
