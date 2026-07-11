# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — shared opt-in memory wiring for Participant CLI commands
"""Own Participant memory flags and wrap seats without growing command modules."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from synapse_channel.participants.memory_contract import MemoryPolicy
from synapse_channel.participants.memory_participant import MemoryAugmentedParticipant
from synapse_channel.participants.participant import Participant
from synapse_channel.participants.remanentia_http import RemanentiaHttpRecall

DEFAULT_MEMORY_TIMEOUT = 2.0
DEFAULT_MEMORY_TOP_K = 3
DEFAULT_MEMORY_MAX_CHARS = 4096


def add_memory_arguments(parser: argparse.ArgumentParser) -> None:
    """Register the same opt-in recall flags on ask, exchange, and convene."""
    parser.allow_abbrev = False
    parser.add_argument(
        "--memory-url",
        default=None,
        help=(
            "REMANENTIA HTTPS origin or literal loopback HTTP origin; "
            "omitted keeps memory disabled."
        ),
    )
    parser.add_argument(
        "--memory-token-file",
        default=None,
        help="Bearer-token file for REMANENTIA; token literals are not accepted.",
    )
    parser.add_argument(
        "--memory-timeout",
        type=float,
        default=None,
        metavar="SECONDS",
        help=f"Hard recall timeout (default {DEFAULT_MEMORY_TIMEOUT:g}s when enabled).",
    )
    parser.add_argument(
        "--memory-top-k",
        type=int,
        default=None,
        metavar="N",
        help=f"Maximum hits per turn (default {DEFAULT_MEMORY_TOP_K}).",
    )
    parser.add_argument(
        "--memory-max-chars",
        type=int,
        default=None,
        metavar="N",
        help=f"Maximum rendered memory characters (default {DEFAULT_MEMORY_MAX_CHARS}).",
    )


def wrap_participants(
    participants: Sequence[Participant],
    args: argparse.Namespace,
) -> list[Participant]:
    """Return unchanged seats when disabled, otherwise one shared recall wrapper."""
    url = getattr(args, "memory_url", None)
    token_file = getattr(args, "memory_token_file", None)
    timeout = getattr(args, "memory_timeout", None)
    top_k = getattr(args, "memory_top_k", None)
    max_chars = getattr(args, "memory_max_chars", None)
    tuning = (token_file, timeout, top_k, max_chars)
    if not url:
        if any(value is not None for value in tuning):
            raise ValueError("memory tuning flags require --memory-url")
        return list(participants)

    policy = MemoryPolicy(
        timeout_seconds=DEFAULT_MEMORY_TIMEOUT if timeout is None else timeout,
        top_k=DEFAULT_MEMORY_TOP_K if top_k is None else top_k,
        max_chars=DEFAULT_MEMORY_MAX_CHARS if max_chars is None else max_chars,
    )
    recall = RemanentiaHttpRecall(
        base_url=url,
        token_file=token_file,
        timeout_seconds=policy.timeout_seconds,
    )
    return [MemoryAugmentedParticipant(participant, recall, policy) for participant in participants]
