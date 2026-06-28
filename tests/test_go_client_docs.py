# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — docs tests for the official Go client

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    """Read a repository text file for documentation assertions."""
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def test_go_client_is_documented_and_navigable() -> None:
    """Public docs should expose the Go client and its read-only boundary."""
    readme = _read("README.md")
    mkdocs = _read("mkdocs.yml")
    docs = _read("docs/go-client.md")
    client_readme = _read("clients/go/synapse/README.md")
    combined = "\n".join([readme, docs, client_readme])

    assert "Official Go client" in combined
    assert "clients/go/synapse" in combined
    assert "DashboardSnapshot" in combined
    assert "read-only" in combined
    assert "does not implement the WebSocket mutation protocol" in combined
    assert "Go client: go-client.md" in mkdocs
