# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — central WebSocket hub that routes messages and owns state
"""Typed legacy keyword names; canonical defaults remain in HubConfig."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TypedDict

from synapse_channel.core.attachment_serving import AttachmentServingPolicy
from synapse_channel.core.attachment_store import AttachmentStore
from synapse_channel.core.durable_ingress import DurableIngressQuota
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protected_write_admission_journal import ProtectedAdmissionReplayPolicy
from synapse_channel.core.ratelimit import RateLimiter


class HubGeneralOptions(TypedDict, total=False):
    """Preserve the original general keyword value contracts."""

    default_ttl_seconds: float
    hub_id: str | None
    journal: EventStore | None
    attachment_store: AttachmentStore | None
    attachment_serving_policy: AttachmentServingPolicy | None
    rate_limiter: RateLimiter | None
    host_rate_limiter: RateLimiter | None
    durable_ingress_quota: DurableIngressQuota | None
    relay_log: str | Path | None
    relay_max_lines: int
    shutdown_close_timeout: float
    clock: Callable[[], float] | None
    protected_write_policies: Mapping[str, ProtectedAdmissionReplayPolicy] | None
    anti_rollback_checkpoint: bool
    checkpoint_store_path: str | Path | None
    checkpoint_interval: float
