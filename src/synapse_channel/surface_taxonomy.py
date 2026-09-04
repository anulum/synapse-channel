# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — public surface taxonomy and stability labels
"""A classified map of the public CLI surface, enforced against drift.

The CLI has grown to dozens of subcommands, and not all of them carry the same
weight: some are the stable coordination core an operator runs daily, some bridge
to other ecosystems, some only read and report, some are advisory governance, and
some are still settling. Left undocumented, that surface is hard to navigate and
easy to mistake an experimental verb for a load-bearing one.

This module is the single source of truth for that taxonomy. Every CLI subcommand
is assigned exactly one :data:`TIERS` label, and a regression test asserts the
taxonomy and the live parser agree — so a new subcommand cannot ship without being
placed, and a removed one cannot linger here. The design-preview documentation
pages (research designs that are intentionally *not* implemented) are tracked
separately in :data:`DESIGN_PREVIEW_DOCS` so they are never mistaken for shipped
surface.
"""

from __future__ import annotations

from dataclasses import dataclass

STABLE = "stable"
"""Daily-safe coordination core: stable wire and CLI surface."""

ADAPTER = "adapter"
"""Bridges to other ecosystems and tools (A2A, MCP, git, tmux, model workers)."""

ANALYSIS = "analysis"
"""Inspection and reporting that never mutates coordination state."""

GOVERNANCE = "governance"
"""Advisory governance: policy, approvals, access control, and release integrity."""

EXPERIMENTAL = "experimental"
"""Newer or advisory surfaces still settling; shape may change before 1.0."""

TIERS = (STABLE, ADAPTER, ANALYSIS, GOVERNANCE, EXPERIMENTAL)
"""The stability tiers, ordered from most to least load-bearing."""

TIER_SUMMARIES = {
    STABLE: "Daily-safe coordination core with a stable wire and CLI surface.",
    ADAPTER: "Bridges to other ecosystems and tools; optional extras, not core.",
    ANALYSIS: "Inspection and reporting that never mutates coordination state.",
    GOVERNANCE: "Advisory governance: policy, approvals, access control, release integrity.",
    EXPERIMENTAL: "Newer or advisory surfaces still settling; shape may change before 1.0.",
}
"""One-line description of each tier, used by the docs renderer and the report."""

CLI_TAXONOMY: dict[str, str] = {
    # stable coordination core
    "hub": STABLE,
    "send": STABLE,
    "wait": STABLE,
    "arm": STABLE,
    "listen": STABLE,
    "who": STABLE,
    "status": STABLE,
    "board": STABLE,
    "task": STABLE,
    "lock": STABLE,
    "channel": STABLE,
    "team": STABLE,
    "init": STABLE,
    "new": STABLE,
    "demo": STABLE,
    "quickstart-coding": STABLE,
    "fleet-init": STABLE,
    "commands": STABLE,
    "completions": STABLE,
    # bridges to other ecosystems and tools
    "a2a-card": ADAPTER,
    "a2a-client": ADAPTER,
    "a2a-conformance": ADAPTER,
    "a2a-interop-trace": ADAPTER,
    "a2a-serve": ADAPTER,
    "adapters": ADAPTER,
    "mcp": ADAPTER,
    "mcp-call": ADAPTER,
    "mcp-tools": ADAPTER,
    "ingest": ADAPTER,
    "codex-tmux": ADAPTER,
    "agent-tmux": ADAPTER,
    "waker": ADAPTER,
    "worker": ADAPTER,
    "worker-session": ADAPTER,
    "git-claim": ADAPTER,
    "git-claim-check": ADAPTER,
    "git-hook": ADAPTER,
    "git-init": ADAPTER,
    "git-release": ADAPTER,
    "install-shell-hook": ADAPTER,
    "shell-hook": ADAPTER,
    # read-only inspection and reporting
    "setup": ANALYSIS,
    "doctor": ANALYSIS,
    "state": ANALYSIS,
    "dead-letters": ANALYSIS,
    "approvals": ANALYSIS,
    "relay": ANALYSIS,
    "event-query": ANALYSIS,
    "debug": ANALYSIS,
    "causality": ANALYSIS,
    "multihub": ANALYSIS,
    "health": ANALYSIS,
    "reliability": ANALYSIS,
    "trust-graph": ANALYSIS,
    "cross-repo": ANALYSIS,
    "conflicts": ANALYSIS,
    "directory": ANALYSIS,
    "manifest": ANALYSIS,
    "dashboard": ANALYSIS,
    "identity": ANALYSIS,
    "accounting": ANALYSIS,
    "fleet-scorecard": ANALYSIS,
    # advisory governance and release integrity
    "policy-check": GOVERNANCE,
    "approval": GOVERNANCE,
    "postmortem": GOVERNANCE,
    "reproduce": GOVERNANCE,
    "merkle": GOVERNANCE,
    "acl": GOVERNANCE,
    "role": GOVERNANCE,
    "federation": GOVERNANCE,
    "verify-release": GOVERNANCE,
    "release": GOVERNANCE,
    "supervisor": GOVERNANCE,
    "compact": GOVERNANCE,
    "capability-card": GOVERNANCE,
    "encrypt-key": GOVERNANCE,
    "sqlcipher": GOVERNANCE,
    # newer or advisory surfaces still settling
    "benchmark": EXPERIMENTAL,
    "deliberate": EXPERIMENTAL,
    "memory-recall": EXPERIMENTAL,
    "participant": EXPERIMENTAL,
    "resource-bids": EXPERIMENTAL,
    "route-task": EXPERIMENTAL,
    "dispatch": EXPERIMENTAL,
    "sandbox": EXPERIMENTAL,
    "ttl-advice": EXPERIMENTAL,
    "auto-action": EXPERIMENTAL,
    "workflow": EXPERIMENTAL,
}
"""Every CLI subcommand mapped to exactly one stability tier."""

