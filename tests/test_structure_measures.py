# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — what each structure measure counts
"""Pin every construct the structure measures count, on small source texts."""

from __future__ import annotations

from pathlib import Path

import pytest
from tools.structure_measures import MEASURES, measure_roots, measure_source


def measure(text: str, allowed: frozenset[str] = frozenset()) -> dict[str, dict[str, int]]:
    """Measure a module text as ``pkg/m.py`` of package ``pkg``."""
    return measure_source(text, module_key="pkg/m.py", package="pkg", allowed_private=allowed)


def test_every_measure_is_present_even_for_an_empty_module() -> None:
    figures = measure("")
    assert tuple(figures) == MEASURES
    assert figures["module_internal_imports"] == {"pkg/m.py": 0}
    assert figures["private_access"] == {"pkg/m.py": 0}
    assert figures["function_body_lines"] == {}


def test_constructor_parameters_count_every_kind_but_self() -> None:
    text = (
        "class C:\n"
        "    def __init__(self, a, /, b, c=1, *rest, d, e=2, **more):\n"
        "        pass\n"
        "class Bare:\n"
        "    def __init__(self):\n"
        "        pass\n"
        "class NoConstructor:\n"
        "    x = 1\n"
    )
    figures = measure(text)
    assert figures["constructor_parameters"] == {"pkg/m.py::C": 7, "pkg/m.py::Bare": 0}
    assert "pkg/m.py::NoConstructor" not in figures["init_attributes"]


def test_init_attributes_count_distinct_self_targets_of_every_assignment_form() -> None:
    text = (
        "class C:\n"
        "    def __init__(self, other):\n"
        "        self.a = 1\n"
        "        self.a = 2\n"
        "        self.b: int = 3\n"
        "        self.c += 1\n"
        "        self.d, (self.e, other.f) = 1, (2, 3)\n"
        "        local = 4\n"
        "        if other:\n"
        "            self.g = local\n"
    )
    assert measure(text)["init_attributes"] == {"pkg/m.py::C": 6}


def test_init_constructions_count_distinct_capitalised_callees_without_errors() -> None:
    text = (
        "class C:\n"
        "    def __init__(self):\n"
        "        self.a = Registry()\n"
        "        self.b = Registry()\n"
        "        self.c = mod.Gate(Path('x'))\n"
        "        self.d = helper()\n"
        "        raise ValueError(ConfigError('x'), UserWarning('y'), BaseException())\n"
    )
    assert measure(text)["init_constructions"] == {"pkg/m.py::C": 3}


def test_body_lines_exclude_the_docstring_and_count_to_the_last_line() -> None:
    text = (
        "def documented():\n"
        '    """Doc\n'
        "\n"
        "    more.\n"
        '    """\n'
        "    a = 1\n"
        "    return (\n"
        "        a\n"
        "    )\n"
        "def only_doc():\n"
        '    """Doc."""\n'
        "def plain():\n"
        "    return 1\n"
    )
    assert measure(text)["function_body_lines"] == {
        "pkg/m.py::documented": 4,
        "pkg/m.py::only_doc": 0,
        "pkg/m.py::plain": 1,
    }


def test_branches_count_each_branching_construct_and_boolean_operand() -> None:
    text = (
        "async def f(xs, a, b, c):\n"
        "    if a and b or c:\n"
        "        pass\n"
        "    elif a:\n"
        "        pass\n"
        "    for x in xs:\n"
        "        pass\n"
        "    async for x in xs:\n"
        "        pass\n"
        "    while a:\n"
        "        break\n"
        "    try:\n"
        "        pass\n"
        "    except ValueError:\n"
        "        pass\n"
        "    except TypeError:\n"
        "        pass\n"
        "    y = 1 if a else 2\n"
        "    z = [x for x in xs if x for w in x]\n"
        "    match a:\n"
        "        case 1:\n"
        "            pass\n"
        "        case _:\n"
        "            pass\n"
        "    def inner():\n"
        "        if a:\n"
        "            pass\n"
        "    return y, z, inner\n"
    )
    figures = measure(text)["function_branches"]
    assert figures["pkg/m.py::f"] == 15
    assert figures["pkg/m.py::f.<locals>.inner"] == 1


