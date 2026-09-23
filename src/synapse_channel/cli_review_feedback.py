# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — owner-local review custody and exact-session routing
"""Bind source reviews to work and route decided findings over the durable hub."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import importlib
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

from synapse_channel.client.agent import SynapseAgent, default_hub_uri
from synapse_channel.connect_failures import closed_after_ready
from synapse_channel.core.approvals import run_approval_report
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.review_feedback import (
    AuthorBinding,
    ReviewFeedbackError,
    ReviewFinding,
    applicability,
    bind_author,
    independent_decision,
    inspect_commit,
    review_subject,
)
from synapse_channel.core.review_feedback_store import (
    default_review_store,
    get_binding,
    get_finding,
    mark_routed,
    save_binding,
    save_finding,
)
from synapse_channel.core.secret_files import SecretFileError, read_secret_file
from synapse_channel.core.secure_path import SecurePathError, read_owner_only_file_bytes


def _store(args: argparse.Namespace) -> Path:
    return Path(args.store).expanduser() if args.store else default_review_store()


def _bind(args: argparse.Namespace) -> int:
    binding = bind_author(
        Path(args.repo_path),
        repository=args.repository,
        commit=args.commit,
        task_id=args.task_id,
        author_seat=args.author_seat,
        author_session=args.author_session,
    )
    created = save_binding(_store(args), binding)
    print(
        json.dumps(
            {
                "created": created,
                "repository": binding.repository,
                "commit": binding.commit,
                "task_id": binding.task_id,
                "author_seat": binding.author_seat,
                "author_session_sha256": hashlib.sha256(
                    binding.author_session.encode("utf-8")
                ).hexdigest(),
            },
            sort_keys=True,
        )
    )
    return 0


def _headers(path: str) -> dict[str, str]:
    raw = read_owner_only_file_bytes(path, purpose="review headers", max_bytes=8192)
    decoded = json.loads(raw)
    if (
        not isinstance(decoded, dict)
        or set(decoded) != {"X-Hub-Signature-256", "X-GitHub-Event", "X-GitHub-Delivery"}
        or any(not isinstance(value, str) for value in decoded.values())
    ):
        raise ReviewFeedbackError("review headers must contain exact signed event fields")
    return decoded


def _ingest(args: argparse.Namespace) -> int:
    body = read_owner_only_file_bytes(
        args.webhook_file, purpose="review webhook", max_bytes=1048576
    )
    headers = _headers(args.headers_file)
    secret = read_owner_only_file_bytes(args.secret_file, purpose="webhook secret", max_bytes=4096)
    try:
        adapter = importlib.import_module("synapse_github_app.review_webhook")
    except ImportError as exc:
        raise ReviewFeedbackError("install the separate synapse-github-app package") from exc
    review = adapter.decode_review_webhook(headers=headers, body=body, secret=secret)
    if review is None:
        raise ReviewFeedbackError("webhook is not a submitted review")
    finding = ReviewFinding(
        repository=review.repository.full_name,
        review_id=review.review_id,
        delivery_id=review.delivery_id,
        pull_number=review.pull_number,
        reviewed_commit=review.review_commit,
        observed_head=review.head_sha,
        github_reviewer=review.reviewer_login,
        github_author=review.author_login,
        github_state=review.state,
        body=review.body,
        severity=args.severity,
        evidence=args.evidence,
        expected_verification=args.verify,
        source_kind=review.event_kind,
        source_path=review.source_path,
        source_line=review.source_line,
        source_sha256=hashlib.sha256(body).hexdigest(),
    )
    created = save_finding(
        _store(args),
        finding,
        webhook_body=body,
        webhook_signature=headers["X-Hub-Signature-256"],
        observed_at=time.time(),
    )
    binding = get_binding(
        _store(args), repository=finding.repository, commit=finding.reviewed_commit
    )
    print(
        json.dumps(
            {
                "created": created,
                "key": finding.key,
                "approval_subject": (
                    review_subject(finding, binding) if binding is not None else None
                ),
                "author_binding": "present" if binding is not None else "missing",
                "github_text_authority": "untrusted_data",
            },
            sort_keys=True,
        )
    )
    return 0


def _assessment(
    args: argparse.Namespace,
) -> tuple[ReviewFinding, AuthorBinding | None, str, str, int, dict[str, Any]]:
    found = get_finding(_store(args), args.key)
    if found is None:
        raise ReviewFeedbackError("review finding is missing")
    finding, receipt = found
    binding = get_binding(
        _store(args), repository=finding.repository, commit=finding.reviewed_commit
    )
    if not Path(args.hub_db).is_file():
        raise ReviewFeedbackError("hub approval event store is missing")
    approvals = run_approval_report(args.hub_db, key_file=args.db_key_file)
    decision = independent_decision(finding, binding, approvals, reviewer_seat=args.reviewer_seat)
    status = (
        approvals.by_subject.get(review_subject(finding, binding)) if binding is not None else None
    )
    decision_seq = status.history[-1].seq if status is not None and status.history else 0
    current = (
        inspect_commit(Path(args.repo_path), args.current_commit)
        if args.repo_path and args.current_commit
        else None
    )
    applies = applicability(binding, current) if binding is not None else "missing_author_binding"
    return finding, binding, decision, applies, decision_seq, receipt


def _status(args: argparse.Namespace) -> int:
    finding, binding, decision, applies, decision_seq, receipt = _assessment(args)
    report = {
        "key": finding.key,
        "repository": finding.repository,
        "reviewed_commit": finding.reviewed_commit,
        "observed_head": finding.observed_head,
        "source_sha256": finding.source_sha256,
        "severity": finding.severity,
        "evidence": finding.evidence,
        "expected_verification": finding.expected_verification,
        "github_review_state": finding.github_state,
        "source_kind": finding.source_kind,
        "source_path": finding.source_path,
        "source_line": finding.source_line,
        "github_text_authority": "untrusted_data",
        "approval_subject": review_subject(finding, binding) if binding is not None else None,
        "decision": decision,
        "decision_seq": decision_seq,
        "applicability": applies,
        "task_id": None if binding is None else binding.task_id,
        "author_seat": None if binding is None else binding.author_seat,
        "author_session_sha256": (
            None
            if binding is None
            else hashlib.sha256(binding.author_session.encode("utf-8")).hexdigest()
        ),
        "routed_at": receipt["routed_at"],
        "route_msg_id": receipt["route_msg_id"],
        "route_decision_seq": receipt["route_decision_seq"],
    }
    if args.show_untrusted_body:
        report["untrusted_body"] = finding.body
    if args.show_author_session and binding is not None:
        report["author_session"] = binding.author_session
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


async def _deliver(
    *, uri: str, name: str, target: str, payload: str, msg_id: str, token: str | None
) -> bool:
    """Use the hub's directed receipt without treating it as model execution."""
    receipts: list[dict[str, Any]] = []

    async def collect(frame: dict[str, Any]) -> None:
        if (
            frame.get("type") == MessageType.DELIVERY_RECEIPT
            and frame.get("client_msg_id") == msg_id
        ):
            receipts.append(frame)

    agent = SynapseAgent(name, collect, uri=uri, verbose=False, token=token)
    connection = asyncio.create_task(agent.connect())
    try:
        if not await agent.wait_until_ready(timeout=5) or await closed_after_ready(agent):
            return False
        await agent.send_message(
            MessageType.CHAT,
            target=target,
            payload=payload,
            priority=True,
            receipt_requested=True,
            client_msg_id=msg_id,
        )
        for _ in range(200):
            if receipts:
                return receipts[-1].get("delivered") is True
            await asyncio.sleep(0.01)
        return False
    finally:
        agent.running = False
        connection.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await connection


