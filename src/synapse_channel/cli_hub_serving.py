# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — process CLI hub command
"""Run the constructed hub under its command lifetime."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import shlex
import sys
import threading
from collections.abc import Callable, Coroutine
from typing import Any

from synapse_channel.cli_hub_config import build_cli_hub_config
from synapse_channel.cli_hub_startup import HubStartup, HubStartupRefused
from synapse_channel.core.aef_runtime import AefRuntimeConfig, run_aef_outbox_worker
from synapse_channel.core.hub import InsecureBindError, SynapseHub
from synapse_channel.core.hub_config import config_fingerprint
from synapse_channel.core.hub_exposure import OPEN_LOOPBACK_NOTICE, is_loopback_host
from synapse_channel.core.merkle_checkpoint import AntiRollbackError, checkpoint_path_for
from synapse_channel.core.multihub_watch import MultiHubWatch

_LOGGER = logging.getLogger(__name__)


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


def _anti_rollback_refusal_lines(
    exc: AntiRollbackError, *, db_path: str, db_key_file: str | None
) -> list[str]:
    """Return the operator message for a start refused by anti-rollback verification.

    The refusal is a detected integrity failure, not a crash: the operator gets
    the detection reason, the offline verification command and the two recovery
    routes. Both files stay in place; the checkpoint store is the tamper evidence.
    The checkpoint path is the hub's default beside ``db_path``, which is the
    only location ``synapse hub`` uses.
    """
    checkpoint = checkpoint_path_for(db_path)
    key_flag = f" --db-key-file {shlex.quote(db_key_file)}" if db_key_file else ""
    return [
        f"refused to start: durable log failed anti-rollback verification: {exc}",
        f"inspect: synapse merkle checkpoint --verify{key_flag} {shlex.quote(db_path)}",
        "keep both files as evidence; restore the log from a trusted copy, or, only if "
        f"the change was authorised, move {shlex.quote(str(checkpoint))} aside so the "
        "next start anchors a new checkpoint chain",
    ]


def run_hub(ctx: HubStartup) -> int:
    """Construct the hub and run its owned server, watch and AEF workers."""
    config = build_cli_hub_config(ctx.args, ctx.config)
    try:
        hub = (
            SynapseHub.from_config(config)
            if ctx.factories.hub is SynapseHub
            else ctx.factories.hub(**config.to_kwargs())
        )
        if isinstance(hub, SynapseHub):
            ctx.resources.callback(hub.close)
    except (OSError, ValueError, AntiRollbackError) as exc:
        if isinstance(exc, AntiRollbackError):
            for line in _anti_rollback_refusal_lines(
                exc, db_path=ctx.args.db, db_key_file=ctx.replay.db_key_file or None
            ):
                print(f"synapse hub: {line}", file=sys.stderr)
        else:
            print(f"synapse hub: could not initialise hub: {exc}", file=sys.stderr)
        raise HubStartupRefused() from None
    hub.config_epoch = config_fingerprint(config)

    def serve() -> Coroutine[Any, Any, None]:
        return hub.serve(
            host=ctx.args.host, port=ctx.args.port, ssl_context=ctx.transport.ssl_context
        )

    server_factory = serve
    if ctx.transport.watch is not None:
        active_watch = ctx.transport.watch

        def watched_server() -> Coroutine[Any, Any, None]:
            return _serve_with_watch(serve, active_watch)

        server_factory = watched_server
    if (
        is_loopback_host(ctx.args.host)
        and ctx.config.auth.authenticator is None
        and (not getattr(ctx.args, "require_identity_binding", False))
    ):
        print(OPEN_LOOPBACK_NOTICE, file=sys.stderr)
    try:
        ctx.factories.runner(
            server_factory() if ctx.aef is None else _serve_with_aef(server_factory, ctx.aef)
        )
    except InsecureBindError as exc:
        print(f"synapse hub: {exc}", file=sys.stderr)
        raise HubStartupRefused() from None
    except KeyboardInterrupt:
        print("\nHub stopped by user.")
    return 0
