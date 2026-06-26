# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — A2A CLI compatibility split tests

from __future__ import annotations

from synapse_channel import cli, cli_a2a, cli_a2a_card, cli_a2a_parsers, cli_a2a_serve


def test_cli_a2a_reexports_card_command_surface() -> None:
    assert cli_a2a._print_agent_card is cli_a2a_card._print_agent_card
    assert cli_a2a._a2a_card is cli_a2a_card._a2a_card
    assert cli_a2a._cmd_a2a_card is cli_a2a_card._cmd_a2a_card


def test_cli_a2a_reexports_serve_command_surface() -> None:
    assert cli_a2a._fetch_manifest is cli_a2a_serve._fetch_manifest
    assert cli_a2a._a2a_inbound_handler is cli_a2a_serve._a2a_inbound_handler
    assert cli_a2a._cmd_a2a_serve is cli_a2a_serve._cmd_a2a_serve


def test_cli_a2a_reexports_parser_registration() -> None:
    assert cli_a2a.add_parsers is cli_a2a_parsers.add_parsers


def test_top_level_parser_uses_compatibility_command_functions() -> None:
    parser = cli.build_parser()

    card_args = parser.parse_args(["a2a-card", "--endpoint-url", "https://example.test/a2a/v1"])
    serve_args = parser.parse_args(["a2a-serve", "--endpoint-url", "https://example.test/a2a/v1"])

    assert card_args.func is cli_a2a._cmd_a2a_card
    assert serve_args.func is cli_a2a._cmd_a2a_serve
