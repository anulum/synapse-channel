# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the `synapse commands` discovery view

from __future__ import annotations

import json

import pytest

from synapse_channel import cli, cli_commands_overview
from synapse_channel.surface_taxonomy import (
    CLI_TAXONOMY,
    PROFILE_ORDER,
    SURFACE_PROFILES,
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


def test_profile_view_prints_measurement_and_activation_boundaries() -> None:
    text = cli_commands_overview.render_profile("first-use")

    assert "measure: 2 top-level commands / 0 optional extras / 0 implicit" in text
    assert "first-use: 3 concepts / 3 shell commands / limit 8" in text
    assert "extras: none (base install)" in text
    assert "activate:" in text
    assert "deactivate:" in text
    for command in SURFACE_PROFILES["first-use"].journey:
        assert command in text


def test_profile_payload_is_machine_readable() -> None:
    payload = cli_commands_overview.profile_payload("first-use")

    assert payload["schema_version"] == "synapse-surface-profile.v1"
    assert payload["concept_count"] == 3
    assert payload["within_concept_limit"] is True
    assert payload["top_level_commands"] == ["doctor", "demo"]
    assert payload["top_level_command_count"] == 2
    assert payload["dependency_extras"] == []
    assert payload["dependency_extra_count"] == 0
    assert payload["persistent_services_started_implicitly"] == 0
    with pytest.raises(ValueError, match="unknown surface profile"):
        cli_commands_overview.profile_payload("unknown")


def test_cli_main_prints_selected_profile_as_json(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["commands", "--profile", "first-use", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["profile"] == "first-use"
    assert payload["journey"] == list(SURFACE_PROFILES["first-use"].journey)


def test_cli_main_prints_profile_without_a_journey(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["commands", "--profile", "core"]) == 0

    text = capsys.readouterr().out
    assert "SYNAPSE CHANNEL profile: core" in text
    assert "journey:" not in text
    assert "stable — " in text
    assert "analysis — " in text
    assert "first-use:" not in text
    assert "concept_count" not in cli_commands_overview.profile_payload("core")


def test_parser_accepts_every_public_profile() -> None:
    for profile in PROFILE_ORDER:
        args = cli.build_parser().parse_args(["commands", "--profile", profile])
        assert args.profile == profile


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
