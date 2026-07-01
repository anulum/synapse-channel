# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — a grouped discovery view over the whole CLI surface
"""The ``synapse commands`` discovery view.

``synapse --help`` lists every subcommand in one flat block, which is a wall at the
current surface size. This command instead prints the same subcommands grouped by
the stability tier each is already assigned in
:mod:`synapse_channel.surface_taxonomy`, with the one-line summary of each tier, so
a reader can find the daily-safe core, the adapters, the read-only analysis
surface, the advisory governance surface, and the settling experimental surface at
a glance. It reads only the static taxonomy — no hub, no network, no side effects.
"""

from __future__ import annotations

import argparse

from synapse_channel.surface_taxonomy import (
    CLI_TAXONOMY,
    TIER_SUMMARIES,
    TIERS,
    taxonomy_by_tier,
)


def render_overview() -> str:
    """Return the grouped command overview as printable text."""
    lines = [
        f"SYNAPSE CHANNEL — {len(CLI_TAXONOMY)} commands in {len(TIERS)} stability tiers.",
        "Run `synapse <command> --help` for usage of any command.",
    ]
    by_tier = taxonomy_by_tier()
    for tier in TIERS:
        names = by_tier.get(tier, [])
        if not names:
            continue
        lines.append("")
        lines.append(f"{tier} — {TIER_SUMMARIES[tier]}")
        lines.append("  " + "  ".join(names))
    return "\n".join(lines)


def _cmd_commands(args: argparse.Namespace) -> int:
    """Dispatch the ``commands`` subcommand."""
    del args
    print(render_overview())
    return 0


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``commands`` discovery subparser."""
    overview = subparsers.add_parser(
        "commands",
        help="List every subcommand grouped by stability tier.",
    )
    overview.set_defaults(func=_cmd_commands)
