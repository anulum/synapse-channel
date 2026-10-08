# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — resource lifetime at the hub composition boundary
"""Validate prerequisites and acquire only resources owned by this hub."""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from synapse_channel.core.durable_ingress import DurableIngressQuota
from synapse_channel.core.hub_config import HubConfig
from synapse_channel.core.merkle_checkpoint import (
    LiveCheckpoint,
    MerkleCheckpointStore,
    checkpoint_path_for,
)
from synapse_channel.core.persistence import EventStore


@dataclass(frozen=True, kw_only=True)
class HubLifetimeComponents:
    """Owned checkpoint, transport synchronization, clock and relay location."""

    checkpoint_path: Path | None
    live_checkpoint: LiveCheckpoint | None
    serving: asyncio.Event
    guard_evidence_quota: DurableIngressQuota
    clock: Callable[[], float]
    started: float
    relay_log: Path | None


def validate_attachments(config: HubConfig) -> None:
    """Refuse incomplete attachment posture before acquiring any checkpoint."""
    auth = config.auth
    if config.attachment_store is not None and not (
        auth.authenticator is not None
        and auth.require_identity_binding
        and auth.identity_trust_bundle is not None
        and auth.require_per_message_auth
        and auth.per_message_auth_keys
        and auth.per_message_auth_replay_store is not None
        and auth.require_acl
        and auth.acl_policy is not None
        and auth.role_grants is not None
        and config.journal is not None
    ):
        raise ValueError(
            "attachments require token, bound identity, durable signed frames, "
            "ACL, roles, and journal"
        )
    if config.attachment_serving_policy is not None:
        if config.attachment_store is None or config.multihub.multihub_serving_policy is None:
            raise ValueError(
                "attachment recipient policy requires attachments and peer serving policy"
            )
        config.attachment_serving_policy.load()


def checkpoint_interval(config: HubConfig) -> float:
    """Validate the checkpoint interval using the legacy finite-positive contract."""
    interval = float(config.checkpoint_interval)
    if not (math.isfinite(interval) and interval > 0.0):
        raise ValueError("checkpoint_interval must be a positive finite number of seconds")
    return interval


def open_checkpoint(journal: EventStore, path: Path) -> LiveCheckpoint:
    """Verify and anchor the chain, releasing the database on any refused start."""
    store = MerkleCheckpointStore(path)
    try:
        store.verify(journal)
        return LiveCheckpoint(store, journal)
    except BaseException:
        store.close()
        raise


def initial_checkpoint(config: HubConfig) -> tuple[Path | None, LiveCheckpoint | None]:
    """Acquire a checkpoint only for a configured persistent journal."""
    if not (
        config.anti_rollback_checkpoint
        and isinstance(config.journal, EventStore)
        and config.journal.path != ":memory:"
    ):
        return None, None
    path = (
        Path(config.checkpoint_store_path)
        if config.checkpoint_store_path
        else checkpoint_path_for(config.journal.path)
    )
    return path, open_checkpoint(config.journal, path)


def build_lifetime(
    config: HubConfig, path: Path | None, live: LiveCheckpoint | None
) -> HubLifetimeComponents:
    """Create synchronization and timing services around the acquired checkpoint."""
    clock = config.clock or time.monotonic
    return HubLifetimeComponents(
        checkpoint_path=path,
        live_checkpoint=live,
        serving=asyncio.Event(),
        guard_evidence_quota=DurableIngressQuota(
            max_events=100, max_bytes=262144, window_seconds=60.0
        ),
        clock=clock,
        started=clock(),
        relay_log=Path(config.relay_log) if config.relay_log else None,
    )
