# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — dedicated tests for A2A CLI parser registration

from __future__ import annotations

import argparse

import pytest

from synapse_channel import cli_a2a_parsers
from synapse_channel.cli_a2a_card import _cmd_a2a_card
from synapse_channel.cli_a2a_serve import _cmd_a2a_serve
from synapse_channel.client.agent import default_hub_uri


def _registered_parser() -> argparse.ArgumentParser:
    """Return a parser whose subcommands are populated by ``add_parsers``."""
    parser = argparse.ArgumentParser(prog="synapse")
    subparsers = parser.add_subparsers()
    cli_a2a_parsers.add_parsers(subparsers)
    return parser


def _subcommand_choices() -> dict[str, argparse.ArgumentParser]:
    """Expose the subparser choices registered by ``add_parsers``."""
    parser = argparse.ArgumentParser(prog="synapse")
    subparsers = parser.add_subparsers()
    cli_a2a_parsers.add_parsers(subparsers)
    return dict(subparsers.choices)


class TestRegistration:
    """The three A2A subcommands and the interop parser are registered."""

    def test_card_and_serve_are_registered(self) -> None:
        choices = _subcommand_choices()
        assert "a2a-card" in choices
        assert "a2a-serve" in choices

    def test_interop_parsers_are_delegated(self) -> None:
        # ``add_parsers`` calls the interop registrar first, so its command appears.
        assert "a2a-interop-trace" in _subcommand_choices()


class TestCardParser:
    """Cover the ``a2a-card`` argument surface."""

    def test_defaults_and_func_binding(self) -> None:
        parser = _registered_parser()
        args = parser.parse_args(["a2a-card", "--endpoint-url", "https://example.test/a2a/v1"])
        assert args.func is _cmd_a2a_card
        assert args.uri == default_hub_uri()
        assert args.name == "A2A-BRIDGE"
        assert args.token is None
        assert args.endpoint_url == "https://example.test/a2a/v1"
        assert args.bridge_name == "SYNAPSE CHANNEL"
        assert args.description is None
        assert args.documentation_url == "https://anulum.github.io/synapse-channel"
        assert args.bearer_auth is False

    def test_bearer_auth_flag_sets_true(self) -> None:
        parser = _registered_parser()
        args = parser.parse_args(
            ["a2a-card", "--endpoint-url", "https://example.test/a2a/v1", "--bearer-auth"]
        )
        assert args.bearer_auth is True

    def test_overrides_are_applied(self) -> None:
        parser = _registered_parser()
        args = parser.parse_args(
            [
                "a2a-card",
                "--uri",
                "ws://hub.test:9000",
                "--name",
                "CARD-BOT",
                "--token",
                "s3cret",
                "--endpoint-url",
                "https://example.test/a2a/v1",
                "--bridge-name",
                "Custom Bridge",
                "--description",
                "A test bridge",
                "--documentation-url",
                "https://docs.test",
            ]
        )
        assert args.uri == "ws://hub.test:9000"
        assert args.name == "CARD-BOT"
        assert args.token == "s3cret"
        assert args.bridge_name == "Custom Bridge"
        assert args.description == "A test bridge"
        assert args.documentation_url == "https://docs.test"

    def test_missing_endpoint_url_is_rejected(self) -> None:
        parser = _registered_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["a2a-card"])


class TestServeParser:
    """Cover the ``a2a-serve`` argument surface."""

    def test_defaults_and_func_binding(self) -> None:
        parser = _registered_parser()
        args = parser.parse_args(["a2a-serve", "--endpoint-url", "https://example.test/a2a/v1"])
        assert args.func is _cmd_a2a_serve
        assert args.uri == default_hub_uri()
        assert args.name == "A2A-BRIDGE"
        assert args.token is None
        assert args.host == "127.0.0.1"
        assert args.port == 8877
        assert args.endpoint_url == "https://example.test/a2a/v1"
        assert args.target == "all"
        assert args.bridge_name == "SYNAPSE CHANNEL"
        assert args.description is None
        assert args.documentation_url == "https://anulum.github.io/synapse-channel"
        assert args.bearer_auth is False
        assert args.a2a_token is None
        assert args.allow_origin is None
        assert args.insecure_off_loopback is False
        assert args.state_file is None
        assert args.task_timeout == pytest.approx(300.0)
        assert args.subscribe_timeout == pytest.approx(0.0)

    def test_numeric_arguments_are_coerced(self) -> None:
        parser = _registered_parser()
        args = parser.parse_args(
            [
                "a2a-serve",
                "--endpoint-url",
                "https://example.test/a2a/v1",
                "--port",
                "9999",
                "--task-timeout",
                "12.5",
                "--subscribe-timeout",
                "3.0",
            ]
        )
        assert args.port == 9999
        assert isinstance(args.port, int)
        assert args.task_timeout == pytest.approx(12.5)
        assert args.subscribe_timeout == pytest.approx(3.0)

    def test_allow_origin_appends_each_value(self) -> None:
        parser = _registered_parser()
        args = parser.parse_args(
            [
                "a2a-serve",
                "--endpoint-url",
                "https://example.test/a2a/v1",
                "--allow-origin",
                "https://a.test",
                "--allow-origin",
                "https://b.test",
            ]
        )
        assert args.allow_origin == ["https://a.test", "https://b.test"]

    def test_boolean_flags_and_token_override(self) -> None:
        parser = _registered_parser()
        args = parser.parse_args(
            [
                "a2a-serve",
                "--endpoint-url",
                "https://example.test/a2a/v1",
                "--bearer-auth",
                "--insecure-off-loopback",
                "--a2a-token",
                "bearer-value",
                "--state-file",
                "/tmp/a2a-state.json",
                "--target",
                "AGENT-A",
            ]
        )
        assert args.bearer_auth is True
        assert args.insecure_off_loopback is True
        assert args.a2a_token == "bearer-value"
        assert args.state_file == "/tmp/a2a-state.json"
        assert args.target == "AGENT-A"

    def test_non_integer_port_is_rejected(self) -> None:
        parser = _registered_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(
                [
                    "a2a-serve",
                    "--endpoint-url",
                    "https://example.test/a2a/v1",
                    "--port",
                    "not-an-int",
                ]
            )

    def test_missing_endpoint_url_is_rejected(self) -> None:
        parser = _registered_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["a2a-serve"])
