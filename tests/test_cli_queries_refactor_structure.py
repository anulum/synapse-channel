# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — read-only query CLI split compatibility contract

from __future__ import annotations

from synapse_channel import cli, cli_queries
from synapse_channel.cli_query_commands import (
    _board,
    _cmd_board,
    _cmd_health,
    _cmd_manifest,
    _cmd_state,
    _cmd_who,
    _health,
    _manifest,
    _state,
    _who,
)
from synapse_channel.cli_query_parsers import add_parsers
from synapse_channel.cli_query_rendering import _print_board, _print_manifest
from synapse_channel.cli_query_transport import (
    AgentFactory,
    _drop_message,
    _query_hub,
)


def test_cli_queries_reexports_transport_helpers_from_owner_module() -> None:
    assert cli_queries.AgentFactory is AgentFactory
    assert cli_queries._drop_message is _drop_message
    assert cli_queries._query_hub is _query_hub


def test_cli_queries_reexports_command_handlers_from_owner_module() -> None:
    assert cli_queries._health is _health
    assert cli_queries._cmd_health is _cmd_health
    assert cli_queries._who is _who
    assert cli_queries._cmd_who is _cmd_who
    assert cli_queries._state is _state
    assert cli_queries._cmd_state is _cmd_state
    assert cli_queries._board is _board
    assert cli_queries._cmd_board is _cmd_board
    assert cli_queries._manifest is _manifest
    assert cli_queries._cmd_manifest is _cmd_manifest


def test_cli_queries_reexports_renderers_and_parser_registration() -> None:
    assert cli_queries._print_board is _print_board
    assert cli_queries._print_manifest is _print_manifest
    assert cli_queries.add_parsers is add_parsers


def test_top_level_parser_keeps_compatibility_command_functions() -> None:
    parser = cli.build_parser()
    assert parser.parse_args(["who"]).func is cli_queries._cmd_who
    assert parser.parse_args(["state"]).func is cli_queries._cmd_state
    assert parser.parse_args(["board"]).func is cli_queries._cmd_board
    assert parser.parse_args(["manifest"]).func is cli_queries._cmd_manifest
    assert parser.parse_args(["health"]).func is cli_queries._cmd_health
