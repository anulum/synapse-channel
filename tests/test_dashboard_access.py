# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — dashboard principal/capability policy tests
"""Pin immutable roles, compatibility behavior, and bearer resolution."""

from __future__ import annotations

import pytest

from synapse_channel.dashboard_access import (
    DashboardAccessPolicy,
    DashboardCredential,
    DashboardPrincipal,
    capabilities_for_role,
    compatibility_access_policy,
)


@pytest.mark.parametrize("role", ["viewer", "operator", "admin"])
def test_capability_matrix_never_invents_admin_actions(role: str) -> None:
    capabilities = capabilities_for_role(role, operator_armed=True)  # type: ignore[arg-type]
    assert capabilities.read is True
    expected = role != "viewer"
    assert capabilities.message_send is expected
    assert capabilities.task_declare is expected
    assert capabilities.task_update is expected
    assert capabilities.as_dict() == {
        "read": True,
        "message_send": expected,
        "task_declare": expected,
        "task_update": expected,
    }
    for name, allowed in capabilities.as_dict().items():
        assert capabilities.allows(name) is allowed  # type: ignore[arg-type]


def test_unarmed_roles_are_read_only() -> None:
    for role in ("viewer", "operator", "admin"):
        capabilities = capabilities_for_role(role, operator_armed=False)
        assert capabilities.read is True
        assert not any(
            (capabilities.message_send, capabilities.task_declare, capabilities.task_update)
        )


def test_policy_resolves_every_bearer_without_exposing_secret() -> None:
    viewer = DashboardPrincipal(
        "review", "viewer", capabilities_for_role("viewer", operator_armed=True)
    )
    operator = DashboardPrincipal(
        "ops",
        "operator",
        capabilities_for_role("operator", operator_armed=True),
        "operator:studio/ops",
    )
    credentials = (
        DashboardCredential(viewer, b"v" * 32),
        DashboardCredential(operator, b"o" * 32),
    )
    policy = DashboardAccessPolicy(credentials, None, True)

    assert policy.reads_gated is True
    assert policy.resolve_read(f"Bearer {'v' * 32}") is viewer
    assert policy.resolve_credential(f"Bearer {'o' * 32}") is operator
    for malformed in (None, "", "Basic token", "Bearer ", "Bearer wrong", "Bearer x y"):
        assert policy.resolve_credential(malformed) is None
    assert "vvvv" not in repr(credentials)


def test_open_policy_falls_back_to_viewer_even_for_a_stale_bearer() -> None:
    viewer = DashboardPrincipal(
        "open", "viewer", capabilities_for_role("viewer", operator_armed=False)
    )
    policy = DashboardAccessPolicy((), viewer, False)
    assert policy.reads_gated is False
    assert policy.resolve_read(None) is viewer
    assert policy.resolve_read("Bearer stale") is viewer


def test_legacy_open_read_only_dashboard_is_an_implicit_viewer() -> None:
    policy = compatibility_access_policy(
        dashboard_token=None,
        token_protects_reads=False,
        operator_armed=False,
        operator_name="operator:DASH",
    )
    principal = policy.resolve_read(None)
    assert principal is not None
    assert principal.role == "viewer"
    assert policy.credentials == ()
    assert policy.compatibility is True


def test_legacy_caller_token_maps_to_viewer_or_operator() -> None:
    viewer = compatibility_access_policy(
        dashboard_token="v" * 32,
        token_protects_reads=True,
        operator_armed=False,
        operator_name="operator:DASH",
    )
    assert viewer.resolve_read(None) is None
    assert viewer.resolve_read(f"Bearer {'v' * 32}").role == "viewer"  # type: ignore[union-attr]

    operator = compatibility_access_policy(
        dashboard_token="o" * 32,
        token_protects_reads=True,
        operator_armed=True,
        operator_name="operator:DASH",
    )
    principal = operator.resolve_credential(f"Bearer {'o' * 32}")
    assert principal is not None
    assert principal.role == "operator"
    assert principal.operator_name == "operator:DASH"


def test_legacy_generated_operator_token_keeps_reads_open() -> None:
    policy = compatibility_access_policy(
        dashboard_token="s" * 32,
        token_protects_reads=False,
        operator_armed=True,
        operator_name="operator:DASH",
    )
    assert policy.resolve_read(None).role == "viewer"  # type: ignore[union-attr]
    assert policy.resolve_read(f"Bearer {'s' * 32}").role == "operator"  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "changes",
    [
        {"dashboard_token": ""},
        {"token_protects_reads": True},
        {"operator_armed": True},
    ],
)
def test_impossible_compatibility_postures_are_refused(changes: dict[str, object]) -> None:
    values: dict[str, object] = {
        "dashboard_token": None,
        "token_protects_reads": False,
        "operator_armed": False,
        "operator_name": "operator:DASH",
    }
    values.update(changes)
    with pytest.raises(ValueError):
        compatibility_access_policy(**values)  # type: ignore[arg-type]


def test_empty_credential_bytes_are_refused() -> None:
    principal = DashboardPrincipal(
        "review", "viewer", capabilities_for_role("viewer", operator_armed=False)
    )
    with pytest.raises(ValueError, match="non-empty bytes"):
        DashboardCredential(principal, b"")
