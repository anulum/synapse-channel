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
import logging
import ssl
import sys
import threading
import time
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

from synapse_channel.cli_processes_runtime import _run
from synapse_channel.core.acl import AclError, load_acl_policy
from synapse_channel.core.aef_legacy_mapping import AEF_MAPPED_EVENT_KINDS
from synapse_channel.core.aef_runtime import (
    AefRuntimeConfig,
    drain_aef_startup_backlog,
    run_aef_outbox_worker,
)
from synapse_channel.core.auth import TokenAuthenticator
from synapse_channel.core.capability_card_history import PersistentCapabilityCardHistory
from synapse_channel.core.capability_card_trust import (
    DEFAULT_CAPABILITY_CARD_CLOCK_SKEW_SECONDS,
    DEFAULT_CAPABILITY_CARD_HISTORY_CAPACITY,
    DEFAULT_CAPABILITY_CARD_HISTORY_RETENTION_SECONDS,
    CapabilityCardTrustBundle,
    CapabilityCardTrustError,
    load_capability_card_trust_bundle,
)
from synapse_channel.core.federation import FederationBundle, bundle_can_authorise
from synapse_channel.core.federation_store import FederationStoreError, bundle_from_store
from synapse_channel.core.federation_wire import FederationWireError, decode_federation_offer
from synapse_channel.core.hub import (
    DEFAULT_MAX_CONNECTIONS_PER_HOST,
    InsecureBindError,
    SynapseHub,
)
from synapse_channel.core.hub_config import HubConfig, config_fingerprint
from synapse_channel.core.hub_exposure import guard_exposure
from synapse_channel.core.identity_binding import IdentityBindingError, load_identity_trust_bundle
from synapse_channel.core.logging_setup import configure_logging
from synapse_channel.core.message_auth import MessageAuthKey
from synapse_channel.core.message_auth_durable import (
    DurableMessageAuthReplayStore,
    SequenceFloorMode,
)
from synapse_channel.core.multihub_watch import MultiHubWatch, parse_watch_peers, parse_watch_pins
from synapse_channel.core.namespace_ownership import NamespaceOwnership
from synapse_channel.core.paranoid import ParanoidModeError, apply_paranoid_hub_profile
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.rate_policy import (
    HubExposurePosture,
    RateLimits,
    decide_auto_rate_policy,
    is_loopback_bind,
)
from synapse_channel.core.ratelimit import RateLimiter
from synapse_channel.core.receipt_signing import (
    ReceiptSigningError,
    load_receipt_signing_key,
)
from synapse_channel.core.role_grants import RoleGrantError, load_role_grants
from synapse_channel.core.secret_files import (
    SecretFileError,
    read_secret_file,
    read_secret_lines,
)
from synapse_channel.core.secure import SecureModeError, apply_secure_hub_profile
from synapse_channel.core.team_secure import TeamSecureModeError, apply_team_secure_hub_profile
from synapse_channel.core.tls import HubTLSConfigError, build_server_ssl_context

_PRECHECK_LOGGER = logging.getLogger(__name__ + ".exposure_precheck")
_PRECHECK_LOGGER.addHandler(logging.NullHandler())
_PRECHECK_LOGGER.propagate = False
"""Silent sink for the pre-store exposure check: serve() owns the warning pass."""

_LOGGER = logging.getLogger(__name__)


def _resolve_max_connections_per_host(raw: int | None) -> int | None:
    """Map CLI ``--max-connections-per-host`` to the hub keyword.

    ``None`` (parser default, before any secure/auto fill) becomes
    :data:`~synapse_channel.core.hub.DEFAULT_MAX_CONNECTIONS_PER_HOST`. ``0`` or
    a negative value disables the per-host cap (``None`` to the hub). A positive
    integer is the enforced ceiling.
    """
    if raw is None:
        return DEFAULT_MAX_CONNECTIONS_PER_HOST
    if raw <= 0:
        return None
    return int(raw)


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


