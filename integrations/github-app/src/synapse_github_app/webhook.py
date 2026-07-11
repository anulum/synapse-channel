# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE GITHUB APP — signed and bounded webhook intake
"""Authenticate raw GitHub webhooks before bounded JSON decoding."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping

from synapse_github_app.errors import PayloadError, WebhookError
from synapse_github_app.json_boundary import loads_strict_bounded
from synapse_github_app.models import PullRequestEvent

MAX_WEBHOOK_BYTES = 1024 * 1024
MAX_WEBHOOK_JSON_DEPTH = 64
SUPPORTED_ACTIONS = frozenset({"opened", "ready_for_review", "reopened", "synchronize"})


def verify_signature(*, secret: bytes, body: bytes, signature: str | None) -> bool:
    """Return whether GitHub's HMAC-SHA256 signature matches the raw body."""
    if not secret:
        raise WebhookError("webhook secret must not be empty")
    expected = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature or "")


def decode_pull_request(
    *, headers: Mapping[str, str], body: bytes, secret: bytes
) -> PullRequestEvent | None:
    """Authenticate and decode one supported pull-request webhook.

    Unsupported event types and pull-request actions return ``None`` so a host
    can acknowledge them without creating a check.
    """
    if len(body) > MAX_WEBHOOK_BYTES:
        raise WebhookError(f"webhook body exceeds {MAX_WEBHOOK_BYTES} bytes")
    normalized = {key.lower(): value for key, value in headers.items()}
    if not verify_signature(
        secret=secret,
        body=body,
        signature=normalized.get("x-hub-signature-256"),
    ):
        raise WebhookError("webhook signature is invalid")
    if normalized.get("x-github-event") != "pull_request":
        return None
    try:
        decoded = loads_strict_bounded(body, max_depth=MAX_WEBHOOK_JSON_DEPTH)
        event = PullRequestEvent.from_payload(
            decoded,
            delivery_id=normalized.get("x-github-delivery", ""),
        )
    except (json.JSONDecodeError, UnicodeError, PayloadError) as exc:
        raise WebhookError("webhook payload is invalid") from exc
    return event if event.action in SUPPORTED_ACTIONS else None
