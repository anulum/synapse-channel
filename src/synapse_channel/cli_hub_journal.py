# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — process CLI hub command
"""Prepare and own durable journal, replay and AEF startup."""

from __future__ import annotations

import sys
from dataclasses import replace

from synapse_channel.cli_hub_startup import HubStartup, HubStartupRefused
from synapse_channel.core.aef_legacy_mapping import AEF_MAPPED_EVENT_KINDS
from synapse_channel.core.aef_runtime import AefRuntimeConfig
from synapse_channel.core.event_row_mac import (
    RowMacError,
    load_or_create_row_mac_key,
    row_mac_key_path,
)
from synapse_channel.core.message_auth_durable import SequenceFloorMode
from synapse_channel.core.receipt_signing import ReceiptSigningError, load_receipt_signing_key


def configure_replay_contract(ctx: HubStartup) -> None:
    """Validate durable replay location, encryption and sequence-floor prerequisites."""
    ctx.replay.db_key_file = getattr(ctx.args, "db_key_file", None)
    if ctx.replay.db_key_file and (not ctx.args.db):
        print("synapse hub: --db-key-file requires --db", file=sys.stderr)
        raise HubStartupRefused() from None
    replay_db_arg = getattr(ctx.args, "message_auth_replay_db", None)
    try:
        ctx.replay.mode = SequenceFloorMode(
            getattr(ctx.args, "message_auth_sequence_floor_mode", "off")
        )
    except ValueError as exc:
        print(f"synapse hub: {exc}", file=sys.stderr)
        raise HubStartupRefused() from None
    if replay_db_arg and (not ctx.args.require_message_auth):
        print(
            "synapse hub: --message-auth-replay-db requires --require-message-auth", file=sys.stderr
        )
        raise HubStartupRefused() from None
    if ctx.replay.mode is not SequenceFloorMode.OFF and (not ctx.args.require_message_auth):
        print(
            "synapse hub: --message-auth-sequence-floor-mode requires --require-message-auth",
            file=sys.stderr,
        )
        raise HubStartupRefused() from None
    ctx.replay.path = replay_db_arg
    if ctx.replay.path is None and ctx.args.require_message_auth and ctx.args.db:
        ctx.replay.path = f"{ctx.args.db}.message-auth.db"
    if ctx.replay.mode is not SequenceFloorMode.OFF and ctx.replay.path is None:
        print(
            (
                "synapse hub: --message-auth-sequence-floor-mode requires a durable"
                " replay ledger; pass --db or --message-auth-replay-db"
            ),
            file=sys.stderr,
        )
        raise HubStartupRefused() from None
    if ctx.args.require_message_auth and ctx.replay.path is None:
        print(
            (
                "synapse hub: WARNING --require-message-auth without --db or "
                "--message-auth-replay-db uses a process-local nonce cache; a "
                "restart reopens still-fresh nonces."
            ),
            file=sys.stderr,
        )
    ctx.config = replace(
        ctx.config,
        auth=replace(ctx.config.auth, per_message_auth_sequence_floor_mode=ctx.replay.mode),
    )


def configure_aef_outbox(ctx: HubStartup) -> None:
    """Validate and load the native AEF signing and drain configuration."""
    aef_signing_key_path = getattr(ctx.args, "aef_signing_key", None)
    if aef_signing_key_path and (not ctx.args.db):
        print("synapse hub: --aef-signing-key requires --db", file=sys.stderr)
        raise HubStartupRefused() from None
    if aef_signing_key_path and (not ctx.args.hub_id):
        print("synapse hub: --aef-signing-key requires --hub-id", file=sys.stderr)
        raise HubStartupRefused() from None
    ctx.aef = None
    if aef_signing_key_path:
        try:
            ctx.aef = AefRuntimeConfig(
                db_path=str(ctx.args.db),
                hub_id=str(ctx.args.hub_id),
                signing_key=load_receipt_signing_key(aef_signing_key_path),
                db_key_file=ctx.replay.db_key_file,
                interval_seconds=float(getattr(ctx.args, "aef_drain_interval", 1.0)),
            )
        except (ReceiptSigningError, ValueError) as exc:
            print(f"synapse hub: {exc}", file=sys.stderr)
            raise HubStartupRefused() from None


