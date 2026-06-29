# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — public surface taxonomy regressions

from __future__ import annotations

from pathlib import Path

from synapse_channel.cli import build_parser
from synapse_channel.surface_taxonomy import (
    CLI_TAXONOMY,
    DESIGN_PREVIEW_DOCS,
    STABLE,
    TIER_SUMMARIES,
    TIERS,
    subcommands_in_tier,
    taxonomy_by_tier,
    tier_of,
    unclassified,
)

ROOT = Path(__file__).resolve().parents[1]


def _live_subcommands() -> list[str]:
    """Return the subcommand names registered on the live CLI parser."""
    parser = build_parser()
    assert parser._subparsers is not None
    choices = parser._subparsers._group_actions[0].choices or {}
    return [str(name) for name in choices]


def test_every_live_subcommand_is_classified() -> None:
    # the drift guard: a new subcommand cannot ship without a stability tier
    assert unclassified(_live_subcommands()) == []


def test_taxonomy_has_no_stale_entries() -> None:
    # a removed subcommand cannot linger in the taxonomy
    live = set(_live_subcommands())
    assert sorted(name for name in CLI_TAXONOMY if name not in live) == []


def test_every_tier_label_is_valid() -> None:
    assert set(CLI_TAXONOMY.values()) <= set(TIERS)
    assert set(TIER_SUMMARIES) == set(TIERS)


def test_tier_of_resolves_and_rejects() -> None:
    assert tier_of("send") == STABLE
    assert tier_of("not-a-command") is None


def test_subcommands_in_tier_partition_the_taxonomy() -> None:
    by_tier = taxonomy_by_tier()
    assert list(by_tier) == list(TIERS)
    assert sum(len(names) for names in by_tier.values()) == len(CLI_TAXONOMY)
    assert subcommands_in_tier("not-a-tier") == []
    assert "send" in subcommands_in_tier(STABLE)


def test_design_preview_docs_are_present_and_not_in_the_cli() -> None:
    for doc in DESIGN_PREVIEW_DOCS:
        assert (ROOT / "docs" / doc).exists(), doc
    # design-preview pages are documentation, never CLI surface
    assert DESIGN_PREVIEW_DOCS.isdisjoint(CLI_TAXONOMY)


def test_public_surface_doc_lists_every_classified_command() -> None:
    doc = (ROOT / "docs" / "public-surface.md").read_text(encoding="utf-8")
    for command in CLI_TAXONOMY:
        assert f"`{command}`" in doc, command
    for tier in TIERS:
        assert tier in doc