def test_qualified_names_cover_nesting_methods_and_redefinitions() -> None:
    text = (
        "class Outer:\n"
        "    class Inner:\n"
        "        def method(self):\n"
        "            pass\n"
        "    def method(self):\n"
        "        def local():\n"
        "            pass\n"
        "        return local\n"
        "if True:\n"
        "    def guarded():\n"
        "        pass\n"
        "else:\n"
        "    def guarded():\n"
        "        pass\n"
        "try:\n"
        "    pass\n"
        "except ValueError:\n"
        "    def recovered():\n"
        "        pass\n"
    )
    assert set(measure(text)["function_body_lines"]) == {
        "pkg/m.py::Outer.Inner.method",
        "pkg/m.py::Outer.method",
        "pkg/m.py::Outer.method.<locals>.local",
        "pkg/m.py::guarded",
        "pkg/m.py::guarded#2",
        "pkg/m.py::recovered",
    }


def test_internal_imports_count_distinct_modules_of_the_package_only() -> None:
    text = (
        "import os\n"
        "import pkg.a\n"
        "import pkg.a\n"
        "import pkgother.x\n"
        "from pkg.b import one, two\n"
        "from pkg.b import three\n"
        "from . import sibling\n"
        "from ..up import thing\n"
        "from json import loads\n"
        "def late():\n"
        "    from pkg.c import lazy\n"
        "    return lazy\n"
    )
    assert measure(text)["module_internal_imports"] == {"pkg/m.py": 5}


def test_private_access_counts_other_objects_only_and_honours_the_allowed_names() -> None:
    text = (
        "import argparse\n"
        "class C:\n"
        "    def f(self, hub, other):\n"
        "        self._mine = 1\n"
        "        cls._also_mine = 2\n"
        "        hub._system()\n"
        "        hub._system()\n"
        "        other.__dict__\n"
        "        other.__mangled\n"
        "        other.public\n"
        "        hub.nested._deep\n"
        "        return argparse._SubParsersAction\n"
    )
    assert measure(text)["private_access"] == {"pkg/m.py": 4}
    allowed = frozenset({"argparse._SubParsersAction"})
    assert measure(text, allowed)["private_access"] == {"pkg/m.py": 3}


def test_roots_are_merged_with_repository_relative_keys(tmp_path: Path) -> None:
    (tmp_path / "src" / "pkg" / "sub").mkdir(parents=True)
    (tmp_path / "tools").mkdir()
    (tmp_path / "src" / "pkg" / "a.py").write_text("from pkg.sub import b\n", encoding="utf-8")
    (tmp_path / "src" / "pkg" / "sub" / "b.py").write_text("def f():\n    return 1\n")
    (tmp_path / "tools" / "t.py").write_text("import tools.other\nimport pkg.a\n")
    figures = measure_roots(tmp_path, ["src/pkg", "tools"])
    assert figures["module_internal_imports"] == {
        "src/pkg/a.py": 1,
        "src/pkg/sub/b.py": 0,
        "tools/t.py": 1,
    }
    assert figures["function_body_lines"] == {"src/pkg/sub/b.py::f": 1}


def test_unreadable_sources_and_missing_roots_raise_with_the_path(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    with pytest.raises(FileNotFoundError, match="measured root is not a directory: absent"):
        measure_roots(tmp_path, ["absent"])
    (tmp_path / "pkg" / "bad.py").write_text("def broken(:\n", encoding="utf-8")
    with pytest.raises(SyntaxError, match=r"pkg/bad\.py: .* \(line 1\)"):
        measure_roots(tmp_path, ["pkg"])
    (tmp_path / "pkg" / "bad.py").write_bytes(b"\xff\xfe\x00")
    with pytest.raises(UnicodeDecodeError):
        measure_roots(tmp_path, ["pkg"])
