# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — dedicated tests for process CLI parser registration

from __future__ import annotations

import argparse

import pytest

from synapse_channel import cli_processes_parsers
from synapse_channel.cli_processes_hub import _cmd_hub
from synapse_channel.cli_processes_supervisor import _cmd_supervisor
from synapse_channel.cli_processes_team import _cmd_team
from synapse_channel.cli_processes_worker import _cmd_worker
from synapse_channel.client.agent import default_hub_uri
from synapse_channel.core.agent_liveness import DEFAULT_WARN_STALE_RECIPIENTS
from synapse_channel.core.hub import DEFAULT_HOST, DEFAULT_PORT
from synapse_channel.core.logging_setup import DEFAULT_LOG_FORMAT, DEFAULT_LOG_LEVEL


def _parser() -> argparse.ArgumentParser:
    """Return a parser whose process subcommands are registered."""
    parser = argparse.ArgumentParser(prog="synapse")
    subparsers = parser.add_subparsers()
    cli_processes_parsers.add_parsers(subparsers)
    return parser


class TestFiniteLimit:
    """Cover every branch of the ``_finite_limit`` argparse type."""

    def test_parses_a_finite_non_negative_value(self) -> None:
        assert cli_processes_parsers._finite_limit("2.5") == pytest.approx(2.5)

    def test_zero_is_accepted(self) -> None:
        assert cli_processes_parsers._finite_limit("0") == pytest.approx(0.0)

    def test_non_numeric_is_rejected(self) -> None:
        with pytest.raises(argparse.ArgumentTypeError, match="is not a number"):
            cli_processes_parsers._finite_limit("abc")

    def test_nan_is_rejected(self) -> None:
        with pytest.raises(argparse.ArgumentTypeError, match="finite non-negative"):
            cli_processes_parsers._finite_limit("nan")

    def test_infinity_is_rejected(self) -> None:
        with pytest.raises(argparse.ArgumentTypeError, match="finite non-negative"):
            cli_processes_parsers._finite_limit("inf")

    def test_negative_is_rejected(self) -> None:
        with pytest.raises(argparse.ArgumentTypeError, match="finite non-negative"):
            cli_processes_parsers._finite_limit("-1")


class TestNonNegativeInt:
    """Cover the integer quota parser used by durable ingress bounds."""

    def test_zero_and_positive_values_are_accepted(self) -> None:
        assert cli_processes_parsers._non_negative_int("0") == 0
        assert cli_processes_parsers._non_negative_int("7") == 7

    def test_non_integer_and_negative_values_are_rejected(self) -> None:
        with pytest.raises(argparse.ArgumentTypeError, match="is not an integer"):
            cli_processes_parsers._non_negative_int("1.5")
        with pytest.raises(argparse.ArgumentTypeError, match="non-negative integer"):
            cli_processes_parsers._non_negative_int("-1")


class TestLoggingArguments:
    """The shared logging options are attached to daemon subcommands."""

    def test_defaults(self) -> None:
        args = _parser().parse_args(["hub"])
        assert args.log_format == DEFAULT_LOG_FORMAT
        assert args.log_level == DEFAULT_LOG_LEVEL

    def test_invalid_log_format_is_rejected(self) -> None:
        with pytest.raises(SystemExit):
            _parser().parse_args(["hub", "--log-format", "smoke-signals"])

    def test_invalid_log_level_is_rejected(self) -> None:
        with pytest.raises(SystemExit):
            _parser().parse_args(["hub", "--log-level", "SHOUT"])


