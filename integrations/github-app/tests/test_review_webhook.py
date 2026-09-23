# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE GITHUB APP — signed review webhook acceptance
"""Exercise signed review intake and fail-closed malformed evidence."""

from __future__ import annotations

import json
from typing import Any

import pytest

from payloads import signed_headers
from synapse_github_app.errors import WebhookError
from synapse_github_app.review_webhook import ReviewWebhook, decode_review_webhook

SECRET = b"review-webhook-secret"
HEAD = "a" * 40
REVIEW_COMMIT = "b" * 40


def _payload() -> dict[str, Any]:
    return {
        "action": "submitted",
        "repository": {"owner": {"login": "anulum"}, "name": "synapse-channel"},
        "pull_request": {
            "number": 42,
            "head": {"sha": HEAD},
            "user": {"login": "author-login"},
        },
        "review": {
            "id": 108,
            "commit_id": REVIEW_COMMIT,
            "user": {"login": "reviewer-login"},
            "state": "changes_requested",
            "body": "Please inspect this text; it is untrusted review content.",
        },
    }


def _decode(payload: dict[str, Any], *, event: str = "pull_request_review") -> ReviewWebhook | None:
    body = json.dumps(payload).encode()
    return decode_review_webhook(
        headers=signed_headers(body, SECRET, event=event),
        body=body,
        secret=SECRET,
    )


def test_submitted_review_retains_source_and_untrusted_body() -> None:
    """Preserve signed review provenance without promoting its text."""
    review = _decode(_payload())
    assert review is not None
    assert review.repository.full_name == "anulum/synapse-channel"
    assert review.pull_number == 42
    assert review.head_sha == HEAD
    assert review.review_commit == REVIEW_COMMIT
    assert review.reviewer_login == "reviewer-login"
    assert review.author_login == "author-login"
    assert review.event_kind == "pull_request_review"
    assert review.source_path is None and review.source_line is None
    assert review.body == "Please inspect this text; it is untrusted review content."


def test_non_review_event_and_edited_action_are_ignored() -> None:
    """Ignore unrelated events and unsupported review edits."""
    body = json.dumps(_payload()).encode()
    assert (
        decode_review_webhook(
            headers=signed_headers(body, SECRET, event="pull_request"),
            body=body,
            secret=SECRET,
        )
        is None
    )
    assert _decode({**_payload(), "action": "edited"}) is None


def test_bad_signature_and_malformed_review_fail_closed() -> None:
    """Reject invalid signatures and malformed review commit identity."""
    body = json.dumps(_payload()).encode()
    with pytest.raises(WebhookError, match="signature"):
        decode_review_webhook(
            headers=signed_headers(body, b"wrong", event="pull_request_review"),
            body=body,
            secret=SECRET,
        )
    payload = _payload()
    payload["review"] = {**payload["review"], "commit_id": "not-a-sha"}
    with pytest.raises(WebhookError, match="invalid"):
        _decode(payload)


def test_review_body_and_delivery_are_bounded() -> None:
    """Bound reviewer text and require a valid delivery identifier."""
    payload = _payload()
    payload["review"] = {**payload["review"], "body": "x" * 32769}
    with pytest.raises(WebhookError, match="invalid"):
        _decode(payload)
    body = json.dumps(_payload()).encode()
    headers = signed_headers(body, SECRET, event="pull_request_review")
    headers["X-GitHub-Delivery"] = "invalid delivery"
    with pytest.raises(WebhookError, match="invalid"):
        decode_review_webhook(headers=headers, body=body, secret=SECRET)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", True),
        ("state", "pending"),
        ("body", 7),
        ("user", "reviewer"),
    ],
)
def test_malformed_review_fields_are_refused(field: str, value: object) -> None:
    """Refuse malformed review fields before creating evidence."""
    payload = _payload()
    payload["review"] = {**payload["review"], field: value}
    with pytest.raises(WebhookError, match="invalid"):
        _decode(payload)


def test_missing_author_and_null_body_remain_explicit() -> None:
    """Retain absent author identity and normalise an empty review body."""
    payload = _payload()
    payload["pull_request"] = {**payload["pull_request"], "user": None}
    payload["review"] = {**payload["review"], "body": None}
    review = _decode(payload)
    assert review is not None
    assert review.author_login is None
    assert review.body == ""


def test_nonprintable_reviewer_and_bad_root_are_refused() -> None:
    """Refuse invalid reviewer names and nonobject signed payloads."""
    payload = _payload()
    payload["review"] = {**payload["review"], "user": {"login": "bad\nlogin"}}
    with pytest.raises(WebhookError, match="invalid"):
        _decode(payload)
    payload["review"] = {**payload["review"], "user": {"login": ""}}
    with pytest.raises(WebhookError, match="invalid"):
        _decode(payload)
    body = b"[]"
    with pytest.raises(WebhookError, match="invalid"):
        decode_review_webhook(
            headers=signed_headers(body, SECRET, event="pull_request_review"),
            body=body,
            secret=SECRET,
        )


def test_webhook_body_size_is_checked_before_decoding() -> None:
    """Reject an oversized body before signature or JSON work."""
    with pytest.raises(WebhookError, match="exceeds"):
        decode_review_webhook(headers={}, body=b"x" * (1024 * 1024 + 1), secret=SECRET)


def test_created_inline_comment_retains_exact_path_line_and_commit() -> None:
    """Bind inline findings to their exact diff location and commit."""
    payload = _payload()
    payload["action"] = "created"
    payload["comment"] = {
        "id": 109,
        "commit_id": REVIEW_COMMIT,
        "user": {"login": "reviewer-login"},
        "body": "The inline finding is untrusted text.",
        "path": "src/safe.py",
        "line": 17,
    }
    del payload["review"]
    comment = _decode(payload, event="pull_request_review_comment")
    assert comment is not None
    assert comment.event_kind == "pull_request_review_comment"
    assert comment.review_id == 109
    assert comment.review_commit == REVIEW_COMMIT
    assert comment.source_path == "src/safe.py"
    assert comment.source_line == 17
    payload["comment"] = {**payload["comment"], "line": None, "original_line": 16}
    original = _decode(payload, event="pull_request_review_comment")
    assert original is not None and original.source_line == 16


def test_inline_comment_refuses_traversal_and_invalid_line() -> None:
    """Refuse path traversal and invalid inline comment positions."""
    payload = _payload()
    payload["action"] = "created"
    payload["comment"] = {
        "id": 109,
        "commit_id": REVIEW_COMMIT,
        "user": {"login": "reviewer-login"},
        "body": "finding",
        "path": "../escape.py",
        "line": 17,
    }
    del payload["review"]
    with pytest.raises(WebhookError, match="invalid"):
        _decode(payload, event="pull_request_review_comment")
    payload["comment"] = {**payload["comment"], "path": "src/safe.py", "line": 0}
    with pytest.raises(WebhookError, match="invalid"):
        _decode(payload, event="pull_request_review_comment")
    payload["action"] = "edited"
    assert _decode(payload, event="pull_request_review_comment") is None
