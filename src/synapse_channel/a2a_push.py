# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Agent2Agent push notification delivery
"""Push-notification delivery helpers for the Agent2Agent bridge."""

from __future__ import annotations

import ipaddress
import json
import socket
from collections.abc import Callable
from http.client import HTTPMessage
from typing import IO
from urllib import request
from urllib.error import URLError
from urllib.parse import urljoin, urlparse

from synapse_channel.a2a import JsonMap
from synapse_channel.a2a_validation import A2A_MEDIA_TYPE

PushDeliverer = Callable[[JsonMap], None]
LOCAL_TARGET_ERROR = "pushNotificationConfig.webhookUrl must not target local networks"


class _SafeWebhookRedirectHandler(request.HTTPRedirectHandler):
    """Validate webhook redirect targets before following them."""

    def redirect_request(
        self,
        req: request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> request.Request | None:
        _validate_webhook_target(urljoin(req.full_url, newurl))
        return super().redirect_request(req, fp, code, msg, headers, newurl)


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
    url = str(delivery["url"])
    _validate_webhook_target(url)
    raw = json.dumps(delivery["payload"], sort_keys=True).encode("utf-8")
    headers = {
        "Content-Type": A2A_MEDIA_TYPE,
        **delivery.get("headers", {}),
    }
    req = request.Request(
        url,
        data=raw,
        headers=headers,
        method="POST",
    )
    opener = request.build_opener(_SafeWebhookRedirectHandler())
    with opener.open(req, timeout=5.0) as response:
        response.read()


def _validate_webhook_target(url: str) -> None:
    """Reject webhook targets that resolve to local network addresses."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise URLError("pushNotificationConfig.webhookUrl must use http or https")
    hostname = parsed.hostname
    if hostname is None:
        raise URLError("pushNotificationConfig.webhookUrl must include a host")
    if hostname.lower() == "localhost":
        raise URLError(LOCAL_TARGET_ERROR)
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise URLError("pushNotificationConfig.webhookUrl has an invalid port") from exc
    try:
        infos = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise URLError(f"could not resolve webhook target {hostname}: {exc}") from exc
    for info in infos:
        sockaddr = info[4]
        if not sockaddr:
            continue
        if _is_local_network_address(str(sockaddr[0])):
            raise URLError(LOCAL_TARGET_ERROR)


def _is_local_network_address(raw_address: str) -> bool:
    """Return whether ``raw_address`` is unsafe for outbound webhook delivery."""
    try:
        address = ipaddress.ip_address(raw_address.split("%", 1)[0])
    except ValueError:
        return False
    return address.is_loopback or address.is_private or address.is_link_local


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
