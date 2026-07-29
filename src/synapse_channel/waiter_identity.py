# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the "-rx" waiter-sidecar naming convention in one place
"""Compatibility facade for the waiter-sidecar identity conventions.

A durable mailbox listener connects as ``<identity>-rx`` and a provider pane
bridge as ``<identity>-pane-rx``. Both are *sidecars* of the identity they wake,
not agents of their own. Distinct names let durable gap replay coexist with live
pane injection without takeover churn. The coordination kernel owns the
implementation; this stable top-level path re-exports those canonical objects.
"""

from __future__ import annotations

from synapse_channel.core.waiter_identity import (
    PANE_WAITER_SUFFIX,
    WAITER_SUFFIX,
    is_waiter,
    legacy_project_scoped_terminal_sidecar,
    pane_waiter_name,
    split_roster,
    waiter_name,
    waiter_owner,
)

__all__ = [
    "PANE_WAITER_SUFFIX",
    "WAITER_SUFFIX",
    "is_waiter",
    "legacy_project_scoped_terminal_sidecar",
    "pane_waiter_name",
    "split_roster",
    "waiter_name",
    "waiter_owner",
]
