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


def test_a2a_deployment_threat_model_pins_exposed_bridge_controls() -> None:
    """The A2A deployment review must keep exposed-bridge controls explicit."""
    threat_model = _single_spaced(_read_repo_text("docs/a2a-deployment-threat-model.md"))
    combined = _single_spaced(
        "\n".join(
            [
                _read_repo_text("README.md"),
                _read_repo_text("docs/cli.md"),
                _read_repo_text("SECURITY.md"),
            ]
        )
    )

    assert "--bearer-auth --a2a-token" in threat_model
    assert "--insecure-off-loopback" in threat_model
    assert "DNS rebinding" in threat_model
    assert "redact `Authorization`" in threat_model
    assert "operator deployment sign-off" in combined
    assert "A2A deployment threat model" in combined


def test_file_scope_docs_describe_literal_prefix_overlap_not_glob() -> None:
    # `core/scoping.py` states wildcard-glob algebra is intentionally out of scope and
    # declared paths are literal files or directory prefixes; SECURITY.md must match the
    # code and never regress to the stale "glob overlap" wording.
    prose = _single_spaced(_read_repo_text("SECURITY.md"))

    assert "literal path or directory-prefix overlap" in prose
    assert "wildcard-glob algebra is intentionally out of scope" in prose
    assert "glob overlap" not in prose


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


def test_update_check_docs_keep_network_access_opt_in() -> None:
    combined = _single_spaced(
        "\n".join(
            [
                _read_repo_text("README.md"),
                _read_repo_text("docs/installation.md"),
                _read_repo_text("docs/faq.md"),
            ]
        )
    )

    assert "`synapse --version` is network-silent by default" in combined
    assert "SYNAPSE_UPDATE_CHECK=1" in combined
    assert "disabled unless `SYNAPSE_UPDATE_CHECK=1` is present" in combined


def test_takeover_docs_describe_payload_free_audit_logs() -> None:
    combined = _single_spaced(
        "\n".join(
            [
                _read_repo_text("README.md"),
                _read_repo_text("docs/cli.md"),
                _read_repo_text("docs/deployment.md"),
                _read_repo_text("docs/troubleshooting.md"),
            ]
        )
    )

    assert "logs takeover/conflict outcomes with sender, remote host, and close reason" in combined
    assert "without message payloads" in combined or "without chat or task payloads" in combined
    assert "cooldown refusals" in combined


def test_federation_proxy_docs_keep_pinning_boundary_explicit() -> None:
    """Public security docs must distinguish proxy TLS from hub mTLS."""
    combined = _single_spaced(
        "\n".join(
            [
                _read_repo_text("docs/deployment.md"),
                _read_repo_text("docs/federated-trust-model.md"),
                _read_repo_text("docs/signed-events-mtls.md"),
                _read_repo_text("docs/cli.md"),
            ]
        )
    )

    assert "TLS-terminating reverse proxy" in combined
    assert "not the hub certificate" in combined
    assert "not a hub mTLS path" in combined
    assert "`direct-mtls`, `tls-passthrough`, `tailnet`, and `tls-terminating-proxy`" in combined


def test_benchmark_docs_describe_heap_expiry_not_stale_linear_sweeper() -> None:
    benchmarks = _read_repo_text("docs/benchmarks.md")

    assert "min-heap" in benchmarks
    assert "now-stale expiry model" in benchmarks
    assert "future heap" not in benchmarks.lower()


def test_benchmark_docs_pin_scope_scan_indexing_decision() -> None:
    benchmarks = _single_spaced(_read_repo_text("docs/benchmarks.md"))

    assert "keep the scope-conflict scan linear inside the local-first envelope" in benchmarks
    assert "local-first ceiling of 100 active claims" in benchmarks
    assert "Loaded workstation evidence" in benchmarks


def test_capability_card_history_security_boundary_matches_runtime() -> None:
    """Security prose must describe both history modes without granting authority."""
    prose = _single_spaced(_read_repo_text("SECURITY.md"))

    assert "bounded in-memory default" in prose
    assert "owner-only SQLite store" in prose
    assert "`--capability-card-history-db`" in prose
    assert "persists replay and downgrade floors across hub restarts" in prose
    assert "runtime persistence failure reports `history_unavailable`" in prose
    assert "no enforcement flag exists" in prose
    assert "a verified card does not authorize tools" in prose
    assert "currently in memory" not in prose