class TestHubParser:
    """Cover the ``hub`` argument surface."""

    def test_defaults_and_func_binding(self) -> None:
        args = _parser().parse_args(["hub"])
        assert args.func is _cmd_hub
        assert args.host == DEFAULT_HOST
        assert args.port == DEFAULT_PORT
        assert isinstance(args.port, int)
        assert args.rate == pytest.approx(0.0)
        assert args.burst == pytest.approx(20.0)
        assert args.durable_ingress_events == 0
        assert args.durable_ingress_bytes == 0
        assert args.durable_ingress_window == pytest.approx(60.0)
        assert args.warn_stale_recipients is DEFAULT_WARN_STALE_RECIPIENTS
        assert args.namespace_owner == []
        assert args.multihub_watch == []
        assert args.federation_observe_only is False
        assert args.insecure_off_loopback is False

    def test_finite_limit_type_rejects_nan_rate(self) -> None:
        with pytest.raises(SystemExit):
            _parser().parse_args(["hub", "--rate", "nan"])

    def test_finite_limit_type_accepts_a_rate(self) -> None:
        args = _parser().parse_args(["hub", "--rate", "5.5", "--burst", "10"])
        assert args.rate == pytest.approx(5.5)
        assert args.burst == pytest.approx(10.0)

    def test_durable_ingress_flags_parse_as_one_runtime_policy(self) -> None:
        args = _parser().parse_args(
            [
                "hub",
                "--durable-ingress-events",
                "25",
                "--durable-ingress-bytes",
                "4096",
                "--durable-ingress-window",
                "120",
            ]
        )

        assert args.durable_ingress_events == 25
        assert args.durable_ingress_bytes == 4096
        assert args.durable_ingress_window == pytest.approx(120.0)

    def test_durable_ingress_window_rejects_a_negative_value(self) -> None:
        with pytest.raises(SystemExit):
            _parser().parse_args(["hub", "--durable-ingress-window", "-1"])

    def test_boolean_optional_action_toggles_both_ways(self) -> None:
        armed = _parser().parse_args(["hub", "--warn-stale-recipients"])
        disarmed = _parser().parse_args(["hub", "--no-warn-stale-recipients"])
        assert armed.warn_stale_recipients is True
        assert disarmed.warn_stale_recipients is False

    def test_append_and_store_true_flags(self) -> None:
        args = _parser().parse_args(
            [
                "hub",
                "--namespace-owner",
                "proj=syn-1",
                "--namespace-owner",
                "other=syn-2",
                "--federation-observe-only",
                "--insecure-off-loopback",
            ]
        )
        assert args.namespace_owner == ["proj=syn-1", "other=syn-2"]
        assert args.federation_observe_only is True
        assert args.insecure_off_loopback is True


class TestWorkerParser:
    """Cover the ``worker`` argument surface."""

    def test_defaults_and_func_binding(self) -> None:
        args = _parser().parse_args(["worker"])
        assert args.func is _cmd_worker
        assert args.name == "FAST"
        assert args.uri == default_hub_uri()
        assert args.provider == "ollama"
        assert args.reply_target_mode == "all"
        assert args.max_context == 8
        assert args.task_class is None

    def test_provider_choice_is_validated(self) -> None:
        args = _parser().parse_args(["worker", "--provider", "openai"])
        assert args.provider == "openai"
        with pytest.raises(SystemExit):
            _parser().parse_args(["worker", "--provider", "carrier-pigeon"])

    def test_task_class_appends(self) -> None:
        args = _parser().parse_args(["worker", "--task-class", "chat", "--task-class", "code"])
        assert args.task_class == ["chat", "code"]


class TestTeamParser:
    """Cover the ``team`` argument surface."""

    def test_defaults_and_func_binding(self) -> None:
        args = _parser().parse_args(["team"])
        assert args.func is _cmd_team
        assert args.port == DEFAULT_PORT
        assert args.no_workers is False

    def test_no_workers_flag(self) -> None:
        args = _parser().parse_args(["team", "--no-workers"])
        assert args.no_workers is True


class TestSupervisorParser:
    """Cover the ``supervisor`` argument surface."""

    def test_defaults_and_func_binding(self) -> None:
        args = _parser().parse_args(["supervisor"])
        assert args.func is _cmd_supervisor
        assert args.name == "SUPERVISOR"
        assert args.predictive_stall is True

    def test_no_predictive_stall_store_false(self) -> None:
        args = _parser().parse_args(["supervisor", "--no-predictive-stall"])
        assert args.predictive_stall is False
