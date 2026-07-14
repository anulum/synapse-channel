# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — dedicated tests for messaging CLI parser registration

from __future__ import annotations

import argparse

import pytest

from synapse_channel import cli_messaging_parsers
from synapse_channel.cli_messaging_listen import _cmd_listen
from synapse_channel.cli_messaging_send import _cmd_send
from synapse_channel.cli_messaging_wait import _cmd_wait
from synapse_channel.client.agent import default_hub_uri
from synapse_channel.core.wake_capability import WAKE_PASSIVE


def _parser() -> argparse.ArgumentParser:
    """Return a parser whose send/wait/listen subcommands are registered."""
    parser = argparse.ArgumentParser(prog="synapse")
    subparsers = parser.add_subparsers()
    cli_messaging_parsers.add_parsers(subparsers)
    return parser


def _choices() -> list[str]:
    """Return the registered subcommand names."""
    parser = argparse.ArgumentParser(prog="synapse")
    subparsers = parser.add_subparsers()
    cli_messaging_parsers.add_parsers(subparsers)
    return list(subparsers.choices)


def test_all_three_commands_are_registered() -> None:
    choices = _choices()
    assert {"send", "wait", "listen"} <= set(choices)


class TestSendParser:
    """Cover the ``send`` argument surface."""

    def test_defaults_and_func_binding(self) -> None:
        args = _parser().parse_args(["send", "hello"])
        assert args.func is _cmd_send
        assert args.message == "hello"
        assert args.uri == default_hub_uri()
        assert args.name == "USER"
        assert args.target == "all"
        assert args.channel == ""
        assert args.wait_seconds == pytest.approx(2.0)
        assert args.priority is False
        assert args.require_recipient is False
        assert args.receipt_timeout == pytest.approx(2.0)
        assert args.encrypt_key_file is None
        assert args.encrypt_key_id == ""
        assert args.encrypt_recipients is None
        assert args.token is None
        assert args.ready_timeout == pytest.approx(5.0)

    def test_flags_types_and_appended_recipients(self) -> None:
        args = _parser().parse_args(
            [
                "send",
                "--target",
                "AGENT-A",
                "--channel",
                "ops",
                "--wait-seconds",
                "0.5",
                "--priority",
                "--require-recipient",
                "--receipt-timeout",
                "1.5",
                "--encrypt-recipient",
                "AGENT-A",
                "--encrypt-recipient",
                "AGENT-B",
                "payload",
            ]
        )
        assert args.target == "AGENT-A"
        assert args.channel == "ops"
        assert args.wait_seconds == pytest.approx(0.5)
        assert args.priority is True
        assert args.require_recipient is True
        assert args.receipt_timeout == pytest.approx(1.5)
        assert args.encrypt_recipients == ["AGENT-A", "AGENT-B"]
        assert args.message == "payload"

    def test_message_is_required(self) -> None:
        with pytest.raises(SystemExit):
            _parser().parse_args(["send"])


class TestWaitParser:
    """Cover the ``wait`` argument surface."""

    def test_defaults_and_func_binding(self) -> None:
        args = _parser().parse_args(["wait"])
        assert args.func is _cmd_wait
        assert args.uri == default_hub_uri()
        assert args.name == "USER"
        assert args.for_name is None
        assert args.timeout == pytest.approx(0.0)
        assert args.directed_only is False
        assert args.role is None
        assert args.wake_jitter == pytest.approx(8.0)
        assert args.token is None
        assert args.wake_capability == WAKE_PASSIVE
        assert args.ready_timeout == pytest.approx(5.0)

    def test_for_dest_directed_flag_and_role_append(self) -> None:
        args = _parser().parse_args(
            [
                "wait",
                "--for",
                "TEAM/*",
                "--directed-only",
                "--role",
                "proj/lead",
                "--role",
                "proj/second",
                "--wake-jitter",
                "0.0",
            ]
        )
        assert args.for_name == "TEAM/*"
        assert args.directed_only is True
        assert args.role == ["proj/lead", "proj/second"]
        assert args.wake_jitter == pytest.approx(0.0)

    def test_wake_capability_accepts_a_valid_choice(self) -> None:
        args = _parser().parse_args(["wait", "--wake-capability", "direct"])
        assert args.wake_capability == "direct"

    def test_wake_capability_rejects_an_unknown_choice(self) -> None:
        with pytest.raises(SystemExit):
            _parser().parse_args(["wait", "--wake-capability", "telepathy"])


class TestListenParser:
    """Cover the ``listen`` argument surface."""

    def test_defaults_and_func_binding(self) -> None:
        args = _parser().parse_args(["listen"])
        assert args.func is _cmd_listen
        assert args.uri == default_hub_uri()
        assert args.name == "USER"
        assert args.token is None
        assert args.ready_timeout == pytest.approx(5.0)
        assert args.for_name is None
        assert args.decrypt_key_file is None

    def test_for_dest_and_decrypt_key_override(self) -> None:
        args = _parser().parse_args(
            ["listen", "--for", "AGENT-A", "--decrypt-key-file", "/keys/chat.key"]
        )
        assert args.for_name == "AGENT-A"
        assert args.decrypt_key_file == "/keys/chat.key"
