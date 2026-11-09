# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — listener-independent lifetime of hub background services
"""Keep one hub's background services alive independently of its listener.

This internal owner reuses the current hub's structural handler context and
bound resource callbacks. It creates no listener, state, journal or authority.
The listener owner retains readiness and bind-failure signalling, while these
services preserve their existing start, cancellation and final-anchor order.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from synapse_channel.core.handlers.delivery_modes import delivery_expiry_loop
from synapse_channel.core.merkle_checkpoint import LiveCheckpoint
from synapse_channel.core.message_forward_origin import message_forward_retry_loop

if TYPE_CHECKING:
    from synapse_channel.core.handlers.delivery_modes import DeliveryModesContext


@dataclass(frozen=True, kw_only=True)
class HubServiceHooks:
    """Bind the current monitor, watches and checkpoint without private access.

    Attributes
    ----------
    monitoring:
        The existing dark-seat monitor's context, entered before listening.
    token_watcher:
        Return a token-set watcher only when the current ingress owns one.
    key_rechecker:
        Construct the existing periodic identity-key recheck coroutine.
    current_checkpoint:
        Read the live checkpoint at service start, without snapshotting it.
    anchor_checkpoint:
        Construct the periodic anchor coroutine for that exact checkpoint.
    flush_audit, close_checkpoint:
        Existing finalisation callbacks, invoked after all services drain.
    """

    monitoring: Callable[[], AbstractAsyncContextManager[None]]
    token_watcher: Callable[[], Coroutine[Any, Any, None] | None]
    key_rechecker: Callable[[], Coroutine[Any, Any, None]]
    current_checkpoint: Callable[[], LiveCheckpoint | None]
    anchor_checkpoint: Callable[[LiveCheckpoint], Coroutine[Any, Any, None]]
    flush_audit: Callable[[], None]
    close_checkpoint: Callable[[], None]


class HubServiceLifetime:
    """Retain and drain the services belonging to one already-composed hub.

    Parameters
    ----------
    context:
        The same live structural context used by delivery and forwarding.
        Registries and peer configuration are read at start, not copied.
    hooks:
        Actual bound callbacks for current monitors and owned checkpoints.

    Notes
    -----
    The enclosing runtime owns the start/stop sequence. This internal helper
    neither launches a replacement actor nor qualifies process isolation.
    """

    def __init__(self, context: DeliveryModesContext, hooks: HubServiceHooks) -> None:
        """Retain callbacks without starting work or acquiring another store."""
        self._context = context
        self._hooks = hooks
        self._backgrounds: list[asyncio.Task[None]] = []

    def monitoring(self) -> AbstractAsyncContextManager[None]:
        """Return the original dark-seat context without adding an await point."""
        return self._hooks.monitoring()

    def start(self, stop: asyncio.Event) -> None:
        """Retain every service before starting the next or adding its callback.

        Parameters
        ----------
        stop:
            Enclosing runtime's stop event. Any completed background task
            requests stop; the runtime then calls ``check_completed`` to
            propagate its original failure before finalisation.
        """
        hub = self._context
        if hub.journal is not None and hub.stable_delivery_hub_id:
            delivery_sweeper = asyncio.create_task(delivery_expiry_loop(hub))
            self._backgrounds.append(delivery_sweeper)
            delivery_sweeper.add_done_callback(lambda _task: stop.set())
        if hub.message_peers:
            forward_retrier = asyncio.create_task(message_forward_retry_loop(hub))
            self._backgrounds.append(forward_retrier)
            forward_retrier.add_done_callback(lambda _task: stop.set())
        token_set_watcher = self._hooks.token_watcher()
        if token_set_watcher is not None:
            watcher = asyncio.create_task(token_set_watcher)
            self._backgrounds.append(watcher)
            watcher.add_done_callback(lambda _task: stop.set())
        key_rechecker = asyncio.create_task(self._hooks.key_rechecker())
        self._backgrounds.append(key_rechecker)
        key_rechecker.add_done_callback(lambda _task: stop.set())
        checkpoint = self._hooks.current_checkpoint()
        if checkpoint is not None:
            anchorer = asyncio.create_task(self._hooks.anchor_checkpoint(checkpoint))
            self._backgrounds.append(anchorer)
            anchorer.add_done_callback(lambda _task: stop.set())

    def check_completed(self) -> None:
        """Propagate original background failures in the existing start order.

        Raises
        ------
        BaseException
            The original failure or cancellation of a completed service.
            Still-running services are drained by ``finish`` on either path.
        """
        for background in self._backgrounds:
            if background.done():
                background.result()

    async def finish(self, started: bool) -> None:
        """Drain services before the existing final audit and checkpoint close.

        Parameters
        ----------
        started:
            Whether enclosing runtime startup completed. A failed listener
            start retains its caller-owned checkpoint exactly as before.

        Raises
        ------
        Exception
            Original final-audit or checkpoint-close error, after all retained
            background work has been joined. Service cancellation results are
            gathered as in the direct runtime's existing shutdown contract.
        """
        for background in self._backgrounds:
            background.cancel()
            await asyncio.gather(background, return_exceptions=True)
        if started:
            self._hooks.flush_audit()
            self._hooks.close_checkpoint()