async def _serve_with_aef(
    serve: Callable[[], Coroutine[Any, Any, None]], config: AefRuntimeConfig
) -> None:
    """Run ``serve`` beside a bounded-shutdown, dedicated AEF drain worker."""
    stop = threading.Event()

    def report_error(exc: Exception) -> None:
        _LOGGER.error("AEF outbox drain failed; durable rows remain pending: %s", exc)

    worker = asyncio.create_task(
        asyncio.to_thread(run_aef_outbox_worker, config, stop, on_error=report_error)
    )
    try:
        await serve()
    finally:
        stop.set()
        await worker


def _parse_message_auth_keys(values: list[str]) -> list[MessageAuthKey]:
    """Parse ``KEY_ID:SECRET:SENDER[,SENDER...]`` values from argv or a key file.

    The error names the two flags and the expected shape, never the value, so a
    malformed entry can be reported without echoing the secret beside it.
    """
    malformed = (
        "--message-auth-key / --message-auth-key-file entries must use "
        "KEY_ID:SECRET:SENDER[,SENDER...]"
    )
    keys: list[MessageAuthKey] = []
    for value in values:
        parts = value.split(":", 2)
        if len(parts) != 3:
            raise ValueError(malformed)
        key_id, secret, sender_csv = (part.strip() for part in parts)
        senders = frozenset(sender.strip() for sender in sender_csv.split(",") if sender.strip())
        if not key_id or not secret or not senders:
            raise ValueError(malformed)
        keys.append(MessageAuthKey(key_id=key_id, secret=secret.encode("utf-8"), senders=senders))
    return keys


def _resolve_file_backed_secrets(args: argparse.Namespace) -> None:
    """Fold the owner-only ``*-file`` secret companions into their argv fields.

    An explicit ``--metrics-token`` wins over its file, mirroring the global
    ``--token``/``--token-file`` precedence; ``--message-auth-key-file`` entries
    merge after any argv keys so both sources can rotate together. Runs before
    the hardening presets, so file-delivered material satisfies their presence
    checks exactly as argv material does.
    """
    metrics_token_file = getattr(args, "metrics_token_file", None)
    if metrics_token_file and not args.metrics_token:
        args.metrics_token = read_secret_file(metrics_token_file, flag="--metrics-token-file")
    key_file = getattr(args, "message_auth_key_file", None)
    if key_file:
        entries = read_secret_lines(key_file, flag="--message-auth-key-file")
        args.message_auth_key = [*args.message_auth_key, *entries]


def _hub_multi_seat_intent(args: argparse.Namespace) -> bool:
    """Return whether startup args declare multi-seat / multi-party intent.

    Runtime seat count is not known before clients connect, so multi-seat is an
    *intent* signal: explicit ``--expect-multi-seat``, multi-seat security
    profiles, identity/role material, or private directed routing.
    """
    if bool(getattr(args, "expect_multi_seat", False)):
        return True
    if bool(getattr(args, "team_secure", False) or getattr(args, "secure", False)):
        return True
    if bool(
        getattr(args, "require_role_claim", False)
        or getattr(args, "require_identity_binding", False)
        or getattr(args, "private_directed_messages", False)
    ):
        return True
    identity_trust = str(getattr(args, "identity_trust", "") or "").strip()
    role_grants = str(getattr(args, "role_grants", "") or "").strip()
    return bool(identity_trust or role_grants)


def _hub_bridge_exposed(args: argparse.Namespace) -> bool:
    """Return whether the operator declared an A2A/MCP bridge as exposed.

    Bridges run as separate processes today; the truthful signal is the explicit
    ``--bridge-exposed`` startup flag (operators who front a2a-serve/mcp against
    this hub must set it). No silent false positive on pure loopback single-seat.
    """
    return bool(getattr(args, "bridge_exposed", False))


