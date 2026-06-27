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
    assert "synapse demo" in combined
    assert "synapse quickstart-coding" in combined
    assert "synapse git-init --name trial-agent" in combined
    assert "synapse a2a-card --endpoint-url http://127.0.0.1:8877" in combined
    assert "synapse a2a-serve --endpoint-url http://127.0.0.1:8877" in combined


def test_trial_path_docs_keep_a2a_and_real_repo_claims_bounded() -> None:
    combined = _single_spaced(
        "\n".join(
            [
                _read_repo_text("README.md"),
                _read_repo_text("docs/quickstart.md"),
                _read_repo_text("docs/cli.md"),
            ]
        )
    )

    assert "Run this in a disposable or already-versioned repository" in combined
    assert "The A2A bridge step is optional and local-only" in combined
    assert "not an external conformance claim" in combined
    assert "Do not bind it off-loopback without bearer auth" in combined
