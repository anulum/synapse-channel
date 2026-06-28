"""Guard the private-channel design and namespace boundaries."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_DOC = ROOT / "docs" / "private-channels.md"


def _read(path: Path) -> str:
    """Read a UTF-8 documentation file."""
    return path.read_text(encoding="utf-8")


def _collapsed(path: Path) -> str:
    """Return lowercase documentation text with normalized whitespace."""
    return " ".join(_read(path).lower().split())


def test_private_channels_design_is_publicly_discoverable() -> None:
    """The private-channel design must be linked from public security docs."""
    nav = _read(ROOT / "mkdocs.yml")
    readme = _read(ROOT / "README.md")
    security = _read(ROOT / "SECURITY.md")
    encrypted_channels = _read(ROOT / "docs" / "end-to-end-encrypted-channels.md")

    assert "Private channels: private-channels.md" in nav
    assert "docs/private-channels.md" in readme
    assert "docs/private-channels.md" in security
    assert "private-channels.md" in encrypted_channels


def test_private_channels_design_defines_namespaces() -> None:
    """The design must define the first private-channel namespace model."""
    text = _collapsed(PRIVATE_DOC)

    required_namespaces = (
        "project channel",
        "worktree channel",
        "task channel",
        "direct channel",
        "channel id",
        "membership list",
        "default public channel",
    )
    for namespace in required_namespaces:
        assert namespace in text


def test_private_channels_design_covers_membership_and_retention() -> None:
    """The design must cover membership, routing, history, and retention."""
    text = _collapsed(PRIVATE_DOC)

    required_controls = (
        "join policy",
        "leave policy",
        "invitation",
        "membership audit",
        "history visibility",
        "retention boundary",
        "relay log filtering",
        "event-query filtering",
    )
    for control in required_controls:
        assert control in text


def test_private_channels_design_keeps_boundaries_clear() -> None:
    """The design must not claim private channels already enforce secrecy."""
    text = _collapsed(PRIVATE_DOC)

    required_boundaries = (
        "design target",
        "not implemented yet",
        "does not encrypt payloads",
        "does not replace end-to-end encrypted channels",
        "does not create cryptographic identity",
        "hub can still see metadata",
        "trusted local hub",
    )
    for boundary in required_boundaries:
        assert boundary in text
