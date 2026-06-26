# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Agent2Agent push notification delivery
"""Push-notification delivery helpers for the Agent2Agent bridge."""

from __future__ import annotations

import json
from collections.abc import Callable
from urllib import request
from urllib.error import URLError

from synapse_channel.a2a import JsonMap
from synapse_channel.a2a_validation import A2A_MEDIA_TYPE

PushDeliverer = Callable[[JsonMap], None]


def build_push_delivery(*, task: JsonMap, config: JsonMap) -> JsonMap:
    """Build the outbound push delivery envelope for one task update.

    Parameters
    ----------
    task : JsonMap
        A2A task snapshot to send to the webhook.
    config : JsonMap
        Stored push-notification configuration.

    Returns
    -------
    JsonMap
        Delivery envelope consumed by a ``PushDeliverer``.
    """
    headers: dict[str, str] = {}
    authentication = config.get("authentication")
    if isinstance(authentication, dict):
        scheme = str(authentication.get("scheme") or "").strip()
        credentials = str(authentication.get("credentials") or "").strip()
        if scheme and credentials:
            headers["Authorization"] = f"{scheme} {credentials}"
    return {
        "url": str(config["webhookUrl"]),
        "headers": headers,
        "payload": {"task": task},
    }


def http_push_deliverer(delivery: JsonMap) -> None:
    """Deliver one push notification over stdlib HTTP.

    Parameters
    ----------
    delivery : JsonMap
        Delivery envelope with ``url``, ``headers``, and ``payload`` entries.
    """
    raw = json.dumps(delivery["payload"], sort_keys=True).encode("utf-8")
    headers = {
        "Content-Type": A2A_MEDIA_TYPE,
        **delivery.get("headers", {}),
    }
    req = request.Request(
        str(delivery["url"]),
        data=raw,
        headers=headers,
        method="POST",
    )
    with request.urlopen(req, timeout=5.0) as response:
        response.read()


def deliver_push_notification(
    *,
    task: JsonMap,
    config: JsonMap,
    push_deliverer: PushDeliverer,
) -> None:
    """Deliver one task update and preserve best-effort failure handling.

    Parameters
    ----------
    task : JsonMap
        A2A task snapshot to send to the webhook.
    config : JsonMap
        Stored push-notification configuration.
    push_deliverer : PushDeliverer
        Callable that sends the prepared delivery envelope.
    """
    try:
        push_deliverer(build_push_delivery(task=task, config=config))
    except (OSError, TimeoutError, URLError):
        return
