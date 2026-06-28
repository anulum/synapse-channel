"""Guard the end-to-end encrypted channels design and boundaries."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
E2EE_DOC = ROOT / "docs" / "end-to-end-encrypted-channels.md"


def _read(path: Path) -> str:
    """Read a UTF-8 documentation file."""
    return path.read_text(encoding="utf-8")


def _collapsed(path: Path) -> str:
    """Return lowercase documentation text with normalized whitespace."""
    return " ".join(_read(path).lower().split())


def test_e2ee_channels_design_is_publicly_discoverable() -> None:
    """The encrypted-channel design must be linked from security docs."""
    nav = _read(ROOT / "mkdocs.yml")
    readme = _read(ROOT / "README.md")
    security = _read(ROOT / "SECURITY.md")
    paranoid = _read(ROOT / "docs" / "paranoid-mode.md")

    assert "Encrypted channels: end-to-end-encrypted-channels.md" in nav
    assert "docs/end-to-end-encrypted-channels.md" in readme
    assert "docs/end-to-end-encrypted-channels.md" in security
    assert "end-to-end-encrypted-channels.md" in paranoid


def test_e2ee_channels_design_scopes_selected_payloads() -> None:
    """The design must identify which payload classes can be encrypted."""
    text = _collapsed(E2EE_DOC)

    required_payloads = (
        "selected payloads",
        "direct messages",
        "private progress notes",
        "handoff checkpoints",
        "a2a artifacts",
        "not every hub frame",
        "metadata remains visible",
    )
    for payload in required_payloads:
        assert payload in text


def test_e2ee_channels_design_covers_key_management() -> None:
    """The design must cover worktree and project key operations."""
    text = _collapsed(E2EE_DOC)

    required_key_terms = (
        "per-project keys",
        "per-worktree keys",
        "recipient set",
        "key discovery",
        "key rotation",
        "member removal",
        "recovery phrase",
        "device loss",
    )
    for term in required_key_terms:
        assert term in text


def test_e2ee_channels_design_keeps_hub_blind_boundaries_clear() -> None:
    """The design must not claim encryption exists or hides routing metadata."""
    text = _collapsed(E2EE_DOC)

    required_boundaries = (
        "broader encrypted-channel profile remains a design target",
        "runtime tranche does not replace at-rest encryption",
        "hub cannot read plaintext",
        "does not hide routing metadata",
        "does not replace at-rest encryption",
        "does not protect compromised endpoints",
        "local-first tradeoff",
    )
    for boundary in required_boundaries:
        assert boundary in text


def test_e2ee_channels_doc_tracks_runtime_tranche() -> None:
    """The public E2EE doc must distinguish shipped runtime from remaining gaps."""
    text = _collapsed(E2EE_DOC)

    required_runtime_terms = (
        "implemented runtime tranche",
        "synapse send --encrypt-key-file",
        "synapse listen --decrypt-key-file",
        "aes-256-gcm",
        "hub routes ciphertext",
        "does not manage key discovery",
        "does not rotate keys",
    )
    for term in required_runtime_terms:
        assert term in text
