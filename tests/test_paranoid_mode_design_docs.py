"""Guard the paranoid-mode design and its public security boundaries."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PARANOID_DOC = ROOT / "docs" / "paranoid-mode.md"


def _read(path: Path) -> str:
    """Read a UTF-8 documentation file."""
    return path.read_text(encoding="utf-8")


def _collapsed(path: Path) -> str:
    """Return lowercase documentation text with normalized whitespace."""
    return " ".join(_read(path).lower().split())


def test_paranoid_mode_design_is_publicly_discoverable() -> None:
    """The design page must be linked from public security and deployment docs."""
    nav = _read(ROOT / "mkdocs.yml")
    readme = _read(ROOT / "README.md")
    deployment = _read(ROOT / "docs" / "deployment.md")
    security = _read(ROOT / "SECURITY.md")

    assert "Paranoid mode: paranoid-mode.md" in nav
    assert "docs/paranoid-mode.md" in readme
    assert "paranoid-mode.md" in deployment
    assert "docs/paranoid-mode.md" in security


def test_paranoid_mode_design_names_strict_local_settings() -> None:
    """The design must specify the settings that the operator switch tightens."""
    text = _collapsed(PARANOID_DOC)

    required_settings = (
        "token required",
        "durable event log required",
        "acl enforcement required",
        "native wss (tls) required",
        "loopback-only by default",
        "metrics token required",
        "metrics query tokens disabled",
        "insecure off-loopback override disabled",
        "a2a bearer auth required",
    )
    for setting in required_settings:
        assert setting in text


def test_paranoid_mode_design_reports_missing_hooks() -> None:
    """The design must identify future hooks without pretending they exist."""
    text = _collapsed(PARANOID_DOC)

    required_hooks = (
        "at-rest encryption",
        "signed events",
        "per-message key rotation",
        "per-agent identity",
        "acl enforcement",
        "private channels",
        "deployment threat model",
    )
    for hook in required_hooks:
        assert hook in text


def test_paranoid_mode_design_keeps_boundary_claims_clear() -> None:
    """The runtime switch must keep unsupported hardening claims clear."""
    text = _collapsed(PARANOID_DOC)

    required_boundaries = (
        "implemented for the hub runtime only",
        "does not encrypt existing databases",
        "does not create cryptographic identity",
        "does not certify exposed deployments",
        "operator checklist",
    )
    for boundary in required_boundaries:
        assert boundary in text
