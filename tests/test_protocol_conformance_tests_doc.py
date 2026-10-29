# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — every test named by the protocol conformance page exists
"""The published protocol conformance page names only tests that exist.

``docs/protocol-conformance-tests.md`` tells readers which tests back each
A2A and MCP row. A renamed or deleted test would leave a row that points at
nothing, so this check parses every ``tests/<file>.py::<name>`` reference and
requires a module-level test function of that name in that file.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "docs" / "protocol-conformance-tests.md"
REFERENCE = re.compile(r"`(tests/[A-Za-z0-9_/]+\.py)::([A-Za-z_][A-Za-z0-9_]*)`")


def page_references(text: str) -> list[tuple[str, str]]:
    """Return every ``(path, test name)`` reference in page order, without repeats."""
    seen: dict[tuple[str, str], None] = {}
    for match in REFERENCE.finditer(text):
        seen.setdefault((match.group(1), match.group(2)), None)
    return list(seen)


def defined_tests(path: Path) -> set[str]:
    """Return the module-level test function names defined in ``path``."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.name.startswith("test_")
    }


def missing_references(root: Path, text: str) -> list[str]:
    """Return every page reference whose file or test function does not exist."""
    missing = []
    for relative, name in page_references(text):
        path = root / relative
        if not path.is_file() or name not in defined_tests(path):
            missing.append(f"{relative}::{name}")
    return missing


def test_every_named_test_exists() -> None:
    """The page cites at least one test per protocol and no missing one."""
    text = PAGE.read_text(encoding="utf-8")
    references = page_references(text)

    assert any(path.startswith("tests/test_a2a") for path, _name in references)
    assert any(path.startswith("tests/test_mcp") for path, _name in references)
    assert missing_references(ROOT, text) == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("`tests/test_gone.py::test_x`", ["tests/test_gone.py::test_x"]),
        ("`tests/test_here.py::test_absent`", ["tests/test_here.py::test_absent"]),
        ("`tests/test_here.py::test_present`", []),
        ("`tests/test_here.py::helper_not_a_test`", ["tests/test_here.py::helper_not_a_test"]),
    ],
)
def test_a_missing_file_or_function_is_reported(
    tmp_path: Path, text: str, expected: list[str]
) -> None:
    """Planted references to an absent file, function or non-test helper are caught."""
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_here.py").write_text(
        "def test_present():\n    pass\n\ndef helper_not_a_test():\n    pass\n",
        encoding="utf-8",
    )

    assert missing_references(tmp_path, text) == expected


def test_references_are_deduplicated_in_page_order() -> None:
    """A test cited in two rows is checked once."""
    text = "`tests/a.py::test_one` `tests/b.py::test_two` `tests/a.py::test_one`"

    assert page_references(text) == [("tests/a.py", "test_one"), ("tests/b.py", "test_two")]
