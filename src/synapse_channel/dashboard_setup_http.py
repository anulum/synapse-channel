# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — whether a dashboard serves setup, and its read-only preflight
"""Decide at start whether this dashboard serves setup, and serve its preflight.

Setup changes the host the dashboard runs on. It is served only when the start
arms it with ``--setup-apply`` beside ``--operator``, binds loopback, uses a
versioned access file rather than the single compatibility bearer, and opens an
owner-only receipt directory named by ``--setup-receipts``; otherwise every
setup route answers 404 naming the first reason, as
:func:`~synapse_channel.dashboard_setup_contract.evaluate_setup_posture` orders
them. ``/capabilities.json`` states the same decision.

``GET /setup/preflight.json`` is read-only. It inspects the command line's
``local-single-user`` profile with the command line's own inspection and returns
the projection of
:func:`~synapse_channel.dashboard_setup_planner.build_setup_preflight`, to a
principal holding ``setup_plan`` only. ``POST /setup/plan``
(:mod:`synapse_channel.dashboard_setup_plan_http`) issues plans into the start's
one-use plan store.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from typing import Final

from synapse_channel.core.hub_exposure import is_loopback_host
from synapse_channel.core.secure_path import assert_owner_only_dir_path
from synapse_channel.dashboard_access import DashboardAccessPolicy
from synapse_channel.dashboard_access_http import AccessHttpDecision
from synapse_channel.dashboard_setup_contract import (
    SetupPosture,
    SetupPostureDecision,
    evaluate_setup_posture,
)
from synapse_channel.dashboard_setup_planner import SetupPlanStore, build_setup_preflight
from synapse_channel.dashboard_setup_receipts import SetupReceiptStore, SetupReceiptStoreError
from synapse_channel.setup_inspector import inspect_setup
from synapse_channel.setup_profiles import SetupProfile, get_setup_profile

SETUP_PREFLIGHT_PATH: Final = "/setup/preflight.json"
"""Read-only setup preflight: the profile's requirements and their observed status."""

SETUP_PATHS: Final = frozenset({SETUP_PREFLIGHT_PATH})
"""Every setup route this dashboard can serve."""

SETUP_APPLY_FLAG: Final = "--setup-apply"
"""The start flag that arms setup, beside ``--operator``."""

SETUP_RECEIPTS_FLAG: Final = "--setup-receipts"
"""The start flag naming the owner-only directory of the setup receipt chain."""

SETUP_PROFILE_ID: Final = "local-single-user"
"""The command line's profile the dashboard serves: the one its executor applies in full."""

Inspection = Callable[[SetupProfile], dict[str, object]]
"""Inspect one profile and return the command line's inspection document."""


@dataclass(frozen=True)
class SetupServing:
    """A start's setup decision, the receipt directory it proved usable and the profile it serves.

    The directory is kept, not an open chain: each use opens the chain and closes
    it again, so a start holds no database open and a failed start leaks none.
    ``plans`` is the start's one-use plan store, present only when setup is served.
    """

    decision: SetupPostureDecision
    receipts: Path | None = None
    profile: SetupProfile | None = None
    plans: SetupPlanStore | None = None

    def capability(self) -> dict[str, object]:
        """Return the ``setup`` block of ``/capabilities.json``."""
        return {"advertised": self.decision.advertised, "reason": self.decision.reason}


UNARMED: Final = SetupServing(SetupPostureDecision(False, "unarmed"))
"""The decision of a start that did not arm setup."""


def hub_inspection(uri: str, token: str | None) -> Inspection:
    """Return the dashboard's inspection: the command line's own, against the dashboard's hub.

    The hub's shared secret, when the dashboard has one, is handed to the
    inspection's hub probe in its environment, as the command line reads it,
    and is never part of the projection a browser reads.
    """

    def inspect(profile: SetupProfile) -> dict[str, object]:
        environment = dict(os.environ)
        if token:
            environment["SYNAPSE_TOKEN"] = token
        return asyncio.run(
            inspect_setup(profile, uri=uri, project=None, agent_id=None, env=environment)
        )

    return inspect


