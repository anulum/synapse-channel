# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — process CLI hub command
"""Ordered startup and compatibility surface for the hub process command."""

from __future__ import annotations

import argparse
import contextlib
import ssl
from collections.abc import Callable, Coroutine
from typing import Any

from synapse_channel.cli_hub_attachments import prepare_hub_attachments
from synapse_channel.cli_hub_auth import _parse_message_auth_keys as _parse_message_auth_keys
from synapse_channel.cli_hub_auth import prepare_hub_trust
from synapse_channel.cli_hub_federation import prepare_hub_peers
from synapse_channel.cli_hub_journal import prepare_hub_journal
from synapse_channel.cli_hub_profiles import _apply_auto_rate_policy as _apply_auto_rate_policy
from synapse_channel.cli_hub_profiles import apply_hub_profiles, configure_ingress_limits
from synapse_channel.cli_hub_serving import _serve_with_watch as _serve_with_watch
from synapse_channel.cli_hub_serving import run_hub
from synapse_channel.cli_hub_startup import HubStartup, HubStartupFactories, HubStartupRefused
from synapse_channel.cli_hub_transport import prepare_hub_transport
from synapse_channel.cli_processes_runtime import _run
from synapse_channel.core.aef_runtime import drain_aef_startup_backlog
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.logging_setup import configure_logging
from synapse_channel.core.message_auth_durable import DurableMessageAuthReplayStore
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.tls import _require_certificate_pin_support, build_server_ssl_context


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
    certificate_pin_support_checker: Callable[[], None] = _require_certificate_pin_support,
) -> int:
    """Run the coordination hub until interrupted.

    With ``--db`` the hub persists authoritative state to a durable event log and
    resumes from it on restart; without it the hub is purely in-memory. Pair
    ``--db-key-file`` with ``--db`` for SQLCipher page encryption of the store.
    """
    factories = HubStartupFactories(
        runner=runner,
        hub=hub_factory,
        journal=store_factory,
        replay=replay_store_factory,
        logging=logging_configurator,
        tls=tls_context_factory,
        certificate_pin_support=certificate_pin_support_checker,
        drain_aef=drain_aef_startup_backlog,
    )
    with contextlib.ExitStack() as resources:
        ctx = HubStartup(args=args, factories=factories, resources=resources)
        try:
            apply_hub_profiles(ctx)
            prepare_hub_transport(ctx)
            prepare_hub_journal(ctx)
            configure_ingress_limits(ctx)
            prepare_hub_trust(ctx)
            prepare_hub_peers(ctx)
            prepare_hub_attachments(ctx)
            return run_hub(ctx)
        except HubStartupRefused:
            return 2
