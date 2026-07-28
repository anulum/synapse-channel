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
a glance. ``--profile`` selects a measured first-use or package boundary, and
``--json`` exposes the same contract to automation. Every view reads only the
static taxonomy — no hub, no network, no side effects.
"""

from __future__ import annotations

import argparse
import json

from synapse_channel.surface_taxonomy import (
    CLI_TAXONOMY,
    FIRST_USE_MAX_CONCEPTS,
    PROFILE_ORDER,
    SURFACE_PROFILES,
    TIER_SUMMARIES,
    TIERS,
    commands_in_profile,
    taxonomy_by_tier,
    tier_of,
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


def profile_payload(profile: str) -> dict[str, object]:
    """Return one surface profile as machine-readable operator data.

    Parameters
    ----------
    profile : str
        One of the public profile identifiers in ``PROFILE_ORDER``.

    Returns
    -------
    dict[str, object]
        Profile boundary, exact commands, dependency extras, activation controls,
        and first-use measurements.

    Raises
    ------
    ValueError
        If ``profile`` is not a public profile identifier.
    """
    selected = SURFACE_PROFILES.get(profile)
    if selected is None:
        raise ValueError(f"unknown surface profile: {profile}")
    commands = commands_in_profile(profile)
    payload: dict[str, object] = {
        "schema_version": "synapse-surface-profile.v1",
        "profile": selected.name,
        "summary": selected.summary,
        "tiers": list(selected.tiers),
        "top_level_commands": commands,
        "top_level_command_count": len(commands),
        "dependency_extras": list(selected.dependency_extras),
        "dependency_extra_count": len(selected.dependency_extras),
        "activation": selected.activation,
        "deactivation": selected.deactivation,
        "persistent_services_started_implicitly": selected.persistent_services,
    }
    if selected.concepts:
        concept_count = len(selected.concepts)
        payload.update(
            {
                "concepts": list(selected.concepts),
                "concept_count": concept_count,
                "concept_limit": FIRST_USE_MAX_CONCEPTS,
                "within_concept_limit": concept_count <= FIRST_USE_MAX_CONCEPTS,
                "journey": list(selected.journey),
                "shell_command_count": len(selected.journey),
            }
        )
    return payload


def render_profile(profile: str) -> str:
    """Return one public surface profile as printable text.

    Parameters
    ----------
    profile : str
        One of the public profile identifiers in ``PROFILE_ORDER``.

    Returns
    -------
    str
        Human-readable profile boundary and selected commands.
    """
    selected = SURFACE_PROFILES[profile]
    commands = commands_in_profile(profile)
    lines = [
        f"SYNAPSE CHANNEL profile: {profile}",
        selected.summary,
        (
            f"measure: {len(commands)} top-level commands / "
            f"{len(selected.dependency_extras)} optional extras / "
            f"{selected.persistent_services} implicit persistent services"
        ),
        "extras: " + (", ".join(selected.dependency_extras) or "none (base install)"),
        f"activate: {selected.activation}",
        f"deactivate: {selected.deactivation}",
    ]
    if selected.concepts:
        lines.append(
            f"first-use: {len(selected.concepts)} concepts / "
            f"{len(selected.journey)} shell commands / limit {FIRST_USE_MAX_CONCEPTS}"
        )
    if selected.journey:
        lines.extend(("", "journey:", *(f"  {command}" for command in selected.journey)))
    for tier in TIERS:
        names = [command for command in commands if tier_of(command) == tier]
        if names:
            lines.extend(("", f"{tier} — {TIER_SUMMARIES[tier]}", "  " + "  ".join(names)))
    return "\n".join(lines)


def _cmd_commands(args: argparse.Namespace) -> int:
    """Dispatch the ``commands`` subcommand."""
    if args.as_json:
        print(json.dumps(profile_payload(args.profile or "all"), indent=2, sort_keys=True))
    elif args.profile:
        print(render_profile(args.profile))
    else:
        print(render_overview())
    return 0


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``commands`` discovery subparser."""
    overview = subparsers.add_parser(
        "commands",
        help="List every command or inspect one measurable surface profile.",
    )
    overview.add_argument(
        "--profile",
        choices=PROFILE_ORDER,
        help="Show one first-use, core, optional-layer, or full-surface profile.",
    )
    overview.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit the selected profile as JSON (defaults to the all profile).",
    )
    overview.set_defaults(func=_cmd_commands)
