# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — central WebSocket hub that routes messages and owns state
"""Typed compatibility projections for the metrics settings family."""

from __future__ import annotations

from dataclasses import replace

from synapse_channel.core.hub_config_attribute import ConfigAttribute, HubConfigOwner


class HubMetricsConfigView(HubConfigOwner):
    """Read and replace record-owned settings through their existing hub names."""

    advertised_host: ConfigAttribute[str | None] = ConfigAttribute(
        lambda config: config.metrics.advertised_host,
        lambda config, value: replace(
            config, metrics=replace(config.metrics, advertised_host=value)
        ),
    )
    allowed_origins: ConfigAttribute[tuple[str, ...]] = ConfigAttribute(
        lambda config: config.metrics.allowed_origins,
        lambda config, value: replace(
            config, metrics=replace(config.metrics, allowed_origins=value)
        ),
    )
    enable_metrics: ConfigAttribute[bool] = ConfigAttribute(
        lambda config: config.metrics.enable_metrics,
        lambda config, value: replace(
            config, metrics=replace(config.metrics, enable_metrics=value)
        ),
    )
    metrics_query_token_ok: ConfigAttribute[bool] = ConfigAttribute(
        lambda config: config.metrics.metrics_query_token_ok,
        lambda config, value: replace(
            config, metrics=replace(config.metrics, metrics_query_token_ok=value)
        ),
    )
    metrics_token: ConfigAttribute[str | None] = ConfigAttribute(
        lambda config: config.metrics.metrics_token,
        lambda config, value: replace(config, metrics=replace(config.metrics, metrics_token=value)),
    )
    recipient_liveness_window: ConfigAttribute[float] = ConfigAttribute(
        lambda config: config.liveness.recipient_liveness_window,
        lambda config, value: replace(
            config, liveness=replace(config.liveness, recipient_liveness_window=value)
        ),
    )
    waiter_liveness_window: ConfigAttribute[float] = ConfigAttribute(
        lambda config: config.liveness.waiter_liveness_window,
        lambda config, value: replace(
            config, liveness=replace(config.liveness, waiter_liveness_window=value)
        ),
    )
    warn_stale_recipients: ConfigAttribute[bool] = ConfigAttribute(
        lambda config: config.liveness.warn_stale_recipients,
        lambda config, value: replace(
            config, liveness=replace(config.liveness, warn_stale_recipients=value)
        ),
    )
