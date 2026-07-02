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
import json
import ssl
import sys
import time
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

from synapse_channel.cli_processes_runtime import _run
from synapse_channel.core.acl import AclError, load_acl_policy
from synapse_channel.core.auth import TokenAuthenticator
from synapse_channel.core.federation import FederationBundle, bundle_can_authorise
from synapse_channel.core.federation_store import FederationStoreError, bundle_from_store
from synapse_channel.core.federation_wire import FederationWireError, decode_federation_offer
from synapse_channel.core.hub import InsecureBindError, SynapseHub
from synapse_channel.core.logging_setup import configure_logging
from synapse_channel.core.message_auth import MessageAuthKey
from synapse_channel.core.paranoid import ParanoidModeError, apply_paranoid_hub_profile
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.ratelimit import RateLimiter
from synapse_channel.core.tls import HubTLSConfigError, build_server_ssl_context


def _parse_message_auth_keys(values: list[str]) -> list[MessageAuthKey]:
    """Parse ``KEY_ID:SECRET:SENDER[,SENDER...]`` CLI values."""
    keys: list[MessageAuthKey] = []
    for value in values:
        parts = value.split(":", 2)
        if len(parts) != 3:
            raise ValueError("--message-auth-key must use KEY_ID:SECRET:SENDER[,SENDER...]")
        key_id, secret, sender_csv = (part.strip() for part in parts)
        senders = frozenset(sender.strip() for sender in sender_csv.split(",") if sender.strip())
        if not key_id or not secret or not senders:
            raise ValueError("--message-auth-key must use KEY_ID:SECRET:SENDER[,SENDER...]")
        keys.append(MessageAuthKey(key_id=key_id, secret=secret.encode("utf-8"), senders=senders))
    return keys


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
        paranoid_report = apply_paranoid_hub_profile(args)
    except ParanoidModeError as exc:
        print(f"synapse hub: {exc}", file=sys.stderr)
        return 2
    if paranoid_report is not None:
        for line in paranoid_report.stderr_lines():
            print(f"synapse hub: {line}", file=sys.stderr)
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
    try:
        message_auth_keys = _parse_message_auth_keys(args.message_auth_key)
    except ValueError as exc:
        print(f"synapse hub: {exc}", file=sys.stderr)
        return 2
    try:
        acl_policy = load_acl_policy(args.acl_policy) if args.acl_policy else None
    except AclError as exc:
        print(f"synapse hub: {exc}", file=sys.stderr)
        return 2
    if args.require_acl and authenticator is None:
        print(
            "synapse hub: WARNING --require-acl without --token authorises a self-reported "
            "sender; namespace ACL rules give no protection on an unauthenticated hub. "
            "Pair --require-acl with --token, and with --require-message-auth so the sender "
            "is cryptographically bound, before relying on enforcement.",
            file=sys.stderr,
        )
    federation_bundle: FederationBundle | None = None
    if args.federation_observe_only and not args.federation_store:
        print(
            "synapse hub: --federation-observe-only requires --federation-store; there "
            "is no peering to observe.",
            file=sys.stderr,
        )
        return 2
    if args.federation_observe_only and args.require_message_auth:
        print(
            "synapse hub: --federation-observe-only contradicts --require-message-auth; "
            "per-message authentication would enforce the peerings this flag declares "
            "unenforced. Drop one of the two.",
            file=sys.stderr,
        )
        return 2
    if args.federation_store:
        try:
            federation_bundle = bundle_from_store(args.federation_store)
        except FederationStoreError as exc:
            print(f"synapse hub: {exc}", file=sys.stderr)
            return 2
        if not args.require_message_auth:
            if args.federation_observe_only:
                print(
                    "synapse hub: federation store loaded observe-only; every "
                    "cross-domain frame is refused deny-closed.",
                    file=sys.stderr,
                )
            elif bundle_can_authorise(federation_bundle, now=time.time()):
                print(
                    "synapse hub: --federation-store grants cross-domain scope but "
                    "--require-message-auth is not set; no signing key is ever verified, "
                    "so no cross-domain frame can be honoured and the granted scope is "
                    "unenforceable. Start with --require-message-auth to enforce "
                    "federation, or declare --federation-observe-only to load the store "
                    "for diagnostics and deny-closed refusal only.",
                    file=sys.stderr,
                )
                return 2
            else:
                print(
                    "synapse hub: WARNING --federation-store without --require-message-auth "
                    "authorises no cross-domain frame; a peered domain's frame can only be "
                    "honoured when per-message authentication binds its signing key. Pair "
                    "--federation-store with --require-message-auth to enforce federation.",
                    file=sys.stderr,
                )
    if args.federation_offer:
        try:
            offered = json.loads(Path(args.federation_offer).read_text(encoding="utf-8"))
            decode_federation_offer(offered)
        except (OSError, json.JSONDecodeError, FederationWireError) as exc:
            print(f"synapse hub: cannot serve --federation-offer: {exc}", file=sys.stderr)
            return 2
    hub = hub_factory(
        journal=journal,
        rate_limiter=limiter,
        host_rate_limiter=host_limiter,
        max_history=args.max_history,
        max_progress=args.max_progress,
        max_progress_per_author=args.max_progress_per_author,
        max_progress_per_task=args.max_progress_per_task,
        max_findings_per_agent=args.max_findings_per_agent,
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
        per_message_auth_keys=message_auth_keys,
        require_per_message_auth=args.require_message_auth,
        per_message_auth_window_seconds=args.message_auth_window_seconds,
        per_message_auth_replay_capacity=args.message_auth_replay_capacity,
        acl_policy=acl_policy,
        require_acl=args.require_acl,
        federation_bundle=federation_bundle,
        federation_offer_path=args.federation_offer or None,
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
