# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — dashboard principal and operator CLI arguments
"""Keep browser-access flags out of the already broad dashboard dispatcher."""

from __future__ import annotations

import argparse
from pathlib import Path


def add_dashboard_access_arguments(parser: argparse.ArgumentParser) -> None:
    """Register compatibility bearer, principal policy, and write-arm flags."""
    parser.add_argument(
        "--dashboard-token",
        default=None,
        help=(
            "Compatibility bearer for dashboard reads/writes; the React shell "
            "stays public so its unlock veil can load. Generated when required."
        ),
    )
    parser.add_argument(
        "--dashboard-access-file",
        type=Path,
        default=None,
        help=(
            "Owner-only versioned viewer/operator/admin token-file policy; "
            "cannot be combined with --dashboard-token or --operator-name."
        ),
    )
    parser.add_argument(
        "--operator",
        action="store_true",
        help=(
            "Arm POST /message, /task, and /task/update. Each request still "
            "needs its dashboard capability and hub authorization/audit."
        ),
    )
    parser.add_argument(
        "--operator-name",
        default=None,
        help=(
            "Compatibility sender for operator writes; access-file principals "
            "carry their own distinct relay identities."
        ),
    )
