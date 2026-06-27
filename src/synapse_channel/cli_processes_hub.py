# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — process CLI hub command
"""Hub process command for the ``synapse`` CLI."""

from __future__ import annotations

import argparse
import ssl
import sys
from collections.abc import Callable, Coroutine
from typing import Any

from synapse_channel.cli_processes_runtime import _run
from synapse_channel.core.auth import TokenAuthenticator
from synapse_channel.core.hub import InsecureBindError, SynapseHub
from synapse_channel.core.logging_setup import configure_logging
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.ratelimit import RateLimiter
from synapse_channel.core.tls import HubTLSConfigError, build_server_ssl_context


def _cmd_hub(
    args: argparse.Namespace,
    *,
    runner: Callable[[Coroutine[Any, Any, None]], None] = _run,
    hub_factory: Callable[..., SynapseHub] = SynapseHub,
    store_factory: Callable[[str], EventStore] = EventStore,
    logging_configurator: Callable[..., object] = configure_logging,
    tls_context_factory: Callable[..., ssl.SSLContext | None] = build_server_ssl_context,
) -> int:
    """Run the coordination hub until interrupted.

    With ``--db`` the hub persists authoritative state to a durable event log and
    resumes from it on restart; without it the hub is purely in-memory.
    """
    logging_configurator(log_format=args.log_format, level=args.log_level)
    try:
        ssl_context = tls_context_factory(certfile=args.tls_certfile, keyfile=args.tls_keyfile)
    except HubTLSConfigError as exc:
        print(f"synapse hub: {exc}", file=sys.stderr)
        return 2
    journal = store_factory(args.db) if args.db else None
    limiter = RateLimiter(rate_per_second=args.rate, burst=args.burst) if args.rate > 0 else None
    host_limiter = (
        RateLimiter(rate_per_second=args.host_rate, burst=args.host_burst)
        if args.host_rate > 0
        else None
    )
    authenticator = TokenAuthenticator([args.token]) if args.token else None
    hub = hub_factory(
        journal=journal,
        rate_limiter=limiter,
        host_rate_limiter=host_limiter,
        max_history=args.max_history,
        relay_log=args.relay_log,
        relay_max_lines=args.relay_max_lines,
        authenticator=authenticator,
        max_clients=args.max_clients,
        max_unauth_clients=args.max_unauth_clients,
        max_connections_per_host=(
            args.max_connections_per_host if args.max_connections_per_host > 0 else None
        ),
        max_msg_bytes=args.max_msg_kb * 1024,
        max_claims_per_agent=args.max_claims_per_agent,
        max_offers_per_agent=args.max_offers_per_agent,
        max_paths_per_claim=args.max_paths_per_claim,
        compact_hint_threshold=args.compact_hint_threshold,
        takeover_cooldown=args.takeover_cooldown,
        shutdown_close_timeout=args.shutdown_close_timeout,
        enable_metrics=args.metrics,
        auth_timeout=args.auth_timeout,
        metrics_token=args.metrics_token,
        metrics_query_token_ok=args.metrics_query_token_ok,
        insecure_off_loopback=args.insecure_off_loopback,
    )
    try:
        runner(hub.serve(host=args.host, port=args.port, ssl_context=ssl_context))
    except InsecureBindError as exc:
        print(f"synapse hub: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nHub stopped by user.")
    finally:
        if journal is not None:
            journal.close()
    return 0
