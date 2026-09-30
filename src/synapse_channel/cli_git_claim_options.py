# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Git claim outcome options
"""Register bounded claim waiting and read-only recovery CLI options."""

from __future__ import annotations

import argparse

from synapse_channel.client.claim_confirmation import (
    DEFAULT_CLAIM_REPLY_TIMEOUT,
    valid_claim_timeout,
)


def _deadline(raw: str) -> float:
    """Parse a finite deadline with fixed refusal text on invalid input."""
    try:
        value = float(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("deadline must be a number") from exc
    if not valid_claim_timeout(value):
        raise argparse.ArgumentTypeError(
            "deadline must be positive, finite and at most 300 seconds"
        )
    return value


def add_claim_outcome_options(parser: argparse.ArgumentParser) -> None:
    """Expose a bounded per-exchange deadline and mutation-free confirmation.

    Parameters
    ----------
    parser : argparse.ArgumentParser
        The existing Git claim subcommand parser.
    """
    parser.add_argument(
        "--reply-timeout",
        type=_deadline,
        default=DEFAULT_CLAIM_REPLY_TIMEOUT,
        help="Seconds per claim/confirmation exchange (default: 30; maximum: 300).",
    )
    parser.add_argument(
        "--confirm-only",
        action="store_true",
        help="Confirm an exact live lease without issuing or renewing a claim (unknown exits 3).",
    )