def _route(args: argparse.Namespace) -> int:
    finding, binding, decision, applies, decision_seq, receipt = _assessment(args)
    if binding is None:
        raise ReviewFeedbackError("author session binding is missing")
    if decision not in {"accepted", "rejected"}:
        raise ReviewFeedbackError("latest independent review decision is missing")
    if applies not in {"exact_commit", "same_patch_rebased_requires_recheck"}:
        raise ReviewFeedbackError("current diff is unavailable or stale")
    msg_id = "review-" + hashlib.sha256(f"{finding.key}:{decision_seq}".encode()).hexdigest()[:32]
    if receipt["route_decision_seq"] == decision_seq:
        if receipt["route_msg_id"] != msg_id:
            raise ReviewFeedbackError("route identity changed")
        print(json.dumps({"key": finding.key, "route": "already_confirmed"}))
        return 0
    payload = json.dumps(
        {
            "kind": "review_feedback_notice",
            "review_key": finding.key,
            "task_id": binding.task_id,
            "author_session_sha256": hashlib.sha256(
                binding.author_session.encode("utf-8")
            ).hexdigest(),
            "reviewed_commit": finding.reviewed_commit,
            "current_commit": args.current_commit,
            "applicability": applies,
            "severity": finding.severity,
            "decision": decision,
            "decision_seq": decision_seq,
            "approval_subject": review_subject(finding, binding),
            "untrusted_text": "inspect with synapse review-feedback status --show-untrusted-body",
        },
        sort_keys=True,
    )
    delivered = asyncio.run(
        _deliver(
            uri=args.uri,
            name=args.name,
            target=binding.author_seat,
            payload=payload,
            msg_id=msg_id,
            token=(
                read_secret_file(args.token_file, flag="--token-file")
                if args.token_file
                else os.environ.get("SYNAPSE_TOKEN") or None
            ),
        )
    )
    if delivered:
        mark_routed(
            _store(args), finding.key, msg_id=msg_id, decision_seq=decision_seq, at=time.time()
        )
    print(json.dumps({"key": finding.key, "delivered": delivered, "client_msg_id": msg_id}))
    return 0 if delivered else 1


