# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — process CLI hub command
"""Validate hub transport exposure before durable allocation."""

from __future__ import annotations

import logging
import sys
from dataclasses import replace

from synapse_channel.cli_hub_journal import configure_aef_outbox, configure_replay_contract
from synapse_channel.cli_hub_profiles import _hub_bridge_exposed, _hub_multi_seat_intent
from synapse_channel.cli_hub_startup import HubStartup, HubStartupRefused
from synapse_channel.core.at_rest_guard import AtRestBindError, guard_at_rest
from synapse_channel.core.auth import TokenAuthenticator
from synapse_channel.core.hub import InsecureBindError
from synapse_channel.core.hub_exposure import guard_exposure
from synapse_channel.core.multihub_serving_config import (
    MultiHubServingConfigError,
    load_multihub_serving_config,
)
from synapse_channel.core.persistence_sqlcipher import sqlcipher_available
from synapse_channel.core.spend_ledger import SpendLedger, SpendLedgerError
from synapse_channel.core.tls import HubTLSConfigError
from synapse_channel.core.unbound_identity_guard import refusal_message, unbound_identity_problem

_PRECHECK_LOGGER = logging.getLogger(__name__ + ".exposure_precheck")
_PRECHECK_LOGGER.addHandler(logging.NullHandler())
_PRECHECK_LOGGER.propagate = False


def configure_serving_policy(ctx: HubStartup) -> None:
    """Load serving grants and verify certificate-pin support."""
    ctx.transport.serving = None
    serving_policy_path = getattr(ctx.args, "multihub_serving_policy", "")
    if serving_policy_path:
        try:
            ctx.transport.serving = load_multihub_serving_config(serving_policy_path)
            ctx.factories.certificate_pin_support()
        except (MultiHubServingConfigError, HubTLSConfigError) as exc:
            print(f"synapse hub: {exc}", file=sys.stderr)
            raise HubStartupRefused() from None
    if ctx.transport.serving is not None:
        ctx.config = replace(
            ctx.config,
            multihub=replace(
                ctx.config.multihub, multihub_serving_policy=ctx.transport.serving.policy
            ),
        )


def configure_spend_ledger(ctx: HubStartup) -> None:
    """Load the owner-bound spend ledger only under an explicit serving policy."""
    ctx.config = replace(ctx.config, multihub=replace(ctx.config.multihub, spend_ledger=None))
    spend_ledger_path = getattr(ctx.args, "spend_ledger", "")
    if spend_ledger_path:
        if ctx.transport.serving is None or not ctx.args.hub_id:
            print(
                "synapse hub: --spend-ledger requires --hub-id and --multihub-serving-policy",
                file=sys.stderr,
            )
            raise HubStartupRefused() from None
        try:
            ctx.config = replace(
                ctx.config,
                multihub=replace(
                    ctx.config.multihub,
                    spend_ledger=SpendLedger(spend_ledger_path, owner_hub_id=ctx.args.hub_id),
                ),
            )
        except SpendLedgerError as exc:
            print(f"synapse hub: {exc}", file=sys.stderr)
            raise HubStartupRefused() from None


def configure_server_tls(ctx: HubStartup) -> None:
    """Build the server TLS context with the configured serving-side client CA."""
    try:
        if ctx.transport.serving is None:
            ctx.transport.ssl_context = ctx.factories.tls(
                certfile=ctx.args.tls_certfile, keyfile=ctx.args.tls_keyfile
            )
        else:
            ctx.transport.ssl_context = ctx.factories.tls(
                certfile=ctx.args.tls_certfile,
                keyfile=ctx.args.tls_keyfile,
                client_ca_data=ctx.transport.serving.client_ca_data,
            )
    except HubTLSConfigError as exc:
        print(f"synapse hub: {exc}", file=sys.stderr)
        raise HubStartupRefused() from None


def guard_transport_exposure(ctx: HubStartup) -> None:
    """Refuse unsafe transport exposure before opening a durable store."""
    ctx.config = replace(
        ctx.config,
        auth=replace(
            ctx.config.auth,
            authenticator=TokenAuthenticator([ctx.args.token]) if ctx.args.token else None,
        ),
    )
    try:
        guard_exposure(
            ctx.args.host,
            authenticator=ctx.config.auth.authenticator,
            enable_metrics=ctx.args.metrics,
            metrics_token=ctx.args.metrics_token,
            metrics_query_token_ok=ctx.args.metrics_query_token_ok,
            insecure_off_loopback=ctx.args.insecure_off_loopback,
            tls_active=ctx.transport.ssl_context is not None,
            logger=_PRECHECK_LOGGER,
        )
    except InsecureBindError as exc:
        print(f"synapse hub: {exc}", file=sys.stderr)
        raise HubStartupRefused() from None


def guard_storage_exposure(ctx: HubStartup) -> None:
    """Refuse unsafe at-rest exposure before opening a durable store."""
    try:
        guard_at_rest(
            ctx.args.host,
            db=ctx.args.db,
            encrypted=bool(ctx.replay.db_key_file),
            insecure_plaintext_at_rest=getattr(ctx.args, "insecure_plaintext_at_rest", False),
            sqlcipher_available=sqlcipher_available(),
            logger=_PRECHECK_LOGGER,
        )
    except AtRestBindError as exc:
        print(f"synapse hub: {exc}", file=sys.stderr)
        raise HubStartupRefused() from None


def guard_declared_identity(ctx: HubStartup) -> None:
    """Refuse unbound multi-seat or exposed identity before allocation."""
    identity_problem = unbound_identity_problem(
        ctx.args.host,
        declared_multi_seat=_hub_multi_seat_intent(ctx.args) or _hub_bridge_exposed(ctx.args),
        identity_bound=bool(ctx.args.require_identity_binding),
    )
    if identity_problem is not None:
        if not getattr(ctx.args, "insecure_unbound_identity", False):
            print(f"synapse hub: {refusal_message(identity_problem)}", file=sys.stderr)
            raise HubStartupRefused() from None
        print(f"synapse hub: WARNING Synapse Hub {identity_problem}.", file=sys.stderr)


def prepare_hub_transport(ctx: HubStartup) -> None:
    """Validate serving, transport, durable prerequisites and exposure before allocation."""
    configure_serving_policy(ctx)
    configure_spend_ledger(ctx)
    configure_server_tls(ctx)
    configure_replay_contract(ctx)
    configure_aef_outbox(ctx)
    guard_transport_exposure(ctx)
    guard_storage_exposure(ctx)
    guard_declared_identity(ctx)
