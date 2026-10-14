# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Cargo and npm partial comparators through the cross-repo CLI
"""Exercise comparator boundaries from actual Cargo and npm manifest files."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from synapse_channel.cli import main
from synapse_channel.core.version_constraints import CONFLICT, NO_CONFLICT, compare_constraints


@pytest.mark.parametrize("ecosystem", ["rust", "javascript"])
@pytest.mark.parametrize(
    ("constraint", "expected"),
    [
        ("=1", NO_CONFLICT),
        ("=1.2", NO_CONFLICT),
        ("<=1", NO_CONFLICT),
        ("<=1.2", NO_CONFLICT),
        (">1", CONFLICT),
        (">1.2", CONFLICT),
        ("=1.2.0", CONFLICT),
        ("<1.2", CONFLICT),
    ],
)
def test_partial_comparators_in_real_manifests(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    ecosystem: str,
    constraint: str,
    expected: str,
) -> None:
    """Omitted components span a prefix rather than pinning its zero-padded version."""
    for name, requirement in (("alpha", constraint), ("beta", "=1.2.3")):
        directory = tmp_path / name
        directory.mkdir()
        if ecosystem == "rust":
            (directory / "Cargo.toml").write_text(
                f'[package]\nname = "{name}"\nversion = "1.0.0"\n'
                f'[dependencies]\nshared = "{requirement}"\n',
                encoding="utf-8",
            )
        else:
            (directory / "package.json").write_text(
                json.dumps({"name": name, "dependencies": {"shared": requirement}}),
                encoding="utf-8",
            )
    assert main(["cross-repo", str(tmp_path), "--json"]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    report = json.loads(captured.out)
    conflicts = [edge for edge in report["edges"] if edge["kind"] == "version_conflict"]
    assert len(conflicts) == (1 if expected == CONFLICT else 0)
    assert compare_constraints(constraint, "=1.2.3", ecosystem) == expected
