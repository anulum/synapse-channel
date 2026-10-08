# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — process CLI hub command
"""Load the hub command's authentication and trust families."""

from __future__ import annotations

import sys
from dataclasses import replace

from synapse_channel.cli_hub_startup import HubStartup, HubStartupRefused
from synapse_channel.core.acl import AclError, load_acl_policy
from synapse_channel.core.capability_card_history import PersistentCapabilityCardHistory
from synapse_channel.core.capability_card_trust import (
    DEFAULT_CAPABILITY_CARD_CLOCK_SKEW_SECONDS,
    DEFAULT_CAPABILITY_CARD_HISTORY_CAPACITY,
    DEFAULT_CAPABILITY_CARD_HISTORY_RETENTION_SECONDS,
    CapabilityCardTrustBundle,
    CapabilityCardTrustError,
    load_capability_card_trust_bundle,
)
from synapse_channel.core.identity_binding import IdentityBindingError, load_identity_trust_bundle
from synapse_channel.core.message_auth import MessageAuthKey
from synapse_channel.core.role_grants import RoleGrantError, load_role_grants


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
        if not key_id or not secret or (not senders):
            raise ValueError(malformed)
        keys.append(MessageAuthKey(key_id=key_id, secret=secret.encode("utf-8"), senders=senders))
    return keys


def load_message_auth_keys(ctx: HubStartup) -> None:
    """Load the sender-bound per-message authentication keys."""
    try:
        ctx.config = replace(
            ctx.config,
            auth=replace(
                ctx.config.auth,
                per_message_auth_keys=_parse_message_auth_keys(ctx.args.message_auth_key),
            ),
        )
    except ValueError as exc:
        print(f"synapse hub: {exc}", file=sys.stderr)
        raise HubStartupRefused() from None


def load_acl_policy_for_hub(ctx: HubStartup) -> None:
    """Load the ACL policy and retain the unauthenticated-enforcement warning."""
    try:
        ctx.config = replace(
            ctx.config,
            auth=replace(
                ctx.config.auth,
                acl_policy=load_acl_policy(ctx.args.acl_policy) if ctx.args.acl_policy else None,
            ),
        )
    except AclError as exc:
        print(f"synapse hub: {exc}", file=sys.stderr)
        raise HubStartupRefused() from None
    if ctx.args.require_acl and ctx.config.auth.authenticator is None:
        print(
            (
                "synapse hub: WARNING --require-acl without --token authorises a "
                "self-reported sender; namespace ACL rules give no protection on an"
                " unauthenticated hub. Pair --require-acl with --token, and with "
                "--require-message-auth so the sender is cryptographically bound, "
                "before relying on enforcement."
            ),
            file=sys.stderr,
        )


def load_role_grants_for_hub(ctx: HubStartup) -> None:
    """Load role grants and retain the unauthenticated-role warning."""
    try:
        ctx.config = replace(
            ctx.config,
            auth=replace(
                ctx.config.auth,
                role_grants=load_role_grants(ctx.args.role_grants)
                if ctx.args.role_grants
                else None,
            ),
        )
    except RoleGrantError as exc:
        print(f"synapse hub: {exc}", file=sys.stderr)
        raise HubStartupRefused() from None
    if ctx.args.require_role_claim and ctx.config.auth.authenticator is None:
        print(
            (
                "synapse hub: WARNING --require-role-claim without --token gates on"
                " a self-reported identity; role grants give no protection on an "
                "unauthenticated hub. Pair --require-role-claim with --token, and "
                "with --require-message-auth so the identity is cryptographically "
                "bound, before relying on enforcement."
            ),
            file=sys.stderr,
        )


def load_identity_trust_for_hub(ctx: HubStartup) -> None:
    """Load and validate the identity binding material."""
    try:
        ctx.config = replace(
            ctx.config,
            auth=replace(
                ctx.config.auth,
                identity_trust_bundle=load_identity_trust_bundle(ctx.args.identity_trust)
                if ctx.args.identity_trust
                else None,
            ),
        )
    except IdentityBindingError as exc:
        print(f"synapse hub: {exc}", file=sys.stderr)
        raise HubStartupRefused() from None
    if ctx.args.require_identity_binding and ctx.config.auth.identity_trust_bundle is None:
        print(
            (
                "synapse hub: --require-identity-binding requires --identity-trust;"
                " there is no identity key material to verify a registration "
                "against."
            ),
            file=sys.stderr,
        )
        raise HubStartupRefused() from None


def load_capability_trust_for_hub(ctx: HubStartup) -> None:
    """Load advisory capability trust and optional durable lifecycle history."""
    try:
        capability_card_trust_path = getattr(ctx.args, "capability_card_trust", "")
        capability_card_history_path = getattr(ctx.args, "capability_card_history_db", "")
        history_capacity = getattr(
            ctx.args, "capability_card_history_capacity", DEFAULT_CAPABILITY_CARD_HISTORY_CAPACITY
        )
        history_retention = getattr(
            ctx.args,
            "capability_card_history_retention_seconds",
            DEFAULT_CAPABILITY_CARD_HISTORY_RETENTION_SECONDS,
        )
        if capability_card_history_path and (not capability_card_trust_path):
            print(
                (
                    "synapse hub: --capability-card-history-db requires "
                    "--capability-card-trust; there are no card keys to verify."
                ),
                file=sys.stderr,
            )
            raise HubStartupRefused() from None
        ctx.config = replace(
            ctx.config,
            auth=replace(
                ctx.config.auth,
                capability_card_trust_bundle=load_capability_card_trust_bundle(
                    capability_card_trust_path,
                    clock_skew_seconds=getattr(
                        ctx.args,
                        "capability_card_clock_skew_seconds",
                        DEFAULT_CAPABILITY_CARD_CLOCK_SKEW_SECONDS,
                    ),
                    history_capacity=history_capacity,
                    history_retention_seconds=history_retention,
                )
                if capability_card_trust_path
                else None,
            ),
        )
        if (
            ctx.config.auth.capability_card_trust_bundle is not None
            and capability_card_history_path
        ):
            ctx.config = replace(
                ctx.config,
                auth=replace(
                    ctx.config.auth,
                    capability_card_trust_bundle=CapabilityCardTrustBundle(
                        keys=ctx.config.auth.capability_card_trust_bundle.keys,
                        history=PersistentCapabilityCardHistory(
                            capability_card_history_path,
                            max_entries=history_capacity,
                            retention_seconds=history_retention,
                        ),
                        clock_skew_seconds=ctx.config.auth.capability_card_trust_bundle.clock_skew_seconds,
                    ),
                ),
            )
    except CapabilityCardTrustError as exc:
        print(f"synapse hub: {exc}", file=sys.stderr)
        raise HubStartupRefused() from None


def prepare_hub_trust(ctx: HubStartup) -> None:
    """Load each trust family in the existing refusal and diagnostic order."""
    load_message_auth_keys(ctx)
    load_acl_policy_for_hub(ctx)
    load_role_grants_for_hub(ctx)
    load_identity_trust_for_hub(ctx)
    load_capability_trust_for_hub(ctx)
