# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — typed hub command startup contract
"""Retain configuration, dependencies and resource ownership during hub startup."""

from __future__ import annotations

import argparse
import ssl
from collections.abc import Callable, Coroutine
from contextlib import ExitStack
from dataclasses import dataclass, field

from synapse_channel.core.aef_runtime import AefRuntimeConfig
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.hub_config import HubConfig
from synapse_channel.core.message_auth_durable import (
    DurableMessageAuthReplayStore,
    SequenceFloorMode,
)
from synapse_channel.core.multihub_serving_config import LoadedMultiHubServingConfig
from synapse_channel.core.multihub_watch import MultiHubWatch
from synapse_channel.core.peer_identity import PeerRegistrationSigner
from synapse_channel.core.persistence import EventStore


class HubStartupRefused(Exception):
    """Stop startup after a stage emitted its existing operator diagnostic."""


@dataclass(frozen=True, kw_only=True)
class HubStartupFactories:
    """Explicit existing command dependencies, captured once for one invocation."""

    runner: Callable[[Coroutine[object, object, None]], None]
    hub: Callable[..., SynapseHub]
    journal: Callable[..., EventStore]
    replay: Callable[..., DurableMessageAuthReplayStore]
    logging: Callable[..., object]
    tls: Callable[..., ssl.SSLContext | None]
    certificate_pin_support: Callable[[], None]
    drain_aef: Callable[[AefRuntimeConfig], int]


@dataclass(kw_only=True)
class HubStartupTransport:
    """Loaded transport material and the optional serving-side observation task."""

    serving: LoadedMultiHubServingConfig | None = None
    ssl_context: ssl.SSLContext | None = None
    watch: MultiHubWatch | None = None
    client_certfile: str | None = None
    client_keyfile: str | None = None
    peer_signer: PeerRegistrationSigner | None = None
    relay_peer_values: list[str] = field(default_factory=list)
    message_peer_values: list[str] = field(default_factory=list)


@dataclass(kw_only=True)
class HubStartupReplay:
    """Validated durable replay location, encryption source and sequence mode."""

    db_key_file: str | None = None
    path: str | None = None
    mode: SequenceFloorMode = SequenceFloorMode.OFF


@dataclass(kw_only=True)
class HubStartup:
    """One invocation's canonical configuration and owned startup lifetime.

    Family stages replace immutable configuration records. Runtime material
    remains explicitly typed; it is not an unstructured keyword accumulator.
    The caller owns the ExitStack and closes it after every terminal outcome.
    """

    args: argparse.Namespace
    factories: HubStartupFactories
    resources: ExitStack
    config: HubConfig = field(default_factory=HubConfig)
    transport: HubStartupTransport = field(default_factory=HubStartupTransport)
    replay: HubStartupReplay = field(default_factory=HubStartupReplay)
    aef: AefRuntimeConfig | None = None
