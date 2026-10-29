# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — tests for the trusted-core dependency direction check
"""The trusted core may depend on nothing but itself and the package version."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest
from tools.check_core_dependency_direction import (
    Violation,
    core_violations,
    main,
    module_violations,
)

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "check_core_dependency_direction.py"
PAGE = ROOT / "docs" / "invariant-conformance.md"
MODULE_PATH = re.compile(r"`(src/synapse_channel/[A-Za-z0-9_/]+\.py)`")


def _tree(tmp_path: Path, files: dict[str, str]) -> Path:
    """Write a minimal repository with the given core modules."""
    core = tmp_path / "src" / "synapse_channel" / "core"
    core.mkdir(parents=True)
    (tmp_path / "src" / "synapse_channel" / "__init__.py").write_text("", encoding="utf-8")
    (core / "__init__.py").write_text("", encoding="utf-8")
    for name, text in files.items():
        path = core / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return tmp_path


def test_the_shipped_core_depends_only_on_itself() -> None:
    """The real repository conforms; this test keeps it that way in every CI run."""
    assert core_violations(ROOT) == []


def test_allowed_core_and_version_imports_pass(tmp_path: Path) -> None:
    root = _tree(
        tmp_path,
        {
            "allowed.py": (
                "import json\n"
                "import synapse_channel.core.state\n"
                "from synapse_channel.core import persistence\n"
                "from synapse_channel import __version__\n"
                "from . import sibling\n"
                "from importlib import import_module\n"
                "import_module('synapse_channel.core.state')\n"
                "import_module(name_chosen_at_run_time)\n"
                "import_module(package='synapse_channel')\n"
            ),
            "handlers/inner.py": "from .. import state\nfrom ..state import Claim\n",
        },
    )

    assert core_violations(root) == []


@pytest.mark.parametrize(
    ("source", "target"),
    [
        ("import synapse_channel.a2a_server\n", "synapse_channel.a2a_server"),
        ("from synapse_channel.integrations import slack\n", "synapse_channel.integrations"),
        ("from synapse_channel import cli\n", "synapse_channel.cli"),
        ("from .. import dashboard\n", "synapse_channel.dashboard"),
        ("from ..mcp import outcome\n", "synapse_channel.mcp"),
        (
            "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n"
            "    from synapse_channel.client.agent import SynapseAgent\n",
            "synapse_channel.client.agent",
        ),
        (
            "import importlib\nimportlib.import_module('synapse_channel.wake_relay')\n",
            "synapse_channel.wake_relay",
        ),
        ("__import__('synapse_channel.participants')\n", "synapse_channel.participants"),
        (
            "import importlib\nimportlib.import_module(name='synapse_channel.cli')\n",
            "synapse_channel.cli",
        ),
        ("__import__(name='synapse_channel.dashboard')\n", "synapse_channel.dashboard"),
        ("import synapse_channel\n", "synapse_channel"),
    ],
)
def test_every_import_form_towards_an_edge_is_refused(
    tmp_path: Path, source: str, target: str
) -> None:
    root = _tree(tmp_path, {"leaky.py": "# a core module\n" + source})

    violations = core_violations(root)

    assert [item.target for item in violations] == [target]
    assert violations[0].path == Path("src/synapse_channel/core/leaky.py")
    assert violations[0].line >= 2
    assert violations[0].render().startswith("src/synapse_channel/core/leaky.py:")


def test_violations_are_reported_in_line_order(tmp_path: Path) -> None:
    root = _tree(
        tmp_path,
        {"two.py": "from synapse_channel import cli\nimport synapse_channel.a2a\n"},
    )
    source = root / "src" / "synapse_channel" / "core" / "two.py"

    assert module_violations(source, root) == [
        Violation(Path("src/synapse_channel/core/two.py"), 1, "synapse_channel.cli"),
        Violation(Path("src/synapse_channel/core/two.py"), 2, "synapse_channel.a2a"),
    ]


def test_relative_import_above_the_top_package_is_refused(tmp_path: Path) -> None:
    root = _tree(tmp_path, {"climb.py": "from .... import anything\n"})

    with pytest.raises(ValueError, match="beyond the top-level package"):
        core_violations(root)


def test_missing_core_package_is_reported(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="trusted core package not found"):
        core_violations(tmp_path)


def test_main_reports_pass_fail_and_unreadable_core(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clean = _tree(tmp_path / "clean", {"ok.py": "import synapse_channel.core.state\n"})
    leaky = _tree(tmp_path / "leaky", {"bad.py": "from synapse_channel import cli\n"})
    broken = _tree(tmp_path / "broken", {"bad.py": "def (:\n"})

    assert main(["--root", str(clean)]) == 0
    assert "core dependency direction: PASS" in capsys.readouterr().out
    assert main(["--root", str(leaky)]) == 1
    error = capsys.readouterr().err
    assert "src/synapse_channel/core/bad.py:1: core imports synapse_channel.cli" in error
    assert "FAIL (1 edge import(s))" in error
    assert main(["--root", str(broken)]) == 1
    assert "cannot check" in capsys.readouterr().err
    assert main(["--root", str(tmp_path / "absent")]) == 1
    assert "trusted core package not found" in capsys.readouterr().err


def test_the_script_runs_as_the_pre_commit_hook_does() -> None:
    """The hook entry ``python tools/check_core_dependency_direction.py`` passes on this tree."""
    result = subprocess.run(
        [sys.executable, str(TOOL)], cwd=ROOT, capture_output=True, text=True, check=False
    )

    assert result.returncode == 0, result.stderr
    assert "PASS" in result.stdout


def listed_module_paths(text: str) -> list[str]:
    """Return the module paths named in the guarantee-dependency section."""
    start = text.index("### Modules the authority and truth guarantees depend on")
    end = text.index("### Trust model per profile", start)
    return MODULE_PATH.findall(text[start:end])


def test_every_module_named_as_a_guarantee_dependency_exists() -> None:
    """The written dependency list names only modules present in the tree."""
    paths = listed_module_paths(PAGE.read_text(encoding="utf-8"))

    assert len(paths) >= 40
    assert [path for path in paths if not (ROOT / path).is_file()] == []
    assert any(path.startswith("src/synapse_channel/core/") for path in paths)


def test_a_renamed_dependency_module_would_be_reported() -> None:
    """A planted missing path is found by the same reader."""
    text = (
        "### Modules the authority and truth guarantees depend on\n"
        "| x | `src/synapse_channel/core/state.py`, `src/synapse_channel/core/gone.py` |\n"
        "### Trust model per profile\n"
        "`src/synapse_channel/core/outside_section.py`\n"
    )

    paths = listed_module_paths(text)

    assert paths == ["src/synapse_channel/core/state.py", "src/synapse_channel/core/gone.py"]
    assert [path for path in paths if not (ROOT / path).is_file()] == [
        "src/synapse_channel/core/gone.py"
    ]