def _run(args: argparse.Namespace) -> int:
    try:
        return int(args.action(args))
    except (
        ReviewFeedbackError,
        SecurePathError,
        SecretFileError,
        ValueError,
        OSError,
        sqlite3.DatabaseError,
    ) as exc:
        print(f"review-feedback: {exc}", file=sys.stderr)
        return 2


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register private review binding, signed intake, inspection and routing."""
    parser = subparsers.add_parser("review-feedback", help="Route decided review to its author.")
    group = parser.add_subparsers(dest="review_command", required=True)
    bind = group.add_parser("bind", help="Bind one local commit to its author session.")
    bind.add_argument("--repo-path", required=True)
    bind.add_argument("--repository", required=True)
    bind.add_argument("--commit", required=True)
    bind.add_argument("--task-id", required=True)
    bind.add_argument("--author-seat", required=True)
    bind.add_argument("--author-session", required=True)
    bind.add_argument("--store")
    bind.set_defaults(action=_bind, func=_run)
    ingest = group.add_parser("ingest", help="Preserve one signed GitHub review webhook.")
    ingest.add_argument("--webhook-file", required=True)
    ingest.add_argument("--headers-file", required=True)
    ingest.add_argument("--secret-file", required=True)
    ingest.add_argument("--severity", required=True)
    ingest.add_argument("--evidence", required=True)
    ingest.add_argument("--verify", required=True)
    ingest.add_argument("--store")
    ingest.set_defaults(action=_ingest, func=_run)
    for command, action in (("status", _status), ("route", _route)):
        view = group.add_parser(command, help=f"{command} one recorded review finding.")
        view.add_argument("key")
        view.add_argument("--hub-db", required=True)
        view.add_argument("--db-key-file", help="Owner-only SQLCipher key file.")
        view.add_argument("--reviewer-seat", required=True)
        view.add_argument("--repo-path")
        view.add_argument("--current-commit")
        view.add_argument("--store")
        if command == "status":
            view.add_argument("--show-untrusted-body", action="store_true")
            view.add_argument("--show-author-session", action="store_true")
        else:
            view.add_argument("--name", required=True, help="Exact routing-service hub identity.")
            view.add_argument("--uri", default=default_hub_uri())
            view.add_argument("--token-file", help="Owner-only hub token file.")
        view.set_defaults(action=action, func=_run)
