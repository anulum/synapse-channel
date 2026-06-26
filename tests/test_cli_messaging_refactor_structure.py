# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — messaging CLI compatibility split tests

from __future__ import annotations

from synapse_channel import (
    cli,
    cli_messaging,
    cli_messaging_listen,
    cli_messaging_parsers,
    cli_messaging_send,
    cli_messaging_wait,
)


def test_cli_messaging_reexports_send_command_surface() -> None:
    assert cli_messaging._send is cli_messaging_send._send
    assert cli_messaging._cmd_send is cli_messaging_send._cmd_send


def test_cli_messaging_reexports_wait_command_surface() -> None:
    assert cli_messaging._wait is cli_messaging_wait._wait
    assert cli_messaging._cmd_wait is cli_messaging_wait._cmd_wait


def test_cli_messaging_reexports_listen_command_surface() -> None:
    assert cli_messaging._listen is cli_messaging_listen._listen
    assert cli_messaging._cmd_listen is cli_messaging_listen._cmd_listen


def test_cli_messaging_reexports_parser_registration() -> None:
    assert cli_messaging.add_parsers is cli_messaging_parsers.add_parsers


def test_top_level_parser_uses_compatibility_command_functions() -> None:
    parser = cli.build_parser()

    send_args = parser.parse_args(["send", "hello"])
    wait_args = parser.parse_args(["wait", "--name", "WORKER"])
    listen_args = parser.parse_args(["listen", "--name", "WATCH"])

    assert send_args.func is cli_messaging._cmd_send
    assert wait_args.func is cli_messaging._cmd_wait
    assert listen_args.func is cli_messaging._cmd_listen
