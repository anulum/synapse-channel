# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL - documentation contract for the safe PyPI trial path

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TRANSLATED_READMES = tuple(sorted((REPO_ROOT / "docs" / "readme").glob("README.*.md")))
GOLDEN_PATH_BLOCK = """python -m pip install synapse-channel
synapse doctor
synapse demo --output ./synapse-golden-demo"""


def _read_repo_text(relative_path: str) -> str:
    """Read a repository text file for safe-trial documentation checks."""
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def _single_spaced(text: str) -> str:
    """Normalize documentation whitespace for exact phrase checks."""
    return " ".join(text.split())


def test_public_docs_foreground_fastest_safe_trial_path() -> None:
    combined = _single_spaced(
        "\n".join(
            [
                _read_repo_text("README.md"),
                _read_repo_text("docs/quickstart.md"),
                _read_repo_text("docs/installation.md"),
                _read_repo_text("docs/cli.md"),
            ]
        )
    )

    assert "Fastest safe trial path" in combined
    assert "python -m pip install synapse-channel" in combined
    assert "synapse doctor" in combined
    assert "synapse demo --output ./synapse-golden-demo" in combined
    assert "synapse git-init --name trial-agent" in combined
    assert "Optional A2A interoperability is a follow-on" in combined


def test_trial_path_docs_keep_first_value_self_contained() -> None:
    combined = _single_spaced(
        "\n".join(
            [
                _read_repo_text("README.md"),
                _read_repo_text("docs/quickstart.md"),
                _read_repo_text("docs/cli.md"),
            ]
        )
    )

    assert "starts and stops its own local hub" in combined
    assert "uses a disposable committed Git repository" in combined
    assert "denies a mutation before handoff" in combined
    assert "writes an observed verification receipt" in combined
    assert "needs no persistent hub, provider CLI, Git hook, MCP host, or A2A bridge" in combined


def test_translated_readmes_share_the_same_executable_trial_block() -> None:
    for path in TRANSLATED_READMES:
        text = path.read_text(encoding="utf-8")
        assert GOLDEN_PATH_BLOCK in text, f"{path} retains a divergent trial sequence"
        assert "synapse demo\nsynapse quickstart-coding\nsynapse git-init" not in text
