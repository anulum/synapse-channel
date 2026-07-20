"""Guard the paranoid-mode design and its public security boundaries."""

from __future__ import annotations

import re
import shlex
from pathlib import Path

from synapse_channel import cli
from synapse_channel.core.paranoid import apply_paranoid_hub_profile

ROOT = Path(__file__).resolve().parents[1]
PARANOID_DOC = ROOT / "docs" / "paranoid-mode.md"
QUICKSTART_DOC = ROOT / "docs" / "quickstart.md"
CLI_DOC = ROOT / "docs" / "cli.md"


def _read(path: Path) -> str:
    """Read a UTF-8 documentation file."""
    return path.read_text(encoding="utf-8")


def _collapsed(path: Path) -> str:
    """Return lowercase documentation text with normalized whitespace."""
    return " ".join(_read(path).lower().split())


def _documented_paranoid_args(path: Path) -> list[str]:
    """Extract the public paranoid command as production CLI arguments."""
    match = re.search(
        r"```bash\n(?P<command>synapse hub --paranoid .*?)\n```",
        _read(path),
        flags=re.DOTALL,
    )
    if match is None:
        raise AssertionError(f"missing paranoid command in {path}")
    command = match.group("command").replace("\\\n", " ")
    return shlex.split(command)[1:]


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


def test_paranoid_mode_design_reports_uncomposed_controls() -> None:
    """The profile must distinguish separate opt-ins from genuinely absent hooks."""
    text = _collapsed(PARANOID_DOC)

    required_controls = (
        "controls not composed by this profile",
        "ships separately",
        "`--team-secure` requires",
        "at-rest encryption",
        "signed events",
        "per-message key rotation",
        "per-agent identity",
        "acl enforcement",
        "private channels",
        "deployment threat model",
    )
    for control in required_controls:
        assert control in text


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


def test_public_paranoid_commands_satisfy_the_runtime_policy() -> None:
    """Copy-paste paranoid examples must include every production requirement."""
    for path in (PARANOID_DOC, QUICKSTART_DOC, CLI_DOC):
        args = cli.build_parser().parse_args(_documented_paranoid_args(path))

        assert args.token_file == "~/.config/synapse/token"
        assert args.message_auth_key_file == "~/.config/synapse/message-auth.keys"

        # Secret files are resolved after parsing and before policy application in
        # the production startup path. Model that boundary without reading secrets.
        args.token = "resolved-token"
        args.message_auth_key = ["main:resolved-secret:project/agent"]
        report = apply_paranoid_hub_profile(args)

        assert report is not None
        assert "native WSS (TLS) required" in report.enforced
