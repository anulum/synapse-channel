# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — process CLI team command
"""Local team process launcher command for the ``synapse`` CLI."""

from __future__ import annotations

import argparse
from collections.abc import Callable

from synapse_channel.client.launcher import run_team


def _cmd_team(
    args: argparse.Namespace,
    *,
    launcher: Callable[..., int] = run_team,
) -> int:
    """Launch a local hub plus one or two workers."""
    return launcher(
        port=args.port,
        no_workers=args.no_workers,
        fast_model=args.fast_model,
        reason_model=args.reason_model,
        prefix=args.prefix,
    )
