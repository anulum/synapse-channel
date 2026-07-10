# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — federated trust model design documentation tests
"""Guard the federated trust model design boundaries and discoverability."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "federated-trust-model.md"


def _read(path: Path) -> str:
    """Read a UTF-8 documentation file."""
    return path.read_text(encoding="utf-8")


def _collapsed(path: Path) -> str:
    """Return lowercase documentation text with normalized whitespace."""
    return " ".join(_read(path).lower().split())


def test_federated_trust_model_is_publicly_discoverable() -> None:
    """The federation design must be linked from public surfaces."""
    assert "Federated trust model: federated-trust-model.md" in _read(ROOT / "mkdocs.yml")
    assert "docs/federated-trust-model.md" in _read(ROOT / "README.md")
    assert "docs/federated-trust-model.md" in _read(ROOT / "SECURITY.md")


def test_federated_trust_model_states_runtime_status_and_dependencies() -> None:
    """The design must ground itself in the shipped single-domain primitives."""
    text = _collapsed(DOC)
    assert "runtime status" in text
    assert "opt-in policy, persistence, lifecycle, exchange, frame-authorisation" in text
    # The exchange transport shipped (offer/fetch); the trust decision itself is
    # declared permanently out-of-band, not a pending implementation gap.
    assert "out-of-band by design" in text
    assert "synapse federation fetch" in text
    assert "--federation-offer" in text
    assert "fingerprint" in text
    for dependency in (
        "identity-and-acl.md",
        "signed-events-mtls.md",
        "signed-capability-cards.md",
        "agent-trust-graph.md",
    ):
        assert dependency in _read(DOC)


def test_federated_trust_model_pins_trust_domain_and_authorization_boundaries() -> None:
    """The core federation concepts and scoping must be present."""
    text = _collapsed(DOC)
    assert "trust domain" in text
    assert "deny-by-default" in text
    assert "out-of-band" in text
    assert "bounded local scope" in text
    assert "one authorisation path" in text
    assert "tls-terminating reverse proxy" in text
    assert "not the hub certificate" in text
    assert "synapse doctor --federation-path peer=mode" in text
    assert "posix wall-clock epoch seconds" in text
    assert "never with process-relative monotonic time" in text


def test_federated_trust_model_keeps_explicit_non_goals() -> None:
    """The boundaries must refuse the dangerous federation defaults."""
    text = _collapsed(DOC)
    assert "not a certificate authority" in text
    assert "trust-on-first-use" in text
    assert "authorise untrusted organisations" in text
    assert "local-first default" in text
    # receipts stay advisory across a domain boundary
    assert "advisory evidence" in text
    assert "never auto-approves" in text
