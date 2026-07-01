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

STABLE = "stable"
"""Daily-safe coordination core: stable wire and CLI surface."""

ADAPTER = "adapter"
"""Bridges to other ecosystems and tools (A2A, MCP, git, tmux, model workers)."""

ANALYSIS = "analysis"
"""Read-only inspection and reporting; safe to run, no coordination side effects."""

GOVERNANCE = "governance"
"""Advisory governance: policy, approvals, access control, and release integrity."""

EXPERIMENTAL = "experimental"
"""Newer or advisory surfaces still settling; shape may change before 1.0."""

TIERS = (STABLE, ADAPTER, ANALYSIS, GOVERNANCE, EXPERIMENTAL)
"""The stability tiers, ordered from most to least load-bearing."""

TIER_SUMMARIES = {
    STABLE: "Daily-safe coordination core with a stable wire and CLI surface.",
    ADAPTER: "Bridges to other ecosystems and tools; optional extras, not core.",
    ANALYSIS: "Read-only inspection and reporting with no coordination side effects.",
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
    "board": STABLE,
    "task": STABLE,
    "lock": STABLE,
    "channel": STABLE,
    "team": STABLE,
    "init": STABLE,
    "new": STABLE,
    "demo": STABLE,
    "quickstart-coding": STABLE,
    "commands": STABLE,
    # bridges to other ecosystems and tools
    "a2a-card": ADAPTER,
    "a2a-serve": ADAPTER,
    "adapters": ADAPTER,
    "mcp": ADAPTER,
    "mcp-call": ADAPTER,
    "mcp-tools": ADAPTER,
    "ingest": ADAPTER,
    "codex-tmux": ADAPTER,
    "agent-tmux": ADAPTER,
    "worker": ADAPTER,
    "worker-session": ADAPTER,
    "git-claim": ADAPTER,
    "git-hook": ADAPTER,
    "git-init": ADAPTER,
    "git-release": ADAPTER,
    "install-shell-hook": ADAPTER,
    "shell-hook": ADAPTER,
    # read-only inspection and reporting
    "doctor": ANALYSIS,
    "state": ANALYSIS,
    "relay": ANALYSIS,
    "event-query": ANALYSIS,
    "debug": ANALYSIS,
    "causality": ANALYSIS,
    "multihub": ANALYSIS,
    "health": ANALYSIS,
    "reliability": ANALYSIS,
    "conflicts": ANALYSIS,
    "directory": ANALYSIS,
    "manifest": ANALYSIS,
    "dashboard": ANALYSIS,
    "identity": ANALYSIS,
    "accounting": ANALYSIS,
    # advisory governance and release integrity
    "policy-check": GOVERNANCE,
    "approval": GOVERNANCE,
    "postmortem": GOVERNANCE,
    "reproduce": GOVERNANCE,
    "merkle": GOVERNANCE,
    "acl": GOVERNANCE,
    "federation": GOVERNANCE,
    "verify-release": GOVERNANCE,
    "release": GOVERNANCE,
    "supervisor": GOVERNANCE,
    "compact": GOVERNANCE,
    "encrypt-key": GOVERNANCE,
    # newer or advisory surfaces still settling
    "memory-recall": EXPERIMENTAL,
    "resource-bids": EXPERIMENTAL,
    "route-task": EXPERIMENTAL,
    "sandbox": EXPERIMENTAL,
    "ttl-advice": EXPERIMENTAL,
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
