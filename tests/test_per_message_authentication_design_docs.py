# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — per-message authentication design documentation tests
"""Guard the per-message authentication design boundaries."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUTH_DOC = ROOT / "docs" / "per-message-authentication.md"


def _read(path: Path) -> str:
    """Read a UTF-8 documentation file."""
    return path.read_text(encoding="utf-8")


def _collapsed(path: Path) -> str:
    """Return lowercase documentation text with normalized whitespace."""
    return " ".join(_read(path).lower().split())


def test_per_message_authentication_design_is_publicly_discoverable() -> None:
    """The authentication design must be linked from public protocol docs."""
    nav = _read(ROOT / "mkdocs.yml")
    readme = _read(ROOT / "README.md")
    security = _read(ROOT / "SECURITY.md")
    paranoid = _read(ROOT / "docs" / "paranoid-mode.md")
    protocol = _read(ROOT / "docs" / "protocol.md")

    assert "Per-message authentication: per-message-authentication.md" in nav
    assert "docs/per-message-authentication.md" in readme
    assert "docs/per-message-authentication.md" in security
    assert "per-message-authentication.md" in paranoid
    assert "per-message-authentication.md" in protocol


def test_per_message_authentication_design_defines_frame_authentication() -> None:
    """The design must define frame-level authentication material."""
    text = _collapsed(AUTH_DOC)

    required_controls = (
        "per-message authentication",
        "websocket connect authentication",
        "authenticated frame",
        "canonical frame",
        "message authentication code",
        "signature",
        "key id",
        "sender binding",
    )
    for control in required_controls:
        assert control in text


def test_per_message_authentication_design_covers_replay_and_rotation() -> None:
    """The design must cover replay controls and key lifecycle."""
    text = _collapsed(AUTH_DOC)

    required_controls = (
        "nonce",
        "sequence binding",
        "timestamp window",
        "replay cache",
        "idempotency key",
        "key rotation",
        "revocation",
        "verification result",
    )
    for control in required_controls:
        assert control in text


def test_per_message_authentication_design_keeps_boundaries_clear() -> None:
    """The design must not claim transport security before implementation."""
    text = _collapsed(AUTH_DOC)

    required_boundaries = (
        "design target",
        "not implemented yet",
        "does not encrypt payloads",
        "does not replace tls",
        "does not replace signed events",
        "does not replace per-agent identity",
        "local-first tradeoff",
    )
    for boundary in required_boundaries:
        assert boundary in text
