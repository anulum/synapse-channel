# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — owner-local immutable review custody
"""Keep author bindings, original webhook bytes and route receipts owner-local."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
from contextlib import closing
from dataclasses import asdict
from pathlib import Path
from typing import Any

from synapse_channel.core.review_feedback import AuthorBinding, ReviewFeedbackError, ReviewFinding
from synapse_channel.core.secure_path import (
    SecurePathError,
    apply_owner_only_dir,
    apply_owner_only_file,
    assert_owner_only_dir_path,
    assert_owner_only_file_path,
)


def default_review_store() -> Path:
    """Return an owner-local review store outside the Git checkout and hub."""
    state = os.environ.get("XDG_STATE_HOME", "")
    root = Path(state) if state and Path(state).is_absolute() else Path.home() / ".local" / "state"
    return root / "synapse-channel" / "review-feedback" / "reviews.sqlite3"


def _open(path: Path) -> sqlite3.Connection:
    parent = path.parent
    db: sqlite3.Connection | None = None
    try:
        if not parent.exists():
            parent.mkdir(mode=0o700, parents=True)
            apply_owner_only_dir(parent)
        assert_owner_only_dir_path(parent, purpose="review directory")
        if path.is_symlink():
            raise ReviewFeedbackError("review store must not be a symlink")
        existed = path.exists()
        if existed:
            assert_owner_only_file_path(path, purpose="review store")
        db = sqlite3.connect(path, timeout=5.0)
        db.row_factory = sqlite3.Row
        if not existed:
            apply_owner_only_file(path)
        version = int(db.execute("PRAGMA user_version").fetchone()[0])
        if version == 0 and not existed:
            db.executescript(
                "CREATE TABLE bindings (repository TEXT NOT NULL, commit_sha TEXT NOT NULL, "
                "record_json TEXT NOT NULL, PRIMARY KEY(repository,commit_sha));"
                "CREATE TABLE findings (review_key TEXT PRIMARY KEY, repository TEXT NOT NULL, "
                "source_kind TEXT NOT NULL, review_id INTEGER NOT NULL, delivery_id TEXT NOT NULL, "
                "record_json TEXT NOT NULL, webhook_body BLOB NOT NULL, "
                "webhook_signature TEXT NOT NULL, observed_at REAL NOT NULL, "
                "routed_at REAL, route_msg_id TEXT, route_decision_seq INTEGER, "
                "UNIQUE(repository,source_kind,review_id), UNIQUE(repository,delivery_id));"
                "PRAGMA user_version=1;"
            )
        elif version != 1:
            raise ReviewFeedbackError(f"unsupported review store version {version}")
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ReviewFeedbackError("review store integrity check failed")
        return db
    except ReviewFeedbackError:
        if db is not None:
            db.close()
        raise
    except (OSError, sqlite3.DatabaseError, SecurePathError) as exc:
        if db is not None:
            db.close()
        raise ReviewFeedbackError("cannot open private review store") from exc


def save_binding(path: Path, binding: AuthorBinding) -> bool:
    """Insert one immutable author/session binding; return false on exact replay."""
    encoded = json.dumps(asdict(binding), sort_keys=True, separators=(",", ":"))
    with closing(_open(path)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        prior = db.execute(
            "SELECT record_json FROM bindings WHERE repository=? AND commit_sha=?",
            (binding.repository, binding.commit),
        ).fetchone()
        if prior is not None:
            if str(prior["record_json"]) != encoded:
                raise ReviewFeedbackError("author binding is immutable")
            return False
        db.execute(
            "INSERT INTO bindings(repository,commit_sha,record_json) VALUES(?,?,?)",
            (binding.repository, binding.commit, encoded),
        )
    return True


def get_binding(path: Path, *, repository: str, commit: str) -> AuthorBinding | None:
    """Read the exact commit binding or report that no author session exists."""
    with closing(_open(path)) as db:
        row = db.execute(
            "SELECT record_json FROM bindings WHERE repository=? AND commit_sha=?",
            (repository, commit),
        ).fetchone()
    return None if row is None else AuthorBinding(**json.loads(str(row["record_json"])))


def save_finding(
    path: Path,
    finding: ReviewFinding,
    *,
    webhook_body: bytes,
    webhook_signature: str,
    observed_at: float,
) -> bool:
    """Preserve original authenticated bytes exactly once per GitHub delivery."""
    if (
        len(webhook_body) > 1024 * 1024
        or hashlib.sha256(webhook_body).hexdigest() != finding.source_sha256
        or re.fullmatch(r"sha256=[0-9a-f]{64}", webhook_signature) is None
        or not math.isfinite(observed_at)
        or observed_at <= 0
    ):
        raise ReviewFeedbackError("review webhook evidence is invalid")
    encoded = json.dumps(asdict(finding), sort_keys=True, separators=(",", ":"))
    with closing(_open(path)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        prior = db.execute(
            "SELECT record_json,webhook_body,webhook_signature FROM findings "
            "WHERE review_key=? OR (repository=? AND delivery_id=?)",
            (finding.key, finding.repository, finding.delivery_id),
        ).fetchone()
        if prior is not None:
            if (
                str(prior["record_json"]) != encoded
                or bytes(prior["webhook_body"]) != webhook_body
                or str(prior["webhook_signature"]) != webhook_signature
            ):
                raise ReviewFeedbackError("review identity reused for different evidence")
            return False
        db.execute(
            "INSERT INTO findings(review_key,repository,source_kind,review_id,delivery_id,"
            "record_json,webhook_body,webhook_signature,observed_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                finding.key,
                finding.repository,
                finding.source_kind,
                finding.review_id,
                finding.delivery_id,
                encoded,
                webhook_body,
                webhook_signature,
                observed_at,
            ),
        )
    return True


def get_finding(path: Path, key: str) -> tuple[ReviewFinding, dict[str, Any]] | None:
    """Read immutable finding data and its local routing receipt."""
    with closing(_open(path)) as db:
        row = db.execute(
            "SELECT record_json,observed_at,routed_at,route_msg_id,route_decision_seq "
            "FROM findings WHERE review_key=?",
            (key,),
        ).fetchone()
    if row is None:
        return None
    return ReviewFinding(**json.loads(str(row["record_json"]))), {
        "observed_at": float(row["observed_at"]),
        "routed_at": None if row["routed_at"] is None else float(row["routed_at"]),
        "route_msg_id": row["route_msg_id"],
        "route_decision_seq": row["route_decision_seq"],
    }


def list_findings(path: Path) -> tuple[tuple[ReviewFinding, dict[str, Any]], ...]:
    """List immutable findings in bounded source order for a private view."""
    with closing(_open(path)) as db:
        keys = [
            str(row["review_key"])
            for row in db.execute("SELECT review_key FROM findings ORDER BY observed_at,review_key")
        ]
    rows = [get_finding(path, key) for key in keys]
    return tuple(row for row in rows if row is not None)


def mark_routed(path: Path, key: str, *, msg_id: str, decision_seq: int, at: float) -> bool:
    """Record one confirmed decision revision; retries reuse its message id."""
    if not msg_id or len(msg_id) > 128 or at <= 0 or decision_seq < 1:
        raise ReviewFeedbackError("route receipt is invalid")
    with closing(_open(path)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        prior = db.execute(
            "SELECT route_msg_id,route_decision_seq FROM findings WHERE review_key=?", (key,)
        ).fetchone()
        if prior is None:
            raise ReviewFeedbackError("review finding is missing")
        previous = prior["route_decision_seq"]
        if previous is not None:
            if decision_seq < int(previous):
                raise ReviewFeedbackError("route decision revision moved backwards")
            if decision_seq == int(previous):
                if str(prior["route_msg_id"]) != msg_id:
                    raise ReviewFeedbackError("route identity changed after confirmation")
                return False
        db.execute(
            "UPDATE findings SET routed_at=?,route_msg_id=?,route_decision_seq=? "
            "WHERE review_key=?",
            (at, msg_id, decision_seq, key),
        )
        return True