DESIGN_PREVIEW_DOCS = frozenset(
    {
        "agent-air-traffic-control.md",
        "cross-agent-adapter-kits.md",
        "federated-trust-model.md",
        "managed-github-app.md",
        "multi-hub-sync.md",
        "sandboxed-tools-and-marketplace.md",
    }
)
"""Documentation pages that describe designs intentionally not yet implemented."""

FIRST_USE_MAX_CONCEPTS = 8
"""Upper bound for the public first-use journey."""

FIRST_USE_CONCEPTS = ("install", "diagnose", "prove")
"""Concepts a new operator must understand before receiving first value."""

FIRST_USE_JOURNEY = (
    "python -m pip install synapse-channel",
    "synapse doctor",
    "synapse demo --output ./synapse-golden-demo",
)
"""The exact public first-use shell sequence."""


@dataclass(frozen=True)
class SurfaceProfile:
    """One measurable way to enter or discover the shipped surface.

    Attributes
    ----------
    name : str
        Stable profile identifier accepted by ``synapse commands --profile``.
    summary : str
        Operator-facing boundary of the profile.
    tiers : tuple of str
        Taxonomy tiers included when the profile does not select explicit commands.
    commands : tuple of str
        Explicit command subset; empty means all commands in ``tiers``.
    dependency_extras : tuple of str
        Optional dependency groups relevant to modes inside the profile. An empty
        tuple is the single-dependency base install; command entry points can still
        expose separately documented optional modes.
    activation : str
        The explicit action that enters the profile; inspection never activates it.
    deactivation : str
        The explicit boundary for leaving the profile or stopping its optional work.
    concepts : tuple of str
        Concepts counted for a first-use journey; empty outside that journey.
    journey : tuple of str
        Exact shell sequence for an executable journey; empty for discovery profiles.
    persistent_services : int
        Number of services the profile starts implicitly.
    """

    name: str
    summary: str
    tiers: tuple[str, ...]
    commands: tuple[str, ...]
    dependency_extras: tuple[str, ...]
    activation: str
    deactivation: str
    concepts: tuple[str, ...] = ()
    journey: tuple[str, ...] = ()
    persistent_services: int = 0


PROFILE_ORDER = ("first-use", "core", "adapters", "governance", "labs", "all")
"""Public profile identifiers in the order shown to operators."""

