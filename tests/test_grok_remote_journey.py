# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — remote Grok connector journey tests

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Callable
from pathlib import Path

_TOOLS = Path(__file__).resolve().parents[1] / "tools"
_SPEC = importlib.util.spec_from_file_location(
    "grok_remote_journey", _TOOLS / "grok_remote_journey.py"
)
assert _SPEC is not None and _SPEC.loader is not None
_JOURNEY = importlib.util.module_from_spec(_SPEC)
sys.modules["grok_remote_journey"] = _JOURNEY
_SPEC.loader.exec_module(_JOURNEY)


def _which(found: set[str]) -> Callable[[str], str | None]:
    def which(name: str) -> str | None:
        if name in found:
            return f"/nonexistent/{name}"
        return None

    return which


def test_missing_tools_are_not_installed() -> None:
    """A host without either executable reports not installed."""
    report = _JOURNEY.probe(which=_which(set()))
    assert report == {
        "grok": "not installed",
        "http_mcp": "not installed",
        "synapse": "not installed",
        "verdict": "not installed",
    }


def test_both_tools_with_http_profile_are_present() -> None:
    """Both executables and the HTTP transport marker are a present connector."""
    report = _JOURNEY.probe(
        which=_which({"grok", "synapse"}),
        http_ready=lambda _path: True,
    )
    assert report["verdict"] == "present"
    assert report["http_mcp"] == "present"


def test_synapse_without_http_profile_is_not_installed() -> None:
    """A synapse binary that lacks the HTTP transport is not this connector."""
    report = _JOURNEY.probe(
        which=_which({"grok", "synapse"}),
        http_ready=lambda _path: False,
    )
    assert report["verdict"] == "not installed"
    assert report["grok"] == "present"
    assert report["http_mcp"] == "not installed"


def test_shell_wrapper_names_its_python(tmp_path: Path) -> None:
    """A setuptools shell wrapper yields the quoted interpreter."""
    script = tmp_path / "synapse"
    script.write_text(
        "#!/bin/sh\n'''exec' '/opt/venv/bin/python3' \"$0\" \"$@\"\n",
        encoding="utf-8",
    )
    assert _JOURNEY._interpreter(str(script)) == "/opt/venv/bin/python3"


def test_remote_guide_keeps_credentials_off_the_command_line() -> None:
    """The published guide refuses raw tokens and names the loopback profile."""
    guide = Path("docs/grok-remote.md").read_text(encoding="utf-8")
    assert "loopback" in guide
    assert "--token-file" in guide
    assert "never with `--token`" in guide
    assert "not installed" in guide
    assert "webhook URL" in guide
    assert "Bearer " not in guide
