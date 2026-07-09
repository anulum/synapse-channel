# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — team-secure hub profile policy tests

from __future__ import annotations

import argparse

import pytest

from synapse_channel.core.team_secure import (
    TeamSecureModeError,
    apply_team_secure_hub_profile,
)


def _args(**overrides: object) -> argparse.Namespace:
    """Build minimal hub arguments for team-secure policy checks."""
    values: dict[str, object] = {
        "team_secure": True,
        "token": "s3cret",
        "identity_trust": "identity-trust.json",
        "role_grants": "role-grants.json",
        "require_identity_binding": False,
        "require_role_claim": False,
        "private_directed_messages": False,
        "message_auth_key": [],
        "require_message_auth": False,
        "require_acl": False,
        "acl_policy": "",
        "tls_certfile": None,
        "tls_keyfile": None,
        "db": None,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_team_secure_policy_is_noop_when_disabled() -> None:
    """Disabled team-secure mode returns no report and leaves arguments alone."""
    args = _args(team_secure=False, require_identity_binding=False)

    assert apply_team_secure_hub_profile(args) is None
    assert args.require_identity_binding is False
    assert args.require_role_claim is False
    assert args.private_directed_messages is False


def test_team_secure_policy_forces_trust_gates_and_reports() -> None:
    """Enabled team-secure mode turns on the multi-seat trust gates."""
    args = _args()

    report = apply_team_secure_hub_profile(args)

    assert report is not None
    assert args.require_identity_binding is True
    assert args.require_role_claim is True
    assert args.private_directed_messages is True
    assert "hub token required" in report.enforced
    assert "identity binding required" in report.enforced[1]
    assert "role-claim grants required" in report.enforced[2]
    assert "private directed messages required" in report.enforced
    assert any("message-auth" in item for item in report.recommended)
    assert any("require-acl" in item for item in report.recommended)
    assert any("tls" in item or "paranoid" in item for item in report.recommended)
    assert any("--db" in item for item in report.recommended)
    lines = report.stderr_lines()
    assert lines[0].startswith("team-secure mode enforced:")
    assert lines[1].startswith("team-secure mode recommended next:")


def test_team_secure_policy_omits_recommendations_when_already_hard() -> None:
    """Recommendations are empty when message-auth, ACL, TLS, and db are set."""
    args = _args(
        message_auth_key=["main:secret:ALPHA"],
        require_message_auth=True,
        require_acl=True,
        acl_policy="acl.json",
        tls_certfile="cert.pem",
        tls_keyfile="key.pem",
        db="hub.db",
    )

    report = apply_team_secure_hub_profile(args)

    assert report is not None
    assert report.recommended == ()
    assert len(report.stderr_lines()) == 1


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"token": None}, "requires --token"),
        ({"identity_trust": ""}, "requires --identity-trust"),
        ({"identity_trust": "   "}, "requires --identity-trust"),
        ({"role_grants": ""}, "requires --role-grants"),
        ({"role_grants": "  "}, "requires --role-grants"),
    ],
)
def test_team_secure_policy_rejects_missing_required_settings(
    overrides: dict[str, object],
    message: str,
) -> None:
    """Team-secure mode fails closed for settings the hub CLI directly controls."""
    with pytest.raises(TeamSecureModeError, match=message):
        apply_team_secure_hub_profile(_args(**overrides))
