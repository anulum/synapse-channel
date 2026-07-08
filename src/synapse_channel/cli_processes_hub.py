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
import asyncio
import contextlib
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
from synapse_channel.core.hub_config import HubConfig, config_fingerprint
from synapse_channel.core.identity_binding import IdentityBindingError, load_identity_trust_bundle
from synapse_channel.core.logging_setup import configure_logging
from synapse_channel.core.message_auth import MessageAuthKey
from synapse_channel.core.multihub_watch import MultiHubWatch, parse_watch_peers
from synapse_channel.core.namespace_ownership import NamespaceOwnership
from synapse_channel.core.paranoid import ParanoidModeError, apply_paranoid_hub_profile
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.ratelimit import RateLimiter
from synapse_channel.core.role_grants import RoleGrantError, load_role_grants
from synapse_channel.core.tls import HubTLSConfigError, build_server_ssl_context


def _parse_namespace_owners(values: list[str]) -> dict[str, str]:
    """Parse repeatable ``NS=HUB_ID`` CLI values into an ownership map."""
    owners: dict[str, str] = {}
    for value in values:
        namespace, sep, hub_id = value.partition("=")
        namespace, hub_id = namespace.strip(), hub_id.strip()
        if not sep or not namespace or not hub_id:
            raise ValueError(f"--namespace-owner must use NS=HUB_ID, got {value!r}")
        if namespace in owners:
            raise ValueError(f"--namespace-owner names namespace {namespace!r} twice")
        owners[namespace] = hub_id
    return owners


async def _serve_with_watch(
    serve: Callable[[], Coroutine[Any, Any, None]], watch: MultiHubWatch
) -> None:
    """Run the hub server with the multihub watch polling alongside it.

    The watch task lives exactly as long as the server: it starts when serving starts and
    is cancelled (and awaited) when serving ends, so no poll outlives the hub. ``serve``
    is a factory rather than a coroutine so a runner that never awaits this wrapper leaves
    no orphaned server coroutine behind.
    """
    task = asyncio.create_task(watch.run())
    try:
        await serve()
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


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
    try:
        role_grants = load_role_grants(args.role_grants) if args.role_grants else None
    except RoleGrantError as exc:
        print(f"synapse hub: {exc}", file=sys.stderr)
        return 2
    if args.require_role_claim and authenticator is None:
        print(
            "synapse hub: WARNING --require-role-claim without --token gates on a "
            "self-reported identity; role grants give no protection on an unauthenticated "
            "hub. Pair --require-role-claim with --token, and with --require-message-auth so "
            "the identity is cryptographically bound, before relying on enforcement.",
            file=sys.stderr,
        )
    try:
        identity_trust_bundle = (
            load_identity_trust_bundle(args.identity_trust) if args.identity_trust else None
        )
    except IdentityBindingError as exc:
        print(f"synapse hub: {exc}", file=sys.stderr)
        return 2
    if args.require_identity_binding and identity_trust_bundle is None:
        print(
            "synapse hub: --require-identity-binding requires --identity-trust; there is no "
            "identity key material to verify a registration against.",
            file=sys.stderr,
        )
        return 2
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
    if args.namespace_owner and not args.hub_id:
        print(
            "synapse hub: --namespace-owner requires --hub-id; the ownership map "
            "compares each namespace's owner against this hub's own stable id.",
            file=sys.stderr,
        )
        return 2
    if args.multihub_watch and not args.namespace_owner:
        print(
            "synapse hub: --multihub-watch requires --namespace-owner; the watch feeds "
            "partition detection, and without an ownership map there is nothing to "
            "detect against.",
            file=sys.stderr,
        )
        return 2
    namespace_ownership: NamespaceOwnership | None = None
    watch: MultiHubWatch | None = None
    try:
        if args.namespace_owner:
            namespace_ownership = NamespaceOwnership(
                owners=_parse_namespace_owners(args.namespace_owner),
                local_hub_id=args.hub_id,
            )
        if args.multihub_watch:
            watch = MultiHubWatch(
                parse_watch_peers(args.multihub_watch),
                local_id=args.hub_id,
                token=args.multihub_watch_token,
                interval=args.multihub_watch_interval,
            )
    except ValueError as exc:
        print(f"synapse hub: {exc}", file=sys.stderr)
        return 2
    hub_kwargs: dict[str, Any] = {
        "journal": journal,
        "rate_limiter": limiter,
        "host_rate_limiter": host_limiter,
        "max_history": args.max_history,
        "max_progress": args.max_progress,
        "max_progress_per_author": args.max_progress_per_author,
        "max_progress_per_task": args.max_progress_per_task,
        "board_task_cap": args.board_task_cap,
        "max_findings_per_agent": args.max_findings_per_agent,
        "relay_log": args.relay_log,
        "relay_max_lines": args.relay_max_lines,
        "authenticator": authenticator,
        "max_clients": args.max_clients,
        "max_unauth_clients": args.max_unauth_clients,
        "max_connections_per_host": (
            args.max_connections_per_host if args.max_connections_per_host > 0 else None
        ),
        "max_msg_bytes": args.max_msg_kb * 1024,
        "max_claims_per_agent": args.max_claims_per_agent,
        "max_offers_per_agent": args.max_offers_per_agent,
        "max_paths_per_claim": args.max_paths_per_claim,
        "compact_hint_threshold": args.compact_hint_threshold,
        "takeover_cooldown": args.takeover_cooldown,
        "shutdown_close_timeout": args.shutdown_close_timeout,
        "enable_metrics": args.metrics,
        "auth_timeout": args.auth_timeout,
        "metrics_token": args.metrics_token,
        "metrics_query_token_ok": args.metrics_query_token_ok,
        "per_message_auth_keys": message_auth_keys,
        "require_per_message_auth": args.require_message_auth,
        "per_message_auth_window_seconds": args.message_auth_window_seconds,
        "per_message_auth_replay_capacity": args.message_auth_replay_capacity,
        "acl_policy": acl_policy,
        "require_acl": args.require_acl,
        "role_grants": role_grants,
        "require_role_claim": args.require_role_claim,
        "identity_trust_bundle": identity_trust_bundle,
        "require_identity_binding": args.require_identity_binding,
        "private_directed_messages": args.private_directed_messages,
        "federation_bundle": federation_bundle,
        "federation_offer_path": args.federation_offer or None,
        "hub_id": args.hub_id,
        "namespace_ownership": namespace_ownership,
        "observed_asserting_hubs": (watch.observed_asserting_hubs if watch is not None else None),
        "insecure_off_loopback": args.insecure_off_loopback,
    }
    hub = hub_factory(**hub_kwargs)
    # Direct SynapseHub(...) construction does not run from_config, so config_epoch
    # would stay empty and the hub's pinning indicator inert. Regroup the flat kwargs
    # and fingerprint the posture, so /health, the who snapshot, and /snapshot.json
    # report the same config_epoch a from_config hub would.
    hub.config_epoch = config_fingerprint(HubConfig.from_kwargs(hub_kwargs))

    def serve() -> Coroutine[Any, Any, None]:
        return hub.serve(host=args.host, port=args.port, ssl_context=ssl_context)

    try:
        runner(serve() if watch is None else _serve_with_watch(serve, watch))
    except InsecureBindError as exc:
        print(f"synapse hub: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nHub stopped by user.")
    finally:
        if journal is not None:
            journal.close()
    return 0
