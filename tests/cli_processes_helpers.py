# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the long-running process commands (hub/worker/team/supervisor)

from __future__ import annotations

import argparse
from typing import Any

from synapse_channel.client.agent import DEFAULT_HUB_URI
from synapse_channel.client.llm_worker import DEFAULT_OLLAMA_BASE_URL
from synapse_channel.core.hub import (
    DEFAULT_COMPACT_HINT_THRESHOLD,
    DEFAULT_SHUTDOWN_CLOSE_TIMEOUT,
    DEFAULT_TAKEOVER_COOLDOWN,
)
from synapse_channel.core.logging_setup import DEFAULT_LOG_FORMAT, DEFAULT_LOG_LEVEL
from synapse_channel.core.scoping import MAX_DECLARED_PATHS

# --- parser ------------------------------------------------------------------


def _hub_ns(**overrides: Any) -> argparse.Namespace:
    base: dict[str, Any] = {
        "host": "localhost",
        "port": 8876,
        "db": None,
        "rate": 0.0,
        "burst": 20.0,
        "host_rate": 0.0,
        "host_burst": 40.0,
        "max_history": 10000,
        "max_progress": 5000,
        "max_progress_per_author": 1000,
        "max_progress_per_task": 1000,
        "board_task_cap": None,
        "max_findings_per_agent": 512,
        "relay_log": None,
        "relay_max_lines": 5000,
        "max_clients": 64,
        "max_unauth_clients": None,
        "max_connections_per_host": 0,
        "max_msg_kb": 1024,
        "max_claims_per_agent": 128,
        "max_offers_per_agent": 64,
        "max_paths_per_claim": MAX_DECLARED_PATHS,
        "compact_hint_threshold": DEFAULT_COMPACT_HINT_THRESHOLD,
        "shutdown_close_timeout": DEFAULT_SHUTDOWN_CLOSE_TIMEOUT,
        "takeover_cooldown": DEFAULT_TAKEOVER_COOLDOWN,
        "log_format": DEFAULT_LOG_FORMAT,
        "log_level": DEFAULT_LOG_LEVEL,
        "token": None,
        "metrics": False,
        "auth_timeout": 10.0,
        "metrics_token": None,
        "metrics_query_token_ok": False,
        "message_auth_key": [],
        "require_message_auth": False,
        "message_auth_window_seconds": 10.0,
        "message_auth_replay_capacity": 4096,
        "acl_policy": "",
        "require_acl": False,
        "role_grants": "",
        "require_role_claim": False,
        "federation_store": "",
        "federation_offer": "",
        "federation_observe_only": False,
        "hub_id": None,
        "namespace_owner": [],
        "multihub_watch": [],
        "multihub_watch_interval": 30.0,
        "multihub_watch_token": None,
        "insecure_off_loopback": False,
        "tls_certfile": None,
        "tls_keyfile": None,
        "paranoid": False,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def _worker_ns(**overrides: Any) -> argparse.Namespace:
    base: dict[str, Any] = {
        "name": "FAST",
        "prefix": "",
        "uri": DEFAULT_HUB_URI,
        "provider": "rule",
        "model": "llama3",
        "base_url": DEFAULT_OLLAMA_BASE_URL,
        "api_key_env": "OPENAI_API_KEY",
        "max_context": 8,
        "reply_target_mode": "all",
        "min_reply_interval": 0.7,
        "token": None,
        "task_class": None,
        "heavy_model": "",
        "log_format": DEFAULT_LOG_FORMAT,
        "log_level": DEFAULT_LOG_LEVEL,
    }
    base.update(overrides)
    return argparse.Namespace(**base)
