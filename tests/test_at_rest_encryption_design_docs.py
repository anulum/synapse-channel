"""Guard the at-rest encryption design and its security boundaries."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENCRYPTION_DOC = ROOT / "docs" / "at-rest-encryption.md"


def _read(path: Path) -> str:
    """Read a UTF-8 documentation file."""
    return path.read_text(encoding="utf-8")


def _collapsed(path: Path) -> str:
    """Return lowercase documentation text with normalized whitespace."""
    return " ".join(_read(path).lower().split())


def test_at_rest_encryption_design_is_publicly_discoverable() -> None:
    """The encryption design must be linked from nav and security surfaces."""
    nav = _read(ROOT / "mkdocs.yml")
    readme = _read(ROOT / "README.md")
    security = _read(ROOT / "SECURITY.md")
    paranoid = _read(ROOT / "docs" / "paranoid-mode.md")

    assert "At-rest encryption: at-rest-encryption.md" in nav
    assert "docs/at-rest-encryption.md" in readme
    assert "docs/at-rest-encryption.md" in security
    assert "at-rest-encryption.md" in paranoid


def test_at_rest_encryption_design_scopes_storage_surfaces() -> None:
    """The design must name each local storage surface that needs coverage."""
    text = _collapsed(ENCRYPTION_DOC)

    required_surfaces = (
        "sqlite event store",
        "wal and shm sidecars",
        "relay logs",
        "a2a state files",
        "cursor files",
        "archive reports",
        "temporary files",
        "backups",
    )
    for surface in required_surfaces:
        assert surface in text


def test_at_rest_encryption_design_covers_key_lifecycle() -> None:
    """The design must cover key storage, rotation, backup, and recovery."""
    text = _collapsed(ENCRYPTION_DOC)

    required_lifecycle = (
        "key storage",
        "key derivation",
        "key rotation",
        "backup recovery",
        "lost-key recovery",
        "passphrase",
        "platform keyring",
        "file permissions",
    )
    for phrase in required_lifecycle:
        assert phrase in text


def test_at_rest_encryption_design_keeps_boundaries_clear() -> None:
    """The doc must mark the implemented foundation and keep boundaries honest.

    The encryption primitive, key-file management, and whole-file runtime
    profile ship now, so the doc names them as implemented. The transparent live
    SQLite boundary and the perpetual security boundaries must remain stated.
    """
    text = _collapsed(ENCRYPTION_DOC)

    required_boundaries = (
        "implemented runtime profile",
        "transparent live database opening",
        "is not implemented",
        "does not protect data while the hub is running",
        "does not replace filesystem permissions",
        "does not solve multi-tenant isolation",
    )
    for phrase in required_boundaries:
        assert phrase in text
