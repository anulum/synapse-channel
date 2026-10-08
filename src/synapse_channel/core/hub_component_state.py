# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — durable state and live state-view composition
"""Resume durable state and connect its live views without owning a hub."""

from __future__ import annotations

from dataclasses import dataclass

from synapse_channel.core.agent_liveness import RecipientLiveness
from synapse_channel.core.chat_dedupe import ChatDedupe
from synapse_channel.core.dark_seat import DarkSeatMonitor
from synapse_channel.core.hub_component_callbacks import HubComponentCallbacks
from synapse_channel.core.hub_component_clients import HubClientComponents
from synapse_channel.core.hub_component_delivery import HubDeliveryComponents
from synapse_channel.core.hub_component_lifetime import HubLifetimeComponents
from synapse_channel.core.hub_config import HubConfig
from synapse_channel.core.hub_journal_recovery_gate import HubJournalRecoveryGate
from synapse_channel.core.hub_ledger_guard import HubLedgerGuard
from synapse_channel.core.hub_liveness import HubLivenessView
from synapse_channel.core.hub_state_seed import SeededHubState, seed_hub_state
from synapse_channel.core.state_transaction import SerializedStateMutationActor


@dataclass(frozen=True, kw_only=True)
class HubStateComponents:
    """Resumed state, its actor, ledger and views over the same live registries."""

    seeded: SeededHubState
    mutations: SerializedStateMutationActor
    recovery_gate: HubJournalRecoveryGate
    reactions: RecipientLiveness
    liveness: HubLivenessView
    chat_dedupe: ChatDedupe
    dark_seats: DarkSeatMonitor
    ledger: HubLedgerGuard


def load_state(config: HubConfig) -> SeededHubState:
    """Apply the unchanged journal replay and retention contract."""
    limits = config.limits
    return seed_hub_state(
        config.journal,
        default_ttl_seconds=config.default_ttl_seconds,
        max_history=limits.max_history,
        max_progress=limits.max_progress,
        max_progress_per_author=limits.max_progress_per_author,
        max_progress_per_task=limits.max_progress_per_task,
        max_claims_per_agent=limits.max_claims_per_agent,
        max_offers_per_agent=limits.max_offers_per_agent,
        max_paths_per_claim=limits.max_paths_per_claim,
        compact_hint_threshold=limits.compact_hint_threshold,
        protected_write_policies=config.protected_write_policies,
    )


def build_state(
    config: HubConfig,
    clients: HubClientComponents,
    delivery: HubDeliveryComponents,
    lifetime: HubLifetimeComponents,
    reactions: RecipientLiveness,
    callbacks: HubComponentCallbacks,
) -> HubStateComponents:
    """Resume receipt and mutation state and assemble the runtime views."""
    seeded = load_state(config)
    delivery.pending_receipts.restore(seeded.pending_receipts)
    liveness = HubLivenessView(
        reactions,
        enabled=config.liveness.warn_stale_recipients,
        waiter_window_seconds=config.liveness.waiter_liveness_window,
        online_agents=callbacks.online_agents,
        agent_sockets=clients.clients.agent_sockets,
        last_seen=seeded.state.last_seen,
        clock=lifetime.clock,
    )
    return HubStateComponents(
        seeded=seeded,
        mutations=SerializedStateMutationActor(),
        recovery_gate=HubJournalRecoveryGate(
            seeded.corrupt_rows, send_json=callbacks.send_json, system=callbacks.system
        ),
        reactions=reactions,
        liveness=liveness,
        chat_dedupe=ChatDedupe(),
        dark_seats=DarkSeatMonitor(
            claims=callbacks.claims,
            tasks=callbacks.tasks,
            has_live_waiter=liveness.has_live_waiter,
            broadcast=callbacks.broadcast,
            system=callbacks.system,
        ),
        ledger=HubLedgerGuard(
            max_findings_per_agent=config.limits.max_findings_per_agent,
            journal=config.journal,
            message_seq=seeded.message_seq,
            finding_counts=seeded.finding_counts,
            idempotency_seed=seeded.idempotency_seed,
        ),
    )