def open_event_journal(ctx: HubStartup) -> None:
    """Open the actual journal and immediately register its lifetime owner."""
    try:
        store_kwargs: dict[str, object] = {"key_file": ctx.replay.db_key_file}
        if ctx.aef is not None:
            store_kwargs["aef_outbox_kinds"] = AEF_MAPPED_EVENT_KINDS
        ctx.config = replace(
            ctx.config,
            journal=ctx.factories.journal(ctx.args.db, **store_kwargs) if ctx.args.db else None,
        )
        if ctx.config.journal is not None:
            ctx.resources.callback(ctx.config.journal.close)
    except (ValueError, RuntimeError) as exc:
        print(f"synapse hub: {exc}", file=sys.stderr)
        raise HubStartupRefused() from None


def authenticate_event_rows(ctx: HubStartup) -> None:
    """Install row-MAC authentication and report quarantined durable rows."""
    if ctx.config.journal is not None:
        key_path = getattr(ctx.args, "row_mac_key_file", None) or row_mac_key_path(ctx.args.db)
        try:
            row_key = load_or_create_row_mac_key(
                key_path,
                current_max_seq=ctx.config.journal.max_seq(),
                log_has_macs=ctx.config.journal.has_row_macs(),
            )
        except RowMacError as exc:
            print(f"synapse hub: {exc}", file=sys.stderr)
            raise HubStartupRefused() from None
        quarantined = ctx.config.journal.enable_row_mac(row_key)
        if quarantined:
            seqs = ", ".join(str(row.seq) for row in quarantined[:10])
            more = f" and {len(quarantined) - 10} more" if len(quarantined) > 10 else ""
            print(
                f"synapse hub: WARNING {len(quarantined)} event row(s) failed row "
                f"authentication and are quarantined (seq {seqs}{more}); the hub serves "
                "read-only until an operator removes them or restores the log from a "
                "trusted copy",
                file=sys.stderr,
            )


def reconcile_startup_aef(ctx: HubStartup) -> None:
    """Drain the original AEF startup backlog before serving."""
    if ctx.aef is not None:
        try:
            settled = ctx.factories.drain_aef(ctx.aef)
        except Exception as exc:  # noqa: BLE001 — existing startup boundary fails closed
            print(f"synapse hub: AEF startup reconciliation failed: {exc}", file=sys.stderr)
            raise HubStartupRefused() from None
        print(
            "synapse hub: native AEF outbox enabled "
            f"(startup_settled={settled}, interval={ctx.aef.interval_seconds:g}s)",
            file=sys.stderr,
        )


def open_message_replay_store(ctx: HubStartup) -> None:
    """Open durable replay and register its release before later startup stages."""
    ctx.config = replace(
        ctx.config, auth=replace(ctx.config.auth, per_message_auth_replay_store=None)
    )
    if ctx.replay.path is not None:
        try:
            replay_store = ctx.factories.replay(
                ctx.replay.path,
                max_entries=ctx.args.message_auth_replay_capacity,
                window_seconds=ctx.args.message_auth_window_seconds,
                key_file=ctx.replay.db_key_file,
            )
            ctx.resources.callback(replay_store.close)
            ctx.config = replace(
                ctx.config,
                auth=replace(
                    ctx.config.auth,
                    per_message_auth_replay_store=replay_store,
                ),
            )
        except Exception as exc:  # noqa: BLE001 — existing startup boundary fails closed
            print(f"synapse hub: cannot open message-auth replay ledger: {exc}", file=sys.stderr)
            raise HubStartupRefused() from None
        print(
            "synapse hub: durable message-auth replay enabled "
            f"(sequence_floor={ctx.replay.mode.value})",
            file=sys.stderr,
        )


def prepare_hub_journal(ctx: HubStartup) -> None:
    """Open and authenticate the journal before startup reconciliation."""
    open_event_journal(ctx)
    authenticate_event_rows(ctx)
    reconcile_startup_aef(ctx)
