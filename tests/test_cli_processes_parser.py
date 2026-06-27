# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the long-running process commands (hub/worker/team/supervisor)

from __future__ import annotations

from synapse_channel import cli, cli_processes
from synapse_channel.core.hub import (
    DEFAULT_COMPACT_HINT_THRESHOLD,
    DEFAULT_SHUTDOWN_CLOSE_TIMEOUT,
    DEFAULT_TAKEOVER_COOLDOWN,
)
from synapse_channel.core.logging_setup import DEFAULT_LOG_FORMAT, DEFAULT_LOG_LEVEL
from synapse_channel.core.scoping import MAX_DECLARED_PATHS


def test_parser_hub_defaults() -> None:
    args = cli.build_parser().parse_args(["hub"])
    assert args.host == "localhost"
    assert args.db is None
    assert args.func is cli_processes._cmd_hub


def test_parser_worker_custom() -> None:
    args = cli.build_parser().parse_args(
        ["worker", "--name", "REASON", "--provider", "rule", "--min-reply-interval", "1.5"]
    )
    assert args.name == "REASON"
    assert args.provider == "rule"
    assert args.min_reply_interval == 1.5


def test_parser_supervisor() -> None:
    args = cli.build_parser().parse_args(["supervisor", "--idle-seconds", "60", "--interval", "5"])
    assert args.idle_seconds == 60.0
    assert args.interval == 5.0
    assert args.func is cli_processes._cmd_supervisor


def test_parser_worker_task_class() -> None:
    worker = cli.build_parser().parse_args(
        ["worker", "--task-class", "reason", "--task-class", "heavy"]
    )
    assert worker.task_class == ["reason", "heavy"]


def test_parser_worker_tiered_provider_and_heavy_model() -> None:
    args = cli.build_parser().parse_args(["worker", "--provider", "tiered", "--heavy-model", "big"])
    assert args.provider == "tiered"
    assert args.heavy_model == "big"


def test_parser_hub_caps() -> None:
    args = cli.build_parser().parse_args(["hub", "--max-clients", "8", "--max-msg-kb", "32"])
    assert args.max_clients == 8
    assert args.max_msg_kb == 32


def test_parser_hub_host_rate_caps() -> None:
    defaults = cli.build_parser().parse_args(["hub"])
    assert defaults.host_rate == 0.0
    assert defaults.host_burst == 40.0
    args = cli.build_parser().parse_args(["hub", "--host-rate", "5", "--host-burst", "12"])
    assert args.host_rate == 5.0
    assert args.host_burst == 12.0


def test_parser_hub_per_agent_quotas() -> None:
    args = cli.build_parser().parse_args(
        ["hub", "--max-claims-per-agent", "10", "--max-offers-per-agent", "5"]
    )
    assert args.max_claims_per_agent == 10
    assert args.max_offers_per_agent == 5


def test_parser_hub_max_paths_per_claim() -> None:
    assert cli.build_parser().parse_args(["hub"]).max_paths_per_claim == MAX_DECLARED_PATHS
    args = cli.build_parser().parse_args(["hub", "--max-paths-per-claim", "20"])
    assert args.max_paths_per_claim == 20


def test_parser_hub_compact_hint_threshold() -> None:
    default = cli.build_parser().parse_args(["hub"]).compact_hint_threshold
    assert default == DEFAULT_COMPACT_HINT_THRESHOLD
    args = cli.build_parser().parse_args(["hub", "--compact-hint-threshold", "500"])
    assert args.compact_hint_threshold == 500


def test_parser_hub_takeover_cooldown() -> None:
    default = cli.build_parser().parse_args(["hub"]).takeover_cooldown
    assert default == DEFAULT_TAKEOVER_COOLDOWN
    args = cli.build_parser().parse_args(["hub", "--takeover-cooldown", "5.5"])
    assert args.takeover_cooldown == 5.5


def test_parser_hub_shutdown_close_timeout() -> None:
    default = cli.build_parser().parse_args(["hub"]).shutdown_close_timeout
    assert default == DEFAULT_SHUTDOWN_CLOSE_TIMEOUT
    args = cli.build_parser().parse_args(["hub", "--shutdown-close-timeout", "2.5"])
    assert args.shutdown_close_timeout == 2.5


def test_parser_hub_logging_options() -> None:
    defaults = cli.build_parser().parse_args(["hub"])
    assert defaults.log_format == DEFAULT_LOG_FORMAT
    assert defaults.log_level == DEFAULT_LOG_LEVEL
    args = cli.build_parser().parse_args(["hub", "--log-format", "json", "--log-level", "DEBUG"])
    assert args.log_format == "json"
    assert args.log_level == "DEBUG"


def test_parser_worker_logging_options() -> None:
    args = cli.build_parser().parse_args(["worker", "--log-format", "json"])
    assert args.log_format == "json"
    assert args.log_level == DEFAULT_LOG_LEVEL


def test_parser_hub_metrics_query_token_ok() -> None:
    assert cli.build_parser().parse_args(["hub"]).metrics_query_token_ok is False
    opted = cli.build_parser().parse_args(["hub", "--metrics-query-token-ok"])
    assert opted.metrics_query_token_ok is True


def test_parser_hub_max_unauth_clients() -> None:
    assert cli.build_parser().parse_args(["hub"]).max_unauth_clients is None
    args = cli.build_parser().parse_args(["hub", "--max-unauth-clients", "8"])
    assert args.max_unauth_clients == 8


def test_parser_hub_max_connections_per_host() -> None:
    assert cli.build_parser().parse_args(["hub"]).max_connections_per_host == 0
    args = cli.build_parser().parse_args(["hub", "--max-connections-per-host", "2"])
    assert args.max_connections_per_host == 2


def test_parser_hub_insecure_off_loopback() -> None:
    assert cli.build_parser().parse_args(["hub"]).insecure_off_loopback is False
    opted = cli.build_parser().parse_args(["hub", "--insecure-off-loopback"])
    assert opted.insecure_off_loopback is True
