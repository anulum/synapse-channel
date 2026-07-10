# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Agent2Agent bridge validation policy
"""Validation policy for the Agent2Agent bridge."""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlparse

from synapse_channel.a2a_errors import A2AValidationError

A2A_MEDIA_TYPE = "application/a2a+json"
PROBLEM_MEDIA_TYPE = "application/problem+json"
SSE_MEDIA_TYPE = "text/event-stream"
BRIDGE_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
MAX_A2A_MESSAGE_PARTS = 64
OPEN_TASK_STATES = {"TASK_STATE_SUBMITTED", "TASK_STATE_WORKING"}
TERMINAL_TASK_STATES = {
    "TASK_STATE_COMPLETED",
    "TASK_STATE_FAILED",
    "TASK_STATE_CANCELED",
    "TASK_STATE_REJECTED",
}


def validate_bridge_id(value: object, *, field: str) -> None:
    """Reject caller-provided ids that are unsafe for bridge URLs or markers."""
    if value is None:
        return
    if not BRIDGE_ID.fullmatch(str(value)):
        raise A2AValidationError(f"message.{field} contains unsupported characters")


def validate_message_parts(parts: object) -> list[object]:
    """Return validated A2A message parts."""
    if not isinstance(parts, list) or not parts:
        raise A2AValidationError("message.parts must be a non-empty array")
    if len(parts) > MAX_A2A_MESSAGE_PARTS:
        raise A2AValidationError("message.parts exceeds maximum supported length")
    return parts


def validate_webhook_url(value: object) -> str:
    """Return a validated HTTP(S) webhook URL string."""
    webhook = str(value)
    parsed_webhook = urlparse(webhook)
    if parsed_webhook.scheme not in {"http", "https"}:
        raise A2AValidationError("pushNotificationConfig.webhookUrl must use http or https")
    if not parsed_webhook.netloc:
        raise A2AValidationError("pushNotificationConfig.webhookUrl must include a host")
    if parsed_webhook.username is not None or parsed_webhook.password is not None:
        raise A2AValidationError("pushNotificationConfig.webhookUrl must not include credentials")
    hostname = parsed_webhook.hostname
    if hostname is None or _is_local_network_host(hostname):
        raise A2AValidationError("pushNotificationConfig.webhookUrl must not target local networks")
    return webhook


def _is_local_network_host(hostname: str) -> bool:
    if hostname.lower() == "localhost":
        return True
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return False
    return address.is_loopback or address.is_private or address.is_link_local


def is_supported_json_media_type(content_type: str) -> bool:
    """Return whether ``content_type`` can carry an A2A JSON request body."""
    media_type = content_type.split(";", 1)[0].strip().lower()
    return not media_type or media_type in {A2A_MEDIA_TYPE, "application/json"}
