# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — process CLI hub command
"""Validate and own private attachment startup."""

from __future__ import annotations

import sqlite3
import sys
from dataclasses import replace
from pathlib import Path

from synapse_channel.cli_hub_journal import open_message_replay_store
from synapse_channel.cli_hub_startup import HubStartup, HubStartupRefused
from synapse_channel.core.attachment_serving import AttachmentServingPolicy
from synapse_channel.core.attachment_store import AttachmentError, AttachmentStore


def open_attachment_policy(ctx: HubStartup) -> None:
    """Load the attachment recipient policy only with valid attachment and serving prerequisites."""
    ctx.config = replace(ctx.config, attachment_serving_policy=None)
    recipient_policy_path = getattr(ctx.args, "attachment_recipient_policy", None)
    if recipient_policy_path:
        try:
            if not getattr(ctx.args, "attachment_root", None) or ctx.transport.serving is None:
                raise AttachmentError(
                    "attachment recipient policy requires attachments and peer serving policy"
                )
            policy = AttachmentServingPolicy(Path(recipient_policy_path))
            policy.load()
            ctx.config = replace(ctx.config, attachment_serving_policy=policy)
        except AttachmentError:
            print(
                "synapse hub: attachment recipient policy unavailable or invalid", file=sys.stderr
            )
            raise HubStartupRefused() from None


def open_attachment_store(ctx: HubStartup) -> None:
    """Validate all attachment prerequisites and own the opened store lifetime."""
    ctx.config = replace(ctx.config, attachment_store=None)
    if getattr(ctx.args, "attachment_root", None):
        if not (
            ctx.config.auth.authenticator is not None
            and ctx.args.require_identity_binding
            and (ctx.config.auth.identity_trust_bundle is not None)
            and ctx.args.require_message_auth
            and (ctx.config.auth.per_message_auth_replay_store is not None)
            and ctx.args.require_acl
            and (ctx.config.auth.acl_policy is not None)
            and (ctx.config.auth.role_grants is not None)
            and (ctx.config.journal is not None)
        ):
            print(
                (
                    "synapse hub: --attachment-root requires token, bound identity, "
                    "durable signed frames, ACL, role grants, and --db"
                ),
                file=sys.stderr,
            )
            raise HubStartupRefused() from None
        try:
            store = AttachmentStore(ctx.args.attachment_root)
            ctx.resources.callback(store.close)
            ctx.config = replace(ctx.config, attachment_store=store)
        except (AttachmentError, OSError, sqlite3.DatabaseError, ValueError) as exc:
            print(f"synapse hub: cannot open attachment store: {exc}", file=sys.stderr)
            raise HubStartupRefused() from None


def prepare_hub_attachments(ctx: HubStartup) -> None:
    """Open durable replay before validating and opening attachments."""
    open_message_replay_store(ctx)
    open_attachment_policy(ctx)
    open_attachment_store(ctx)
