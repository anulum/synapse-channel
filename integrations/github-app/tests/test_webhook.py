# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE GITHUB APP — signed bounded webhook tests
"""Verify HMAC-before-parse behaviour against real payload bytes."""

from __future__ import annotations

import hashlib
import hmac

import pytest

from payloads import encoded_payload, signed_headers
from synapse_github_app.errors import WebhookError
from synapse_github_app.webhook import (
    MAX_WEBHOOK_BYTES,
    decode_pull_request,
    verify_signature,
)

SECRET = b"It's a Secret to Everybody"


def test_github_published_hmac_known_answer_vector() -> None:
    body = b"Hello, World!"
    signature = "sha256=757107ea0eb2509fc211221cce984b8a37570b6d7586c22c46f4379c8b043e17"

    assert verify_signature(secret=SECRET, body=body, signature=signature)
    assert not verify_signature(secret=SECRET, body=body + b"!", signature=signature)


def test_signature_requires_a_nonempty_secret() -> None:
    with pytest.raises(WebhookError, match="must not be empty"):
        verify_signature(secret=b"", body=b"{}", signature=None)


def test_valid_signed_pull_request_decodes_after_authentication() -> None:
    body = encoded_payload(action="synchronize")
    event = decode_pull_request(headers=signed_headers(body, SECRET), body=body, secret=SECRET)

    assert event is not None
    assert event.action == "synchronize"
    assert event.pull_request.number == 7


def test_non_pull_event_and_unsupported_action_are_ignored() -> None:
    body = encoded_payload()
    assert (
        decode_pull_request(
            headers=signed_headers(body, SECRET, event="push"),
            body=body,
            secret=SECRET,
        )
        is None
    )
    closed = encoded_payload(action="closed")
    assert (
        decode_pull_request(headers=signed_headers(closed, SECRET), body=closed, secret=SECRET)
        is None
    )


def test_invalid_signature_is_rejected_before_json_parse() -> None:
    body = b"not-json"
    bad = "sha256=" + hmac.new(SECRET, b"different", hashlib.sha256).hexdigest()
    with pytest.raises(WebhookError, match="signature"):
        decode_pull_request(
            headers={
                "X-GitHub-Event": "pull_request",
                "X-GitHub-Delivery": "delivery",
                "X-Hub-Signature-256": bad,
            },
            body=body,
            secret=SECRET,
        )


def test_authenticated_invalid_or_deep_json_is_rejected() -> None:
    for body in (
        b"{",
        b'{"value":"\xff"}',
        ("[" * 65 + "0" + "]" * 65).encode(),
    ):
        with pytest.raises(WebhookError, match="payload"):
            decode_pull_request(headers=signed_headers(body, SECRET), body=body, secret=SECRET)


def test_oversized_body_is_refused_before_hmac_work() -> None:
    body = b"x" * (MAX_WEBHOOK_BYTES + 1)
    with pytest.raises(WebhookError, match="exceeds"):
        decode_pull_request(headers={}, body=body, secret=b"")


def test_authenticated_payload_validation_error_is_redacted() -> None:
    body = b'{"action":"opened"}'
    with pytest.raises(WebhookError, match="payload is invalid") as raised:
        decode_pull_request(headers=signed_headers(body, SECRET), body=body, secret=SECRET)
    assert "installation" not in str(raised.value)
