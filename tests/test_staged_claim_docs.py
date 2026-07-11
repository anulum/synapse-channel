# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — public staged claim-check documentation contract

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_public_guides_document_the_shipped_gate_and_identity_boundary() -> None:
    readme = _read("README.md")
    cli = _read("docs/cli.md")
    claims = _read("docs/git-claims.md")
    assert "synapse git-claim-check --staged" in readme
    assert "empty staged index succeeds without a hub" in readme
    assert "explicit `--name`, local `synapse.identity`" in cli
    assert "git diff --cached --name-status" in claims
    assert "A `PROJECT:git` serialization lock cannot satisfy" in claims
    assert "never token content" in claims


def test_git_init_boundary_is_not_overstated() -> None:
    claims = _read("docs/git-claims.md")
    policy = _read("docs/policy-engine.md")
    assert "installs only" in claims
    assert "post-commit" in claims and "post-merge" in claims
    assert "does **not** splice or overwrite a pre-commit hook" in claims
    assert "does not install a universal commit, merge, or push policy gate" in policy
