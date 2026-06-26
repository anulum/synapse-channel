# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the messaging CLI commands (send/wait/listen)

from __future__ import annotations

from synapse_channel import cli, cli_arm, cli_messaging


def test_parser_send_and_listen() -> None:
    send = cli.build_parser().parse_args(
        ["send", "hello", "--target", "FAST", "--wait-seconds", "0"]
    )
    assert send.message == "hello"
    assert send.target == "FAST"
    assert send.wait_seconds == 0.0

    listen = cli.build_parser().parse_args(["listen", "--name", "WATCH"])
    assert listen.name == "WATCH"


def test_parser_listen_for_flag() -> None:
    listen = cli.build_parser().parse_args(["listen", "--name", "B", "--for", "B"])
    assert listen.for_name == "B"
    assert listen.func is cli_messaging._cmd_listen


def test_parser_wait() -> None:
    args = cli.build_parser().parse_args(["wait", "--name", "X", "--for", "Y", "--timeout", "5"])
    assert args.name == "X"
    assert args.for_name == "Y"
    assert args.timeout == 5.0
    assert args.func is cli_messaging._cmd_wait


def test_parser_wait_directed_only() -> None:
    args = cli.build_parser().parse_args(["wait", "--for", "B", "--directed-only"])
    assert args.directed_only is True


def test_parser_send_priority() -> None:
    args = cli.build_parser().parse_args(["send", "hi", "--priority"])
    assert args.priority is True


def test_parser_send_require_recipient() -> None:
    args = cli.build_parser().parse_args(["send", "hi", "--target", "B", "--require-recipient"])
    assert args.require_recipient is True
    assert args.receipt_timeout == 2.0


def test_parser_wait_wake_jitter() -> None:
    args = cli.build_parser().parse_args(["wait", "--for", "B", "--wake-jitter", "3"])
    assert args.wake_jitter == 3.0
    assert cli.build_parser().parse_args(["wait", "--for", "B"]).wake_jitter == 8.0


def test_parser_arm_is_persistent_directed_waiter() -> None:
    args = cli.build_parser().parse_args(["arm", "--name", "B", "--for", "B"])
    assert args.name == "B"
    assert args.for_name == "B"
    assert args.directed_only is True
    assert args.func is cli_arm._cmd_arm


def test_parser_arm_broadcasts_opt_in() -> None:
    args = cli.build_parser().parse_args(["arm", "--for", "B", "--broadcasts"])
    assert args.directed_only is False
