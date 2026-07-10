# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — combine semantic selectors and diff-derived claim evidence
"""Build one local semantic claim request without involving the hub.

Explicit selectors and tree-sitter diff inference remain independent analyzers.
This module joins their claim paths, adds owning tests and generated outputs for
diff sources, and writes optional receipt evidence atomically with owner-only
permissions. ``gitclaim`` stays a thin transport adapter.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from synapse_channel.git import semantic_claims, semantic_diff


@dataclass(frozen=True)
class SemanticClaimRequest:
    """Resolved local semantic claim inputs and their combined path scope."""

    selector_records: tuple[semantic_claims.SemanticClaimRecord, ...]
    diff_records: tuple[semantic_diff.SemanticDiffRecord, ...]
    diff_base: str | None
    diff_head: str | None
    companion_paths: tuple[str, ...]
    claim_paths: tuple[str, ...]


def resolve_semantic_request(
    repo_root: Path,
    *,
    selectors: tuple[str, ...] = (),
    diff_base: str | None = None,
    diff_head: str | None = None,
    diff_paths: tuple[str, ...] = (),
) -> SemanticClaimRequest:
    """Resolve explicit selectors and an optional tracked Git diff.

    ``diff_head`` and path filters require ``diff_base`` so an omitted base can
    never silently change the comparison being claimed.
    """
    if diff_base is None and (diff_head is not None or diff_paths):
        raise ValueError("semantic diff --head/--path requires --diff-base")
    selector_records = semantic_claims.resolve_selectors(repo_root, selectors) if selectors else ()
    diff_records = (
        semantic_diff.resolve_git_diff(
            repo_root,
            base=diff_base,
            head=diff_head,
            paths=diff_paths,
        )
        if diff_base is not None
        else ()
    )
    companions = (
        semantic_claims.companion_claim_paths(
            repo_root,
            tuple(record.source for record in diff_records),
        )
        if diff_records
        else ()
    )
    paths = (
        *(path for record in selector_records for path in record.claim_paths),
        *(path for record in diff_records for path in record.claim_paths),
        *companions,
    )
    return SemanticClaimRequest(
        selector_records=selector_records,
        diff_records=diff_records,
        diff_base=diff_base,
        diff_head=diff_head,
        companion_paths=companions,
        claim_paths=tuple(dict.fromkeys(paths)),
    )


def request_evidence(request: SemanticClaimRequest) -> list[dict[str, object]]:
    """Return backward-compatible selector records plus optional diff evidence."""
    evidence = semantic_claims.records_to_json(request.selector_records)
    if request.diff_base is not None:
        evidence.append(
            {
                "kind": "diff-summary",
                "base": request.diff_base,
                "head": request.diff_head,
                "records": semantic_diff.records_to_json(request.diff_records),
                "companion_claim_paths": list(request.companion_paths),
                "claim_paths": list(
                    dict.fromkeys(
                        (
                            *(
                                path
                                for record in request.diff_records
                                for path in record.claim_paths
                            ),
                            *request.companion_paths,
                        )
                    )
                ),
                "note": ("tree-sitter evidence; incomplete mappings widen to whole-file claims"),
            }
        )
    return evidence


def write_semantic_evidence(
    request: SemanticClaimRequest,
    repo_root: Path,
    evidence_json: str,
) -> Path:
    """Atomically write owner-only receipt evidence and return its path."""
    destination = Path(evidence_json)
    if not destination.is_absolute():
        destination = repo_root / destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(request_evidence(request), handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return destination