def _apply_auto_rate_policy(args: argparse.Namespace) -> None:
    """Fill disabled flood limits when the hub starts in an exposed posture (REV-SEC-06).

    Pure decision from :mod:`synapse_channel.core.rate_policy` is applied back onto
    ``args`` so the existing limiter construction path stays unchanged. When
    ``--secure`` is active the decision stands down (secure already normalises
    limits). Local-first loopback single-seat hubs stay unbounded.
    """
    token_configured = bool(args.token or getattr(args, "token_file", None))
    multi_seat = _hub_multi_seat_intent(args)
    bridge_exposed = _hub_bridge_exposed(args)
    posture = HubExposurePosture(
        off_loopback_bind=not is_loopback_bind(args.host),
        token_configured=token_configured,
        bridge_exposed=bridge_exposed,
        multi_seat=multi_seat,
    )
    # Treat an omitted CLI value as disabled for auto-fill decisions so an
    # exposed posture can still inject the secure connection ceiling; the hub
    # kwargs path later resolves a bare default to DEFAULT_MAX_CONNECTIONS_PER_HOST.
    raw_host_cap = getattr(args, "max_connections_per_host", None)
    operator = RateLimits(
        agent_rate=float(args.rate),
        agent_burst=float(args.burst),
        host_rate=float(args.host_rate),
        host_burst=float(args.host_burst),
        max_connections_per_host=(0 if raw_host_cap is None else int(raw_host_cap)),
    )
    decision = decide_auto_rate_policy(
        posture,
        operator,
        secure_mode=bool(getattr(args, "secure", False)),
    )
    if not decision.auto_enabled:
        return
    args.rate = decision.limits.agent_rate
    args.burst = decision.limits.agent_burst
    args.host_rate = decision.limits.host_rate
    args.host_burst = decision.limits.host_burst
    args.max_connections_per_host = decision.limits.max_connections_per_host
    for line in decision.report_lines:
        print(f"synapse hub: {line}", file=sys.stderr)


