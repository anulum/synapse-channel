# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the long-running process commands (hub/worker/team/supervisor)

from __future__ import annotations

from synapse_channel import cli, cli_processes
from synapse_channel.core.agent_liveness import (
    DEFAULT_RECIPIENT_LIVENESS_WINDOW,
    DEFAULT_WAITER_LIVENESS_WINDOW,
    DEFAULT_WARN_STALE_RECIPIENTS,
)
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
    assert args.db_key_file is None
    assert args.func is cli_processes._cmd_hub
    assert args.role_grants == ""
    assert args.require_role_claim is False


def test_parser_hub_db_key_file_flag() -> None:
    """Production hub CLI exposes --db-key-file next to --db."""
    args = cli.build_parser().parse_args(
        ["hub", "--db", "/tmp/hub.db", "--db-key-file", "/tmp/hub.key"]
    )
    assert args.db == "/tmp/hub.db"
    assert args.db_key_file == "/tmp/hub.key"


def test_parser_hub_role_claim_flags() -> None:
    args = cli.build_parser().parse_args(
        ["hub", "--role-grants", "/tmp/rg.json", "--require-role-claim"]
    )
    assert args.role_grants == "/tmp/rg.json"
    assert args.require_role_claim is True


def test_parser_hub_identity_binding_flags() -> None:
    defaults = cli.build_parser().parse_args(["hub"])
    assert defaults.identity_trust == ""
    assert defaults.require_identity_binding is False

    args = cli.build_parser().parse_args(
        ["hub", "--identity-trust", "/tmp/id.json", "--require-identity-binding"]
    )
    assert args.identity_trust == "/tmp/id.json"
    assert args.require_identity_binding is True


def test_parser_hub_private_directed_messages_flag() -> None:
    assert cli.build_parser().parse_args(["hub"]).private_directed_messages is False
    args = cli.build_parser().parse_args(["hub", "--private-directed-messages"])
    assert args.private_directed_messages is True


def test_parser_hub_stale_recipient_warning_flags() -> None:
    defaults = cli.build_parser().parse_args(["hub"])
    assert defaults.warn_stale_recipients is DEFAULT_WARN_STALE_RECIPIENTS
    assert defaults.recipient_liveness_window == DEFAULT_RECIPIENT_LIVENESS_WINDOW
    assert defaults.waiter_liveness_window == DEFAULT_WAITER_LIVENESS_WINDOW

    args = cli.build_parser().parse_args(
        [
            "hub",
            "--warn-stale-recipients",
            "--recipient-liveness-window",
            "45",
            "--waiter-liveness-window",
            "12",
        ]
    )
    assert args.warn_stale_recipients is True
    assert args.recipient_liveness_window == 45.0
    assert args.waiter_liveness_window == 12.0

    opt_out = cli.build_parser().parse_args(["hub", "--no-warn-stale-recipients"])
    assert opt_out.warn_stale_recipients is False


def test_parser_worker_custom() -> None:
    args = cli.build_parser().parse_args(
        ["worker", "--name", "REASON", "--provider", "rule", "--min-reply-interval", "1.5"]
    )
    assert args.name == "REASON"
    assert args.provider == "rule"
    assert args.min_reply_interval == 1.5


def test_parser_supervisor() -> None:
    args = cli.build_parser().parse_args(
        [
            "supervisor",
            "--idle-seconds",
            "60",
            "--interval",
            "5",
            "--no-predictive-stall",
            "--history-multiplier",
            "4",
            "--min-history-samples",
            "5",
            "--min-predictive-idle-seconds",
            "45",
        ]
    )
    assert args.idle_seconds == 60.0
    assert args.interval == 5.0
    assert args.predictive_stall is False
    assert args.history_multiplier == 4.0
    assert args.min_history_samples == 5
    assert args.min_predictive_idle_seconds == 45.0
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


def test_parser_hub_blackboard_and_memory_quotas() -> None:
    defaults = cli.build_parser().parse_args(["hub"])
    assert defaults.max_progress == 5000
    assert defaults.max_progress_per_author == 1000
    assert defaults.max_progress_per_task == 1000
    assert defaults.max_findings_per_agent == 512
    assert defaults.board_task_cap is None

    args = cli.build_parser().parse_args(
        [
            "hub",
            "--max-progress",
            "99",
            "--max-progress-per-author",
            "7",
            "--max-progress-per-task",
            "8",
            "--max-findings-per-agent",
            "9",
            "--board-task-cap",
            "500",
        ]
    )
    assert args.board_task_cap == 500
    assert args.max_progress == 99
    assert args.max_progress_per_author == 7
    assert args.max_progress_per_task == 8
    assert args.max_findings_per_agent == 9


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


def test_parser_hub_message_authentication_options() -> None:
    defaults = cli.build_parser().parse_args(["hub"])
    assert defaults.message_auth_key == []
    assert defaults.require_message_auth is False
    assert defaults.message_auth_window_seconds == 10.0
    assert defaults.message_auth_replay_capacity == 4096

    args = cli.build_parser().parse_args(
        [
            "hub",
            "--message-auth-key",
            "main:shared-secret:ALPHA",
            "--require-message-auth",
            "--message-auth-window-seconds",
            "12.5",
            "--message-auth-replay-capacity",
            "99",
        ]
    )
    assert args.message_auth_key == ["main:shared-secret:ALPHA"]
    assert args.require_message_auth is True
    assert args.message_auth_window_seconds == 12.5
    assert args.message_auth_replay_capacity == 99


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


def test_parser_hub_paranoid() -> None:
    assert cli.build_parser().parse_args(["hub"]).paranoid is False
    opted = cli.build_parser().parse_args(["hub", "--paranoid"])
    assert opted.paranoid is True


def test_parser_hub_tls_certificate_chain() -> None:
    args = cli.build_parser().parse_args(
        ["hub", "--tls-certfile", "cert.pem", "--tls-keyfile", "key.pem"]
    )

    assert args.tls_certfile == "cert.pem"
    assert args.tls_keyfile == "key.pem"