SURFACE_PROFILES = {
    "first-use": SurfaceProfile(
        name="first-use",
        summary="Three concepts prove the complete local loop before persistent setup.",
        tiers=(STABLE, ANALYSIS),
        commands=("doctor", "demo"),
        dependency_extras=(),
        activation="Run the three recorded journey commands in order.",
        deactivation=(
            "The demo stops its temporary hub; remove its chosen output directory if unwanted."
        ),
        concepts=FIRST_USE_CONCEPTS,
        journey=FIRST_USE_JOURNEY,
    ),
    "core": SurfaceProfile(
        name="core",
        summary="Base-install coordination and read-only operator commands.",
        tiers=(STABLE, ANALYSIS),
        commands=(),
        dependency_extras=(),
        activation="Install the base package; start only the hub or waiter you explicitly choose.",
        deactivation=(
            "Stop the exact process or user unit you started; no global profile state exists."
        ),
    ),
    "adapters": SurfaceProfile(
        name="adapters",
        summary="Explicit bridges for agent hosts, protocols, telemetry, and semantic claims.",
        tiers=(ADAPTER,),
        commands=(),
        dependency_extras=("a2a-grpc", "mcp", "otel", "semantic", "wasm"),
        activation="Install only the required extra, then launch that adapter command explicitly.",
        deactivation="Stop its process and remove its host launch entry or omit its opt-in flag.",
    ),
    "governance": SurfaceProfile(
        name="governance",
        summary="Policy, identity, evidence, encryption, and release-integrity commands.",
        tiers=(GOVERNANCE,),
        commands=(),
        dependency_extras=("cloud-hsm", "encryption", "pkcs11", "sqlcipher", "tpm2"),
        activation="Install the selected backend extra and pass its explicit policy or key option.",
        deactivation="Remove that option only under its documented migration and custody contract.",
    ),
    "labs": SurfaceProfile(
        name="labs",
        summary="Experimental and benchmark commands that never activate themselves.",
        tiers=(EXPERIMENTAL,),
        commands=(),
        dependency_extras=("benchmark", "wasm"),
        activation=(
            "Invoke the selected experimental command; pin the package version for its shape."
        ),
        deactivation=(
            "Stop the invoked process or omit the command; "
            "no lab runs in the background by default."
        ),
    ),
    "all": SurfaceProfile(
        name="all",
        summary="Every classified command, with the aggregate runtime dependency extra available.",
        tiers=TIERS,
        commands=(),
        dependency_extras=("all",),
        activation=(
            "Install 'synapse-channel[all]', then invoke only the capabilities you intend to use."
        ),
        deactivation="Use a clean base-only environment and stop explicitly launched processes.",
    ),
}
"""Measured public profiles; they classify discovery and never hide a command."""


def commands_in_profile(profile: str) -> list[str]:
    """Return commands selected by a public surface profile.

    Parameters
    ----------
    profile : str
        One of :data:`PROFILE_ORDER`.

    Returns
    -------
    list[str]
        Commands in stable tier order, or an empty list for an unknown profile.
    """
    selected = SURFACE_PROFILES.get(profile)
    if selected is None:
        return []
    if selected.commands:
        return list(selected.commands)
    return [
        command for tier in TIERS if tier in selected.tiers for command in subcommands_in_tier(tier)
    ]


def tier_of(subcommand: str) -> str | None:
    """Return the stability tier of a CLI subcommand, or ``None`` if unclassified.

    Parameters
    ----------
    subcommand : str
        The CLI subcommand name (for example ``"send"``).

    Returns
    -------
    str or None
        One of :data:`TIERS`, or ``None`` when the subcommand is not in the map.
    """
    return CLI_TAXONOMY.get(subcommand)


def subcommands_in_tier(tier: str) -> list[str]:
    """Return the sorted subcommands assigned to a tier.

    Parameters
    ----------
    tier : str
        One of :data:`TIERS`.

    Returns
    -------
    list[str]
        The subcommands in that tier, sorted; empty for an unknown tier.
    """
    return sorted(name for name, label in CLI_TAXONOMY.items() if label == tier)


def taxonomy_by_tier() -> dict[str, list[str]]:
    """Return the taxonomy grouped by tier in :data:`TIERS` order."""
    return {tier: subcommands_in_tier(tier) for tier in TIERS}


def unclassified(subcommands: list[str]) -> list[str]:
    """Return the given subcommands that have no tier, sorted.

    Parameters
    ----------
    subcommands : list[str]
        Subcommand names to check (typically the live parser's subcommands).

    Returns
    -------
    list[str]
        Names absent from :data:`CLI_TAXONOMY`, sorted.
    """
    return sorted(name for name in subcommands if name not in CLI_TAXONOMY)
