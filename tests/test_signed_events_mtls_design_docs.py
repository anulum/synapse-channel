# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — signed event and mTLS design documentation tests
"""Guard the signed-event and mTLS federation design boundaries."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SIGNED_EVENTS_DOC = ROOT / "docs" / "signed-events-mtls.md"


def _read(path: Path) -> str:
    """Read a UTF-8 documentation file."""
    return path.read_text(encoding="utf-8")


def _collapsed(path: Path) -> str:
    """Return lowercase documentation text with normalized whitespace."""
    return " ".join(_read(path).lower().split())


def test_signed_events_mtls_design_is_publicly_discoverable() -> None:
    """The trust design must be linked from public security surfaces."""
    nav = _read(ROOT / "mkdocs.yml")
    readme = _read(ROOT / "README.md")
    security = _read(ROOT / "SECURITY.md")
    paranoid = _read(ROOT / "docs" / "paranoid-mode.md")

    assert "Signed events and mTLS: signed-events-mtls.md" in nav
    assert "docs/signed-events-mtls.md" in readme
    assert "docs/signed-events-mtls.md" in security
    assert "signed-events-mtls.md" in paranoid


def test_signed_events_mtls_design_covers_signing_and_replay() -> None:
    """The design must specify event signatures and replay controls."""
    text = _collapsed(SIGNED_EVENTS_DOC)

    required_controls = (
        "signed events",
        "runtime status",
        "eventsignaturetrustbundle",
        "synapsehub(..., require_per_message_auth=true, signed_event_trust_bundle=...)",
        "event signature",
        "canonical payload",
        "key id",
        "sequence binding",
        "timestamp window",
        "replay protection",
        "verification result",
    )
    for control in required_controls:
        assert control in text


def test_signed_events_mtls_design_covers_trusted_federation() -> None:
    """The design must describe trusted multi-host federation controls."""
    text = _collapsed(SIGNED_EVENTS_DOC)

    required_controls = (
        "mutual tls",
        "certificate pinning",
        "trusted peer",
        "mtlspeertrustbundle",
        "key rotation",
        "revocation",
        "multi-host",
        "cross-project",
        "trust bundle",
        "tls passthrough",
        "tls-terminating proxy",
        "synapse doctor --federation-path",
    )
    for control in required_controls:
        assert control in text


def test_signed_events_mtls_runtime_keeps_boundaries_clear() -> None:
    """The runtime must not claim broad federation security."""
    text = _collapsed(SIGNED_EVENTS_DOC)

    required_boundaries = (
        "operator-managed",
        "no cli trust-bundle import command yet",
        "does not encrypt payloads",
        "does not replace per-agent identity",
        "does not certify federation",
        "local-first tradeoff",
        "command-line trust-bundle import/export",
    )
    for boundary in required_boundaries:
        assert boundary in text