def open_setup_receipts(directory: str | Path) -> Path:
    """Prove that ``directory`` is safe and holds a usable setup receipt chain, and return it.

    The chain is opened, which checks its integrity, and closed again at once.

    Raises
    ------
    ValueError
        Naming :data:`SETUP_RECEIPTS_FLAG` and no path, when ``directory`` is
        missing, a symlink, not a directory, not the effective user's, or open to
        its group or others, or when the chain in it cannot be opened.
    """
    try:
        assert_owner_only_dir_path(directory, purpose=SETUP_RECEIPTS_FLAG)
        with SetupReceiptStore(directory):
            return Path(directory)
    except (ValueError, SetupReceiptStoreError, OSError) as exc:
        raise ValueError(
            f"{SETUP_RECEIPTS_FLAG} must name an existing owner-only directory "
            "(mode 0700 or stricter, owned by this user, not a symlink)"
        ) from exc


def setup_serving(
    *,
    host: str,
    operator: bool,
    setup_apply: bool,
    setup_receipts: str | Path | None,
    policy: DashboardAccessPolicy,
) -> SetupServing:
    """Decide at start whether this dashboard serves setup.

    Raises
    ------
    ValueError
        When ``--setup-apply`` is given without ``--operator``, when
        ``--setup-receipts`` is given without ``--setup-apply``, when the
        receipt directory cannot be used (:func:`open_setup_receipts`), or when
        the package does not register the served profile.
    """
    if setup_apply and not operator:
        raise ValueError(f"{SETUP_APPLY_FLAG} needs --operator")
    if setup_receipts is not None and not setup_apply:
        raise ValueError(f"{SETUP_RECEIPTS_FLAG} needs {SETUP_APPLY_FLAG}")
    receipts = open_setup_receipts(setup_receipts) if setup_receipts is not None else None
    posture = SetupPosture(
        feature_armed=setup_apply,
        loopback_bind=is_loopback_host(host),
        compatibility_access=policy.compatibility,
        versioned_access_file=not policy.compatibility and bool(policy.credentials),
        receipt_store_ready=receipts is not None,
    )
    decision = evaluate_setup_posture(posture)
    profile = get_setup_profile(SETUP_PROFILE_ID) if decision.advertised else None
    if decision.advertised and profile is None:
        raise ValueError(f"setup profile {SETUP_PROFILE_ID} is not registered")
    plans = SetupPlanStore() if decision.advertised else None
    return SetupServing(decision, receipts, profile, plans)


def setup_preflight_response(
    authorization: str | None,
    policy: DashboardAccessPolicy,
    serving: SetupServing,
    inspect: Inspection,
) -> AccessHttpDecision:
    """Answer ``GET /setup/preflight.json``.

    Returns
    -------
    AccessHttpDecision
        404 naming the posture's reason when setup is not served; 401 without a
        recognised bearer; 403 without ``setup_plan``; 503 when the inspection
        fails; otherwise 200 with the preflight projection, which holds no path,
        no hub address and no identity.
    """
    headers = (("Vary", "Authorization"),)
    profile = serving.profile
    if profile is None:
        body = f"setup not served: {serving.decision.reason}".encode()
        return AccessHttpDecision(HTTPStatus.NOT_FOUND, body, headers=headers)
    principal = policy.resolve_credential(authorization)
    if principal is None:
        return AccessHttpDecision(
            HTTPStatus.UNAUTHORIZED, b"bearer required", authenticate=True, headers=headers
        )
    if not principal.capabilities.setup_plan:
        return AccessHttpDecision(
            HTTPStatus.FORBIDDEN, b"setup_plan capability required", headers=headers
        )
    try:
        inspection = inspect(profile)
    except Exception:  # any probe failure is one bounded answer, never a trace
        return AccessHttpDecision(
            HTTPStatus.SERVICE_UNAVAILABLE, b"setup inspection unavailable", headers=headers
        )
    body = json.dumps(build_setup_preflight(profile, inspection), sort_keys=True).encode()
    return AccessHttpDecision(HTTPStatus.OK, body, principal, headers=headers)
