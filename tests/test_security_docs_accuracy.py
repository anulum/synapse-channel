# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — security documentation accuracy tests

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _read_repo_text(relative_path: str) -> str:
    """Read a repository text file for public documentation contract checks."""
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def _single_spaced(text: str) -> str:
    """Normalize documentation whitespace for prose phrase checks."""
    return " ".join(text.split())


def test_security_policy_uses_evidence_bounded_a2a_claim_language() -> None:
    security = _read_repo_text("SECURITY.md")
    prose = _single_spaced(security)

    assert "certified A2A implementation" not in prose
    assert "not externally validated for full A2A conformance" in prose


def test_metrics_token_docs_keep_query_tokens_opt_in() -> None:
    combined = "\n".join(
        [
            _read_repo_text("README.md"),
            _read_repo_text("SECURITY.md"),
            _read_repo_text("docs/cli.md"),
        ]
    )

    assert "Authorization: Bearer <token>" in combined
    assert "--metrics-query-token-ok" in combined
    assert "query token" in combined


def test_benchmark_docs_describe_heap_expiry_not_stale_linear_sweeper() -> None:
    benchmarks = _read_repo_text("docs/benchmarks.md")

    assert "min-heap" in benchmarks
    assert "now-stale expiry model" in benchmarks
    assert "future heap" not in benchmarks.lower()
