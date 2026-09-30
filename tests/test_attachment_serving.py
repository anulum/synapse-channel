# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — owner policy replacement, exact grants and fail-closed parsing
"""Exercise public attachment policy decisions using real owner-controlled files."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from synapse_channel.core.attachment_serving import AttachmentServingPolicy
from synapse_channel.core.attachment_store import AttachmentError

DIGEST = "a" * 64
GRANT: dict[str, object] = {
    "recipient_hub": "hub-b",
    "scope": "PROJECT",
    "digest": DIGEST,
    "expires_at": 200.0,
}


def _write(path: Path, document: object) -> None:
    path.write_text(json.dumps(document), encoding="utf-8")
    path.chmod(0o600)


def test_exact_grants_expire_and_replacements_revoke(tmp_path: Path) -> None:
    path = tmp_path / "grants.json"
    _write(path, {"version": 1, "grants": [GRANT]})
    policy = AttachmentServingPolicy(path, clock=lambda: 100.0)
    assert policy.load()[0].expires_at == 200.0
    assert policy.allows("hub-b", "PROJECT", DIGEST)
    for recipient, scope, digest in (
        ("hub-c", "PROJECT", DIGEST),
        ("hub-b", "OTHER", DIGEST),
        ("hub-b", "PROJECT", "b" * 64),
    ):
        assert not policy.allows(recipient, scope, digest)
    assert not AttachmentServingPolicy(path, clock=lambda: 200.0).allows("hub-b", "PROJECT", DIGEST)
    assert not AttachmentServingPolicy(path, clock=lambda: float("nan")).allows(
        "hub-b", "PROJECT", DIGEST
    )
    replacement = tmp_path / "replacement.json"
    _write(replacement, {"version": 1, "grants": []})
    replacement.replace(path)
    assert not policy.allows("hub-b", "PROJECT", DIGEST)
    path.unlink()
    assert not policy.allows("hub-b", "PROJECT", DIGEST)


@pytest.mark.parametrize(
    "document",
    [
        None,
        [],
        {},
        {"version": True, "grants": []},
        {"version": 2, "grants": []},
        {"version": 1, "grants": {}},
        {"version": 1, "grants": [], "extra": 1},
        {"version": 1, "grants": [GRANT, GRANT]},
        {"version": 1, "grants": [GRANT] * 257},
        {"version": 1, "grants": [None]},
        {"version": 1, "grants": [{}]},
        *[
            {"version": 1, "grants": [{**GRANT, field: value}]}
            for field, value in (
                ("recipient_hub", 2),
                ("recipient_hub", "*"),
                ("recipient_hub", "PROJ/seat"),
                ("scope", None),
                ("scope", "../PROJECT"),
                ("digest", []),
                ("digest", "A" * 64),
                ("expires_at", True),
                ("expires_at", "200"),
                ("expires_at", 0),
                ("expires_at", float("inf")),
                ("expires_at", float("nan")),
                ("expires_at", 10**400),
            )
        ],
    ],
)
def test_malformed_documents_refuse_every_grant(tmp_path: Path, document: object) -> None:
    path = tmp_path / "grants.json"
    _write(path, document)
    policy = AttachmentServingPolicy(path)
    with pytest.raises(AttachmentError, match="invalid attachment recipient policy"):
        policy.load()
    assert not policy.allows("hub-b", "PROJECT", DIGEST)


@pytest.mark.parametrize(
    "raw",
    [
        '{"version":1,"version":1,"grants":[]}',
        '{"version":1,"grants":[{"scope":"X","scope":"Y"}]}',
        "{not-json",
        "[" * 5000,
        " " * 65_537,
    ],
)
def test_ambiguous_or_unbounded_files_fail_closed(tmp_path: Path, raw: str) -> None:
    path = tmp_path / "grants.json"
    path.write_text(raw, encoding="utf-8")
    path.chmod(0o600)
    with pytest.raises(AttachmentError, match="invalid attachment recipient policy"):
        AttachmentServingPolicy(path).load()


def test_public_or_symlinked_policy_is_not_authority(tmp_path: Path) -> None:
    path = tmp_path / "grants.json"
    _write(path, {"version": 1, "grants": [GRANT]})
    link = tmp_path / "link.json"
    link.symlink_to(path)
    assert not AttachmentServingPolicy(link).allows("hub-b", "PROJECT", DIGEST)
    path.chmod(0o644)
    assert not AttachmentServingPolicy(path).allows("hub-b", "PROJECT", DIGEST)