def _cmd_hub(
    args: argparse.Namespace,
    *,
    runner: Callable[[Coroutine[Any, Any, None]], None] = _run,
    hub_factory: Callable[..., SynapseHub] = SynapseHub,
    store_factory: Callable[..., EventStore] = EventStore,
    replay_store_factory: Callable[..., DurableMessageAuthReplayStore] = (
        DurableMessageAuthReplayStore
    ),
    logging_configurator: Callable[..., object] = configure_logging,
    tls_context_factory: Callable[..., ssl.SSLContext | None] = build_server_ssl_context,
) -> int:
    """Run the coordination hub until interrupted.

    With ``--db`` the hub persists authoritative state to a durable event log and
    resumes from it on restart; without it the hub is purely in-memory. Pair
    ``--db-key-file`` with ``--db`` for SQLCipher page encryption of the store.
    """
    logging_configurator(log_format=args.log_format, level=args.log_level)
    try:
        _resolve_file_backed_secrets(args)
    except SecretFileError as exc:
        print(f"synapse hub: {exc}", file=sys.stderr)
        return 2
    # The secure umbrella composes team-secure and paranoid itself and emits one
    # consolidated report; when it is on, skip the subordinate profile passes so the
    # operator does not see duplicate team-secure and paranoid reports.
    try:
        secure_report = apply_secure_hub_profile(args)
    except SecureModeError as exc:
        print(f"synapse hub: {exc}", file=sys.stderr)
        return 2
    if secure_report is not None:
        for line in secure_report.stderr_lines():
            print(f"synapse hub: {line}", file=sys.stderr)
    else:
        try:
            paranoid_report = apply_paranoid_hub_profile(args)
        except ParanoidModeError as exc:
            print(f"synapse hub: {exc}", file=sys.stderr)
            return 2
        if paranoid_report is not None:
            for line in paranoid_report.stderr_lines():
                print(f"synapse hub: {line}", file=sys.stderr)
        try:
            team_secure_report = apply_team_secure_hub_profile(args)
        except TeamSecureModeError as exc:
            print(f"synapse hub: {exc}", file=sys.stderr)
            return 2
        if team_secure_report is not None:
            for line in team_secure_report.stderr_lines():
                print(f"synapse hub: {line}", file=sys.stderr)
    # REV-SEC-06: after security profiles, fill disabled flood limits on exposed
    # hubs that are not under --secure (which already normalises limits).
    _apply_auto_rate_policy(args)
    try:
        ssl_context = tls_context_factory(certfile=args.tls_certfile, keyfile=args.tls_keyfile)
    except HubTLSConfigError as exc:
        print(f"synapse hub: {exc}", file=sys.stderr)
        return 2
    db_key_file = getattr(args, "db_key_file", None)
    if db_key_file and not args.db:
        print("synapse hub: --db-key-file requires --db", file=sys.stderr)
        return 2
    replay_db_arg = getattr(args, "message_auth_replay_db", None)
    try:
        sequence_floor_mode = SequenceFloorMode(
            getattr(args, "message_auth_sequence_floor_mode", "off")
        )
    except ValueError as exc:
        print(f"synapse hub: {exc}", file=sys.stderr)
        return 2
    if replay_db_arg and not args.require_message_auth:
        print(
            "synapse hub: --message-auth-replay-db requires --require-message-auth",
            file=sys.stderr,
        )
        return 2
    if sequence_floor_mode is not SequenceFloorMode.OFF and not args.require_message_auth:
        print(
            "synapse hub: --message-auth-sequence-floor-mode requires --require-message-auth",
            file=sys.stderr,
        )
        return 2
    message_auth_replay_path = replay_db_arg
    if message_auth_replay_path is None and args.require_message_auth and args.db:
        message_auth_replay_path = f"{args.db}.message-auth.db"
    if sequence_floor_mode is not SequenceFloorMode.OFF and message_auth_replay_path is None:
        print(
            "synapse hub: --message-auth-sequence-floor-mode requires a durable replay "
            "ledger; pass --db or --message-auth-replay-db",
            file=sys.stderr,
        )
        return 2
    if args.require_message_auth and message_auth_replay_path is None:
        print(
            "synapse hub: WARNING --require-message-auth without --db or "
            "--message-auth-replay-db uses a process-local nonce cache; a restart "
            "reopens still-fresh nonces.",
            file=sys.stderr,
        )
    aef_signing_key_path = getattr(args, "aef_signing_key", None)
    if aef_signing_key_path and not args.db:
        print("synapse hub: --aef-signing-key requires --db", file=sys.stderr)
        return 2
    if aef_signing_key_path and not args.hub_id:
        print("synapse hub: --aef-signing-key requires --hub-id", file=sys.stderr)
        return 2
    aef_config: AefRuntimeConfig | None = None
    if aef_signing_key_path:
        try:
            aef_config = AefRuntimeConfig(
                db_path=str(args.db),
                hub_id=str(args.hub_id),
                signing_key=load_receipt_signing_key(aef_signing_key_path),
                db_key_file=db_key_file,
                interval_seconds=float(getattr(args, "aef_drain_interval", 1.0)),
            )
        except (ReceiptSigningError, ValueError) as exc:
            print(f"synapse hub: {exc}", file=sys.stderr)
            return 2
    authenticator = TokenAuthenticator([args.token]) if args.token else None
    # Fail-closed exposure precheck: an insecure non-loopback bind is refused here,
    # BEFORE the durable event store is constructed, so a refused start never leaves
    # a database file on disk. serve() re-runs the same guard at the bind (embedded
    # hubs keep their own guard); warnings are deferred to that pass, so this
    # precheck stays silent and only the refusal surfaces.
    try:
        guard_exposure(
            args.host,
            authenticator=authenticator,
            enable_metrics=args.metrics,
            metrics_token=args.metrics_token,
            metrics_query_token_ok=args.metrics_query_token_ok,
            insecure_off_loopback=args.insecure_off_loopback,
            tls_active=ssl_context is not None,
            logger=_PRECHECK_LOGGER,
        )
    except InsecureBindError as exc:
        print(f"synapse hub: {exc}", file=sys.stderr)
        return 2
    try:
        store_kwargs: dict[str, Any] = {"key_file": db_key_file}
        if aef_config is not None:
            store_kwargs["aef_outbox_kinds"] = AEF_MAPPED_EVENT_KINDS
        journal = store_factory(args.db, **store_kwargs) if args.db else None
    except (ValueError, RuntimeError) as exc:
        # ValueError: bad key file. RuntimeError: SqlCipherUnavailableError /
        # SqlCipherKeyError subclasses for missing driver or rejected key.
        print(f"synapse hub: {exc}", file=sys.stderr)
        return 2
    if aef_config is not None:
        try:
            settled = drain_aef_startup_backlog(aef_config)
        except Exception as exc:  # noqa: BLE001 — startup evidence gate fails closed
            if journal is not None:
                journal.close()
            print(f"synapse hub: AEF startup reconciliation failed: {exc}", file=sys.stderr)
            return 2
        print(
            "synapse hub: native AEF outbox enabled "
            f"(startup_settled={settled}, interval={aef_config.interval_seconds:g}s)",
            file=sys.stderr,
        )
    limiter = RateLimiter(rate_per_second=args.rate, burst=args.burst) if args.rate > 0 else None
    host_limiter = (
        RateLimiter(rate_per_second=args.host_rate, burst=args.host_burst)
        if args.host_rate > 0
        else None
    )
    durable_ingress_quota = None
    ingress_events = int(getattr(args, "durable_ingress_events", 0) or 0)
    ingress_bytes = int(getattr(args, "durable_ingress_bytes", 0) or 0)
    if ingress_events > 0 or ingress_bytes > 0:
        from synapse_channel.core.durable_ingress import (
            DEFAULT_MAX_BYTES,
            DEFAULT_MAX_EVENTS,
            DurableIngressQuota,
        )

        durable_ingress_quota = DurableIngressQuota(
            max_events=ingress_events if ingress_events > 0 else DEFAULT_MAX_EVENTS,
            max_bytes=ingress_bytes if ingress_bytes > 0 else DEFAULT_MAX_BYTES,
            window_seconds=float(getattr(args, "durable_ingress_window", 60.0) or 60.0),
        )
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
    try:
        capability_card_trust_path = getattr(args, "capability_card_trust", "")
        capability_card_history_path = getattr(args, "capability_card_history_db", "")
        history_capacity = getattr(
            args,
            "capability_card_history_capacity",
            DEFAULT_CAPABILITY_CARD_HISTORY_CAPACITY,
        )
        history_retention = getattr(
            args,
            "capability_card_history_retention_seconds",
            DEFAULT_CAPABILITY_CARD_HISTORY_RETENTION_SECONDS,
        )
        if capability_card_history_path and not capability_card_trust_path:
            print(
                "synapse hub: --capability-card-history-db requires "
                "--capability-card-trust; there are no card keys to verify.",
                file=sys.stderr,
            )
            return 2
        capability_card_trust_bundle = (
            load_capability_card_trust_bundle(
                capability_card_trust_path,
                clock_skew_seconds=getattr(
                    args,
                    "capability_card_clock_skew_seconds",
                    DEFAULT_CAPABILITY_CARD_CLOCK_SKEW_SECONDS,
                ),
                history_capacity=history_capacity,
                history_retention_seconds=history_retention,
            )
            if capability_card_trust_path
            else None
        )
        if capability_card_trust_bundle is not None and capability_card_history_path:
            capability_card_trust_bundle = CapabilityCardTrustBundle(
                keys=capability_card_trust_bundle.keys,
                history=PersistentCapabilityCardHistory(
                    capability_card_history_path,
                    max_entries=history_capacity,
                    retention_seconds=history_retention,
                ),
                clock_skew_seconds=capability_card_trust_bundle.clock_skew_seconds,
            )
    except CapabilityCardTrustError as exc:
        print(f"synapse hub: {exc}", file=sys.stderr)
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
            watch_peers = parse_watch_peers(args.multihub_watch)
            watch = MultiHubWatch(
                watch_peers,
                local_id=args.hub_id,
                token=args.multihub_watch_token,
                interval=args.multihub_watch_interval,
                pins=parse_watch_pins(args.multihub_watch_pin, watch_peers),
                namespace_ownership=namespace_ownership,
                journal=journal,
            )
    except ValueError as exc:
        print(f"synapse hub: {exc}", file=sys.stderr)
        return 2
    message_auth_replay_store: DurableMessageAuthReplayStore | None = None
    if message_auth_replay_path is not None:
        try:
            message_auth_replay_store = replay_store_factory(
                message_auth_replay_path,
                max_entries=args.message_auth_replay_capacity,
                window_seconds=args.message_auth_window_seconds,
                key_file=db_key_file,
            )
        except Exception as exc:  # noqa: BLE001 — security store startup fails closed
            if journal is not None:
                journal.close()
            print(f"synapse hub: cannot open message-auth replay ledger: {exc}", file=sys.stderr)
            return 2
        print(
            "synapse hub: durable message-auth replay enabled "
            f"(sequence_floor={sequence_floor_mode.value})",
            file=sys.stderr,
        )
    hub_kwargs: dict[str, Any] = {
        "journal": journal,
        "rate_limiter": limiter,
        "host_rate_limiter": host_limiter,
        "durable_ingress_quota": durable_ingress_quota,
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
        "max_connections_per_host": _resolve_max_connections_per_host(
            getattr(args, "max_connections_per_host", None)
        ),
        "max_msg_bytes": args.max_msg_kb * 1024,
        "max_claims_per_agent": args.max_claims_per_agent,
        "max_offers_per_agent": args.max_offers_per_agent,
        "max_paths_per_claim": args.max_paths_per_claim,
        "compact_hint_threshold": args.compact_hint_threshold,
        "takeover_cooldown": args.takeover_cooldown,
        "lease_offline_ttl": args.lease_offline_ttl,
        "shutdown_close_timeout": args.shutdown_close_timeout,
        "enable_metrics": args.metrics,
        "auth_timeout": args.auth_timeout,
        "metrics_token": args.metrics_token,
        "metrics_query_token_ok": args.metrics_query_token_ok,
        "allowed_origins": tuple(getattr(args, "allow_origin", ()) or ()),
        "advertised_host": getattr(args, "advertised_host", None) or None,
        "per_message_auth_keys": message_auth_keys,
        "require_per_message_auth": args.require_message_auth,
        "per_message_auth_window_seconds": args.message_auth_window_seconds,
        "per_message_auth_replay_capacity": args.message_auth_replay_capacity,
        "per_message_auth_replay_store": message_auth_replay_store,
        "per_message_auth_sequence_floor_mode": sequence_floor_mode,
        "capability_card_trust_bundle": capability_card_trust_bundle,
        "acl_policy": acl_policy,
        "require_acl": args.require_acl,
        "role_grants": role_grants,
        "require_role_claim": args.require_role_claim,
        "identity_trust_bundle": identity_trust_bundle,
        "require_identity_binding": args.require_identity_binding,
        "identity_pin_path": args.identity_pins or None,
        "private_directed_messages": args.private_directed_messages,
        "warn_stale_recipients": args.warn_stale_recipients,
        "recipient_liveness_window": args.recipient_liveness_window,
        "waiter_liveness_window": args.waiter_liveness_window,
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

    server_factory = serve
    if watch is not None:
        active_watch = watch

        def watched_server() -> Coroutine[Any, Any, None]:
            return _serve_with_watch(serve, active_watch)

        server_factory = watched_server
    try:
        runner(
            server_factory() if aef_config is None else _serve_with_aef(server_factory, aef_config)
        )
    except InsecureBindError as exc:
        print(f"synapse hub: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nHub stopped by user.")
    finally:
        if message_auth_replay_store is not None:
            message_auth_replay_store.close()
        if journal is not None:
            journal.close()
    return 0
