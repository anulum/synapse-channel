# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — paranoid-mode policy tests

from __future__ import annotations

import argparse

import pytest

from synapse_channel.core.paranoid import (
    MISSING_PARANOID_HOOKS,
    ParanoidModeError,
    apply_paranoid_hub_profile,
)


def _args(**overrides: object) -> argparse.Namespace:
    """Build minimal hub arguments for paranoid policy checks."""
    values: dict[str, object] = {
        "paranoid": True,
        "token": "s3cret",
        "db": "hub.db",
        "metrics": False,
        "metrics_token": None,
        "metrics_query_token_ok": True,
        "insecure_off_loopback": True,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_paranoid_policy_is_noop_when_disabled() -> None:
    """Disabled paranoid mode returns no report and leaves arguments alone."""
    args = _args(paranoid=False)

    assert apply_paranoid_hub_profile(args) is None
    assert args.metrics_query_token_ok is True
    assert args.insecure_off_loopback is True


def test_paranoid_policy_reports_enforced_and_missing_hooks() -> None:
    """Enabled paranoid mode reports what is enforced and what is still missing."""
    args = _args(metrics=True, metrics_token="metrics")

    report = apply_paranoid_hub_profile(args)

    assert report is not None
    assert args.metrics_query_token_ok is False
    assert args.insecure_off_loopback is False
    assert "hub token required" in report.enforced
    assert "metrics bearer-token auth required" in report.enforced
    assert "private channels" in report.missing_hooks
    assert report.missing_hooks == MISSING_PARANOID_HOOKS
    assert "paranoid mode missing hooks:" in report.stderr_lines()[1]


def test_paranoid_policy_omits_metrics_auth_when_metrics_disabled() -> None:
    """Disabled metrics do not appear as an enforced metrics-auth setting."""
    report = apply_paranoid_hub_profile(_args(metrics=False, metrics_token=None))

    assert report is not None
    assert "metrics bearer-token auth required" not in report.enforced


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"token": None}, "requires --token"),
        ({"db": None}, "requires --db"),
        ({"metrics": True, "metrics_token": None}, "requires --metrics-token"),
    ],
)
def test_paranoid_policy_rejects_missing_required_settings(
    overrides: dict[str, object],
    message: str,
) -> None:
    """Paranoid mode fails closed for settings the hub CLI directly controls."""
    with pytest.raises(ParanoidModeError, match=message):
        apply_paranoid_hub_profile(_args(**overrides))
