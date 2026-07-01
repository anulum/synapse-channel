# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the `synapse commands` discovery view

from __future__ import annotations

import pytest

from synapse_channel import cli, cli_commands_overview
from synapse_channel.surface_taxonomy import (
    CLI_TAXONOMY,
    TIER_SUMMARIES,
    TIERS,
    taxonomy_by_tier,
)


def test_parser_routes_commands_to_the_overview_handler() -> None:
    args = cli.build_parser().parse_args(["commands"])

    assert args.func is cli_commands_overview._cmd_commands


def test_overview_lists_every_tier_with_its_summary() -> None:
    overview = cli_commands_overview.render_overview()

    for tier in TIERS:
        assert f"{tier} — {TIER_SUMMARIES[tier]}" in overview


def test_overview_lists_every_classified_command() -> None:
    overview = cli_commands_overview.render_overview()

    for command in CLI_TAXONOMY:
        assert command in overview


def test_overview_counts_the_whole_surface_and_names_itself() -> None:
    overview = cli_commands_overview.render_overview()

    assert f"{len(CLI_TAXONOMY)} commands in {len(TIERS)} stability tiers" in overview
    # the discovery command is part of the stable core it prints
    assert "commands" in taxonomy_by_tier()["stable"]


def test_overview_orders_tiers_from_stable_to_experimental() -> None:
    overview = cli_commands_overview.render_overview()

    positions = [overview.index(f"{tier} —") for tier in TIERS]
    assert positions == sorted(positions)


def test_cli_main_prints_the_overview(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["commands"]) == 0

    out = capsys.readouterr().out
    assert "SYNAPSE CHANNEL" in out
    assert "stable — " in out
    assert "experimental — " in out


def test_render_overview_skips_a_tier_with_no_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tier that loses all commands disappears instead of rendering empty."""
    from synapse_channel import cli_commands_overview

    slim: dict[str, list[str]] = {tier: [] for tier in TIERS}
    slim["stable"] = ["send"]
    monkeypatch.setattr(cli_commands_overview, "taxonomy_by_tier", lambda: slim)
    text = cli_commands_overview.render_overview()
    assert "stable" in text
    assert "experimental —" not in text
