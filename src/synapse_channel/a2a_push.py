# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Agent2Agent push notification delivery
"""Push-notification delivery helpers for the Agent2Agent bridge.

Delivery goes out over the SSRF-resistant transport in
:mod:`synapse_channel.safe_webhook_transport`, which resolves each target once,
admits only globally routable addresses, pins the connection to the validated
address, bounds the discarded response body, refuses HTTPS downgrade, and
keeps credential-bearing redirects on the configured exact origin.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable, Sequence
from urllib import request
from urllib.error import URLError
from urllib.parse import urlparse

from synapse_channel import a2a_http_protocol, safe_webhook_transport
from synapse_channel.a2a import JsonMap
from synapse_channel.a2a_push_delivery import (
    DEFAULT_PUSH_RETRY_DELAYS_SECONDS,
    AttemptRecorder,
    Clock,
    PushDeliveryAttempt,
    PushDeliveryResult,
    Sleeper,
    deliver_with_retries,
)

PushDeliverer = Callable[[JsonMap], None]
LOCAL_TARGET_ERROR = safe_webhook_transport.LOCAL_TARGET_ERROR


class WebhookDeliveryClient:
    """HTTP(S) push-delivery client with explicit target and TLS policy.

    Parameters
    ----------
    allow_local_targets : bool, optional
        When true, localhost, loopback, private, and link-local addresses are
        allowed. The production default is false.
    ca_file : str or None, optional
        PEM file used as a trust anchor for HTTPS webhook receivers.
    timeout_seconds : float, optional
        Socket timeout for one webhook delivery attempt.
    """

    def __init__(
        self,
        *,
        allow_local_targets: bool = False,
        ca_file: str | None = None,
        timeout_seconds: float = 5.0,
    ) -> None:
        self.allow_local_targets = allow_local_targets
        self.ca_file = ca_file
        self.timeout_seconds = timeout_seconds

    def __call__(self, delivery: JsonMap) -> None:
        """Deliver one push notification envelope."""
        self.deliver(delivery)

    def deliver(self, delivery: JsonMap) -> None:
        """POST one prepared push notification envelope.

        The URL scheme and host are checked before the request is built, and the
        actual connection is pinned to a validated globally routable address by
        the safe transport, so a DNS answer cannot change between validation and
        connect. The transport also owns a fixed redirect policy: sensitive
        headers can follow only exact same-origin 307/308 responses, while every
        HTTPS-to-HTTP redirect fails closed.

        Parameters
        ----------
        delivery : JsonMap
            Delivery envelope with ``url``, ``headers``, and ``payload`` entries.
        """
        url = str(delivery["url"])
        _validate_webhook_target(url, allow_local_targets=self.allow_local_targets)
        raw = json.dumps(
            a2a_http_protocol.to_wire_json(delivery["payload"]),
            sort_keys=True,
        ).encode("utf-8")
        headers = {
            "Content-Type": a2a_http_protocol.HTTP_JSON_MEDIA_TYPE,
            **delivery.get("headers", {}),
        }
        req = request.Request(
            url,
            data=raw,
            headers=headers,
            method="POST",
        )
        opener = safe_webhook_transport.build_safe_opener(
            allow_local=self.allow_local_targets,
            ca_file=self.ca_file,
        )
        with opener.open(req, timeout=self.timeout_seconds) as response:
            safe_webhook_transport.read_bounded(response)


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
    """Deliver one push notification over the SSRF-resistant transport.

    Parameters
    ----------
    delivery : JsonMap
        Delivery envelope with ``url``, ``headers``, and ``payload`` entries.
    """
    WebhookDeliveryClient().deliver(delivery)


def _validate_webhook_target(url: str, *, allow_local_targets: bool = False) -> None:
    """Reject obviously unsafe webhook targets before a connection is attempted.

    The scheme, host, and port are checked here for a fast, clear failure. The
    address-family policy — refusing any non-public destination — is enforced when
    the safe transport resolves and pins the connection, so validation and connect
    observe the same DNS answer.
    """
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise URLError("pushNotificationConfig.webhookUrl must use http or https")
    hostname = parsed.hostname
    if hostname is None:
        raise URLError("pushNotificationConfig.webhookUrl must include a host")
    if parsed.username is not None or parsed.password is not None:
        raise URLError("pushNotificationConfig.webhookUrl must not include credentials")
    if not allow_local_targets and hostname.lower() == "localhost":
        raise URLError(LOCAL_TARGET_ERROR)
    try:
        port = parsed.port
    except ValueError as exc:
        raise URLError("pushNotificationConfig.webhookUrl has an invalid port") from exc
    # Reading ``.port`` validates the range; the value itself is not needed here.
    del port


def deliver_push_notification(
    *,
    task: JsonMap,
    config: JsonMap,
    push_deliverer: PushDeliverer,
    record_attempt: AttemptRecorder | None = None,
    delivery_id: str | None = None,
    retry_delays_seconds: Sequence[float] = DEFAULT_PUSH_RETRY_DELAYS_SECONDS,
    sleep: Sleeper = time.sleep,
    clock: Clock = time.time,
) -> PushDeliveryResult:
    """Deliver one task update with typed, bounded, sanitized outcomes.

    Parameters
    ----------
    task : JsonMap
        A2A task snapshot to send to the webhook.
    config : JsonMap
        Stored push-notification configuration.
    push_deliverer : PushDeliverer
        Callable that sends the prepared delivery envelope.
    record_attempt : AttemptRecorder or None, optional
        Durable evidence callback. ``None`` retains typed results for callers
        that do not own a persistent store.
    delivery_id : str or None, optional
        Stable identifier grouping every retry for this task update. A fresh
        UUID is generated when omitted.
    retry_delays_seconds : Sequence[float], optional
        Finite delays before retries; validated by the bounded retry engine.

    Returns
    -------
    PushDeliveryResult
        Final success or terminal dead-letter result.
    """
    delivery = build_push_delivery(task=task, config=config)

    def discard_attempt(_attempt: PushDeliveryAttempt) -> None:
        """Accept typed evidence when the caller has no persistent store."""

    return deliver_with_retries(
        task_id=str(task.get("id") or ""),
        config_id=str(config.get("id") or ""),
        delivery_id=delivery_id or str(uuid.uuid4()),
        deliver=lambda: push_deliverer(delivery),
        record_attempt=record_attempt or discard_attempt,
        retry_delays_seconds=retry_delays_seconds,
        sleep=sleep,
        clock=clock,
    )
