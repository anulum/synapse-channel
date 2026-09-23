# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE GITHUB APP — signed review evidence intake
"""Decode bounded GitHub review evidence without treating its body as authority."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from synapse_github_app.errors import PayloadError, WebhookError
from synapse_github_app.json_boundary import loads_strict_bounded
from synapse_github_app.models import Repository, normalize_paths
from synapse_github_app.webhook import (
    MAX_WEBHOOK_BYTES,
    MAX_WEBHOOK_JSON_DEPTH,
    verify_signature,
)

_SHA = re.compile(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})\Z")
_DELIVERY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_REVIEW_STATES = frozenset({"approved", "changes_requested", "commented", "dismissed"})


@dataclass(frozen=True)
class ReviewWebhook:
    """Authenticated source metadata and untrusted body of one submitted review."""

    delivery_id: str
    event_kind: str
    repository: Repository
    pull_number: int
    head_sha: str
    review_id: int
    review_commit: str
    reviewer_login: str
    author_login: str | None
    state: str
    body: str
    source_path: str | None
    source_line: int | None


def _object(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise PayloadError(f"{name} must be an object")
    return cast(Mapping[str, object], value)


def _text(value: object, name: str, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > maximum:
        raise PayloadError(f"{name} must be a bounded non-empty string")
    if not value.isprintable():
        raise PayloadError(f"{name} must be printable")
    return value


def _positive(value: object, name: str) -> int:
    if type(value) is not int or value <= 0:
        raise PayloadError(f"{name} must be a positive integer")
    return value


def _sha(value: object, name: str) -> str:
    candidate = _text(value, name, 64)
    if _SHA.fullmatch(candidate) is None:
        raise PayloadError(f"{name} must be a Git object id")
    return candidate.lower()


def decode_review_webhook(
    *, headers: Mapping[str, str], body: bytes, secret: bytes
) -> ReviewWebhook | None:
    """Validate a signed submitted-review event, preserving its text as data.

    Parameters
    ----------
    headers : Mapping[str, str]
        GitHub event and HMAC headers.
    body : bytes
        Unmodified webhook body.
    secret : bytes
        Installation webhook secret.

    Returns
    -------
    ReviewWebhook or None
        A bounded submitted review, or ``None`` for another event/action.

    Raises
    ------
    WebhookError
        If authentication or the supported event shape fails.
    """
    if len(body) > MAX_WEBHOOK_BYTES:
        raise WebhookError(f"webhook body exceeds {MAX_WEBHOOK_BYTES} bytes")
    normalized = {key.lower(): value for key, value in headers.items()}
    if not verify_signature(
        secret=secret, body=body, signature=normalized.get("x-hub-signature-256")
    ):
        raise WebhookError("webhook signature is invalid")
    event_kind = normalized.get("x-github-event")
    if event_kind not in {"pull_request_review", "pull_request_review_comment"}:
        return None
    try:
        payload = _object(loads_strict_bounded(body, max_depth=MAX_WEBHOOK_JSON_DEPTH), "payload")
        expected_action = "submitted" if event_kind == "pull_request_review" else "created"
        if payload.get("action") != expected_action:
            return None
        repo = _object(payload.get("repository"), "repository")
        owner = _object(repo.get("owner"), "repository.owner")
        pull = _object(payload.get("pull_request"), "pull_request")
        head = _object(pull.get("head"), "pull_request.head")
        review_field = "review" if event_kind == "pull_request_review" else "comment"
        review = _object(payload.get(review_field), review_field)
        user = _object(review.get("user"), "review.user")
        author_value = pull.get("user")
        author = None if author_value is None else _object(author_value, "pull_request.user")
        state = "commented"
        if event_kind == "pull_request_review":
            state = _text(review.get("state"), "review.state", 32).lower()
            if state not in _REVIEW_STATES:
                raise PayloadError("review.state is unsupported")
        source_path = None
        source_line = None
        if event_kind == "pull_request_review_comment":
            source_path = normalize_paths((review.get("path"),))[0]
            raw_line = review.get("line")
            if raw_line is None:
                raw_line = review.get("original_line")
            source_line = None if raw_line is None else _positive(raw_line, "comment.line")
        raw_body = review.get("body")
        if raw_body is None:
            raw_body = ""
        if not isinstance(raw_body, str) or len(raw_body.encode("utf-8")) > 32768:
            raise PayloadError("review.body exceeds its bound")
        delivery_id = _text(normalized.get("x-github-delivery"), "delivery id", 128)
        if _DELIVERY.fullmatch(delivery_id) is None:
            raise PayloadError("delivery id is malformed")
        return ReviewWebhook(
            delivery_id=delivery_id,
            event_kind=event_kind,
            repository=Repository(
                owner=_text(owner.get("login"), "repository.owner.login", 39),
                name=_text(repo.get("name"), "repository.name", 100),
            ),
            pull_number=_positive(pull.get("number"), "pull_request.number"),
            head_sha=_sha(head.get("sha"), "pull_request.head.sha"),
            review_id=_positive(review.get("id"), "review.id"),
            review_commit=_sha(review.get("commit_id"), "review.commit_id"),
            reviewer_login=_text(user.get("login"), "review.user.login", 48),
            author_login=(
                None if author is None else _text(author.get("login"), "author.login", 48)
            ),
            state=state,
            body=raw_body,
            source_path=source_path,
            source_line=source_line,
        )
    except (json.JSONDecodeError, UnicodeError, PayloadError) as exc:
        raise WebhookError("review webhook payload is invalid") from exc
