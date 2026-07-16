# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — roadmap shipped-reality contract
"""Keep the public roadmap aligned with real product and CLI surfaces."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from synapse_channel import cli

ROOT = Path(__file__).resolve().parents[1]
ROADMAP = ROOT / "ROADMAP.md"


def _roadmap() -> str:
    """Return the public roadmap as UTF-8 text."""
    return ROADMAP.read_text(encoding="utf-8")


def _collapsed() -> str:
    """Return lowercase, single-spaced roadmap text."""
    return " ".join(_roadmap().lower().split())


def _top_level_commands() -> set[str]:
    """Return the production parser's registered top-level commands."""
    parser = cli.build_parser()
    subparser_actions = [
        action for action in parser._actions if isinstance(action, argparse._SubParsersAction)
    ]
    assert len(subparser_actions) == 1
    return set(subparser_actions[0].choices)


def test_roadmap_reclassifies_features_that_already_shipped() -> None:
    """Previously speculative integrations must now appear as shipped reality."""
    text = _collapsed()

    for shipped_surface in (
        "mcp server",
        "a2a agent card and http+json bridge",
        "human-in-the-loop approval",
        "event-log ingestion and recall",
        "opentelemetry projection",
        "multi-hub observation",
        "machine-key trust-on-first-use identity pins",
        "opencode",
        "webassembly tool sandbox",
    ):
        assert shipped_surface in text

    for stale_statement in (
        "an optional mcp-server face that exposes hub operations",
        "human-in-the-loop interrupt and approval gates with a review outbox",
        "one memory story: a projection over the event log",
        "an optional opentelemetry exporter over the event log",
        "a gated cross-host bridge, only on real cross-machine demand",
        "cryptographic agent identity in the single-owner local setting",
    ):
        assert stale_statement not in text


def test_roadmap_shipped_cli_claims_exist_in_the_production_parser() -> None:
    """Every named command family must be registered by the real CLI parser."""
    parser = cli.build_parser()
    commands = _top_level_commands()

    assert {
        "mcp",
        "a2a-card",
        "a2a-serve",
        "approval",
        "ingest",
        "memory-recall",
        "causality",
        "federation",
        "dashboard",
        "sandbox",
        "adapters",
    } <= commands

    parse_cases = (
        ("mcp",),
        ("a2a-card", "--endpoint-url", "http://127.0.0.1:8877/a2a/v1"),
        ("a2a-serve", "--endpoint-url", "http://127.0.0.1:8877/a2a/v1"),
        ("approval", "status", "events.db"),
        ("ingest", "events.db", "--memory"),
        ("memory-recall", "events.db", "handoff receipt"),
        ("causality", "otel", "events.db", "--out", "spans.json"),
        ("federation", "list"),
        ("dashboard",),
        ("sandbox", "validate", "tool.manifest.json"),
        ("adapters", "opencode", "status"),
    )
    for argv in parse_cases:
        parsed = parser.parse_args(argv)
        assert callable(parsed.func), argv


def test_roadmap_local_links_resolve_and_do_not_publish_internal_plans() -> None:
    """Every relative roadmap link must exist and avoid internal-only surfaces."""
    destinations = re.findall(r"\[[^\]]+\]\(([^)]+)\)", _roadmap())
    relative_links = [
        destination.split("#", 1)[0]
        for destination in destinations
        if not destination.startswith(("http://", "https://", "#"))
    ]

    assert relative_links
    for relative_link in relative_links:
        assert (ROOT / relative_link).is_file(), relative_link
        assert "docs/internal/" not in relative_link
        assert ".coordination/" not in relative_link


def test_roadmap_preserves_security_and_external_validation_boundaries() -> None:
    """Shipped primitives must not be inflated into certification or authority."""
    text = _collapsed()

    for boundary in (
        "partial validation, not external a2a certification",
        "cards and routing recommendations do not grant authority",
        "federation does not turn observed peer state into local authority",
        "it is not a general host or container isolation claim",
        "does not claim external certification",
        "a substitute for operating-system isolation",
    ):
        assert boundary in text


def test_roadmap_names_current_pre_one_priorities_and_research_boundary() -> None:
    """The roadmap must distinguish active hardening from optional research."""
    text = _roadmap()

    for heading in (
        "## Shipped foundation",
        "## Shipped experimental surfaces",
        "## Active pre-1.0 priorities",
        "## Research candidates",
        "## Explicit limits and non-goals",
    ):
        assert heading in text

    collapsed = _collapsed()
    for priority in (
        "delivery integrity and secure outcomes",
        "a smaller golden path",
        "protocol and platform evidence",
        "operational recovery",
        "external validation",
        "stable boundaries",
    ):
        assert priority in collapsed

    for candidate in (
        "agent transaction protocol",
        "pre-execution read/write-set simulation",
        "per-operation, non-transferable capability tokens",
        "oidc or spiffe",
    ):
        assert candidate in collapsed
