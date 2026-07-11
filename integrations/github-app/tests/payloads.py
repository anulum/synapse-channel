# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE GITHUB APP — production-shaped webhook test payloads
"""Construct deterministic GitHub webhook payloads for boundary tests."""

from __future__ import annotations

import hashlib
import hmac
import json


def pull_request_record(
    number: int,
    *,
    head_ref: str = "feature/risk",
    base_ref: str = "main",
    head_sha: str | None = None,
) -> dict[str, object]:
    """Return one GitHub REST-shaped pull-request record."""
    return {
        "number": number,
        "head": {"sha": head_sha or f"{number:040x}", "ref": head_ref},
        "base": {"ref": base_ref},
    }


def pull_request_payload(
    *,
    action: str = "opened",
    number: int = 7,
    head_ref: str = "feature/risk",
    base_ref: str = "main",
    head_sha: str | None = None,
) -> dict[str, object]:
    """Return the authenticated fields consumed from a GitHub webhook."""
    return {
        "action": action,
        "installation": {"id": 42},
        "repository": {"name": "synapse-channel", "owner": {"login": "anulum"}},
        "pull_request": pull_request_record(
            number,
            head_ref=head_ref,
            base_ref=base_ref,
            head_sha=head_sha,
        ),
    }


def encoded_payload(
    *,
    action: str = "opened",
    number: int = 7,
    head_ref: str = "feature/risk",
    base_ref: str = "main",
    head_sha: str | None = None,
) -> bytes:
    """Encode :func:`pull_request_payload` as deterministic UTF-8 JSON."""
    return json.dumps(
        pull_request_payload(
            action=action,
            number=number,
            head_ref=head_ref,
            base_ref=base_ref,
            head_sha=head_sha,
        ),
        sort_keys=True,
    ).encode("utf-8")


def signed_headers(
    body: bytes,
    secret: bytes,
    *,
    event: str = "pull_request",
    delivery_id: str = "00000000-0000-4000-8000-000000000007",
) -> dict[str, str]:
    """Return the GitHub headers for a real HMAC-SHA256 delivery."""
    signature = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
    return {
        "X-GitHub-Delivery": delivery_id,
        "X-GitHub-Event": event,
        "X-Hub-Signature-256": signature,
    }
