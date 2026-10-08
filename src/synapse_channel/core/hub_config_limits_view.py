# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — central WebSocket hub that routes messages and owns state
"""Typed compatibility projections for the limits settings family."""

from __future__ import annotations

from dataclasses import replace

from synapse_channel.core.hub_config_attribute import ConfigAttribute, HubConfigOwner


class HubLimitsConfigView(HubConfigOwner):
    """Read and replace record-owned settings through their existing hub names."""

    board_task_cap: ConfigAttribute[int | None] = ConfigAttribute(
        lambda config: config.limits.board_task_cap,
        lambda config, value: replace(config, limits=replace(config.limits, board_task_cap=value)),
    )
    compact_hint_threshold: ConfigAttribute[int] = ConfigAttribute(
        lambda config: config.limits.compact_hint_threshold,
        lambda config, value: replace(
            config, limits=replace(config.limits, compact_hint_threshold=value)
        ),
    )
    dead_letter_escalation_threshold: ConfigAttribute[int] = ConfigAttribute(
        lambda config: config.limits.dead_letter_escalation_threshold,
        lambda config, value: replace(
            config, limits=replace(config.limits, dead_letter_escalation_threshold=value)
        ),
    )
    lease_offline_ttl: ConfigAttribute[float] = ConfigAttribute(
        lambda config: config.takeover.lease_offline_ttl,
        lambda config, value: replace(
            config, takeover=replace(config.takeover, lease_offline_ttl=value)
        ),
    )
    max_clients: ConfigAttribute[int] = ConfigAttribute(
        lambda config: config.limits.max_clients,
        lambda config, value: replace(config, limits=replace(config.limits, max_clients=value)),
    )
    max_connections_per_host: ConfigAttribute[int | None] = ConfigAttribute(
        lambda config: config.limits.max_connections_per_host,
        lambda config, value: replace(
            config, limits=replace(config.limits, max_connections_per_host=value)
        ),
    )
    max_findings_per_agent: ConfigAttribute[int] = ConfigAttribute(
        lambda config: config.limits.max_findings_per_agent,
        lambda config, value: replace(
            config, limits=replace(config.limits, max_findings_per_agent=value)
        ),
    )
    max_history: ConfigAttribute[int] = ConfigAttribute(
        lambda config: config.limits.max_history,
        lambda config, value: replace(config, limits=replace(config.limits, max_history=value)),
    )
    max_msg_bytes: ConfigAttribute[int] = ConfigAttribute(
        lambda config: config.limits.max_msg_bytes,
        lambda config, value: replace(config, limits=replace(config.limits, max_msg_bytes=value)),
    )
    takeover_cooldown: ConfigAttribute[float] = ConfigAttribute(
        lambda config: config.takeover.takeover_cooldown,
        lambda config, value: replace(
            config, takeover=replace(config.takeover, takeover_cooldown=value)
        ),
    )
    takeover_oscillation_threshold: ConfigAttribute[int] = ConfigAttribute(
        lambda config: config.takeover.takeover_oscillation_threshold,
        lambda config, value: replace(
            config, takeover=replace(config.takeover, takeover_oscillation_threshold=value)
        ),
    )
    takeover_oscillation_window: ConfigAttribute[float] = ConfigAttribute(
        lambda config: config.takeover.takeover_oscillation_window,
        lambda config, value: replace(
            config, takeover=replace(config.takeover, takeover_oscillation_window=value)
        ),
    )
    takeover_quarantine: ConfigAttribute[float] = ConfigAttribute(
        lambda config: config.takeover.takeover_quarantine,
        lambda config, value: replace(
            config, takeover=replace(config.takeover, takeover_quarantine=value)
        ),
    )
