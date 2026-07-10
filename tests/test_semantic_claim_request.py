# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — combined semantic claim request regressions
"""Keep selector, diff, companion, and evidence behavior out of git transport."""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from synapse_channel.git import semantic_claim_request, semantic_claims, semantic_diff


def _selector_record() -> semantic_claims.SemanticClaimRecord:
    return semantic_claims.SemanticClaimRecord(
        selector="symbol:pkg.mod.first",
        kind="symbol",
        value="pkg.mod.first",
        sources=("src/pkg/mod.py",),
        modules=("pkg.mod",),
        symbols=("first",),
        semantic_scopes=("src/pkg/mod.py/.synapse-symbol/first",),
        tests=("tests/test_mod.py",),
        generated=(),
        claim_paths=("src/pkg/mod.py/.synapse-symbol/first", "tests/test_mod.py"),
    )


def _diff_record() -> semantic_diff.SemanticDiffRecord:
    return semantic_diff.SemanticDiffRecord(
        status="M",
        source="src/pkg/other.py",
        old_source="src/pkg/other.py",
        language="python",
        symbols=("second",),
        semantic_scopes=("src/pkg/other.py/.synapse-symbol/second",),
        claim_paths=("src/pkg/other.py/.synapse-symbol/second",),
        narrowed=True,
        reason="all changed lines map to named declarations",
    )


def test_request_combines_selectors_diff_and_deduplicated_companions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        semantic_claims,
        "resolve_selectors",
        lambda _root, _items: (_selector_record(),),
    )
    monkeypatch.setattr(
        semantic_diff,
        "resolve_git_diff",
        lambda *_args, **_kwargs: (_diff_record(),),
    )
    monkeypatch.setattr(
        semantic_claims,
        "companion_claim_paths",
        lambda _root, _sources: ("tests/test_mod.py", "README.md"),
    )

    request = semantic_claim_request.resolve_semantic_request(
        tmp_path,
        selectors=("symbol:pkg.mod.first",),
        diff_base="main",
        diff_head="HEAD",
        diff_paths=("src/pkg",),
    )

    assert request.claim_paths == (
        "src/pkg/mod.py/.synapse-symbol/first",
        "tests/test_mod.py",
        "src/pkg/other.py/.synapse-symbol/second",
        "README.md",
    )
    evidence = semantic_claim_request.request_evidence(request)
    assert [record["kind"] for record in evidence] == ["symbol", "diff-summary"]
    assert evidence[1]["companion_claim_paths"] == ["tests/test_mod.py", "README.md"]


def test_selector_only_evidence_keeps_the_existing_list_shape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        semantic_claims,
        "resolve_selectors",
        lambda _root, _items: (_selector_record(),),
    )
    monkeypatch.setattr(semantic_claims, "companion_claim_paths", lambda _root, _sources: ())

    request = semantic_claim_request.resolve_semantic_request(
        tmp_path,
        selectors=("symbol:pkg.mod.first",),
    )

    assert semantic_claim_request.request_evidence(request) == semantic_claims.records_to_json(
        (_selector_record(),)
    )


def test_diff_options_without_a_base_are_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="requires --diff-base"):
        semantic_claim_request.resolve_semantic_request(tmp_path, diff_head="HEAD")
    with pytest.raises(ValueError, match="requires --diff-base"):
        semantic_claim_request.resolve_semantic_request(tmp_path, diff_paths=("src",))


def test_evidence_writer_is_atomic_owner_only_and_cleans_failed_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = semantic_claim_request.SemanticClaimRequest((), (), None, None, (), ())
    destination = tmp_path / "evidence" / "semantic.json"

    written = semantic_claim_request.write_semantic_evidence(
        request,
        tmp_path,
        "evidence/semantic.json",
    )

    assert written == destination
    assert json.loads(destination.read_text(encoding="utf-8")) == []
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600

    def refuse(_source: object, _destination: object) -> None:
        raise OSError("replace refused")

    monkeypatch.setattr("synapse_channel.git.semantic_claim_request.os.replace", refuse)
    with pytest.raises(OSError, match="replace refused"):
        semantic_claim_request.write_semantic_evidence(request, tmp_path, "other.json")
    assert not (tmp_path / "other.json").exists()
    assert not list(tmp_path.glob(".other.json.*.tmp"))
