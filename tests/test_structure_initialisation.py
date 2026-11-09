# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — source-local initialisation measurement regression cases
"""Exercise constructor/helper and uncertainty handling through source measurement."""

from __future__ import annotations

import pytest
from tools.structure_measures import measure_source


def inspect(source: str) -> tuple[dict[str, int], dict[str, tuple[str, ...]]]:
    """Inspect fixture source through the same public entry point as the gate."""
    unresolved: dict[str, tuple[str, ...]] = {}
    figures = measure_source(
        source, module_key="pkg/source.py", package="pkg", unresolved=unresolved
    )
    return figures["initialisation_attributes"], unresolved


def test_constructor_cannot_hide_seventy_five_attributes_in_helpers() -> None:
    """The union survives transitive helpers, repeated calls, recursion and augmented writes."""
    source = (
        "class Hub:\n"
        " def __init__(self):\n"
        "  self.a = self.b = self.c = 0\n"
        "  self.receive()\n"
        "  self.receive()\n"
        " def receive(this):\n"
        + "".join(f"  this.value{i}: int = {i}\n" for i in range(75))
        + "  this.nested()\n"
        " def nested(self):\n"
        "  self.a += 1\n"
        "  self.receive()\n"
        " def unused(self):\n"
        "  self.not_initialised = 0\n"
    )
    figures = measure_source(source, module_key="pkg/source.py", package="pkg")
    assert figures["init_attributes"] == {"pkg/source.py::Hub": 3}
    assert figures["initialisation_attributes"] == {"pkg/source.py::Hub": 78}
    assert inspect(source)[1] == {}


def test_assignment_forms_and_literal_setters_keep_one_union() -> None:
    """Destructuring, starred targets and both literal setters retain their real names."""
    source = (
        "class C:\n"
        " def __init__(self):\n"
        "  self.a, (self.b, *self.c) = 1, (2, 3)\n"
        "  other.d = 4\n"
        "  other[0] = 5\n"
        "  setattr(self, 'd', 4)\n"
        "  object.__setattr__(self, 'e', 5)\n"
        "  setattr(other, 'not_mine', 0)\n"
        "  setattr(self)\n"
        "  not_setattr(other, self.a)\n"
    )
    assert inspect(source) == ({"pkg/source.py::C": 5}, {})


def test_conditional_async_and_class_receivers_are_followed() -> None:
    """Conditional method definitions are visible without entering nested classes."""
    source = (
        "class C:\n"
        " def __init__(self):\n"
        "  self.helper()\n"
        "  cls.class_helper()\n"
        " if available:\n"
        "  async def helper(self):\n"
        "   self.a = 1\n"
        " else:\n"
        "  def helper(self):\n"
        "   self.b = 2\n"
        " try:\n"
        "  pass\n"
        " except Exception:\n"
        "  def class_helper(cls):\n"
        "   cls.c = 3\n"
        " class Nested:\n"
        "  def helper(self):\n"
        "   self.other = 1\n"
    )
    assert inspect(source) == ({"pkg/source.py::C": 3}, {})


def test_dynamic_inherited_and_external_calls_stay_explicitly_unresolved() -> None:
    """An unknown effect is visible even when no literal attribute can be counted."""
    source = (
        "class C(Base):\n"
        " def __init__(self):\n"
        "  self.inherited()\n"
        "  getattr(self, name)()\n"
        "  setattr(self, name, 1)\n"
        "  install(target=self)\n"
        "  object.__setattr__(self, name, 1)\n"
    )
    figures, unresolved = inspect(source)
    assert figures == {"pkg/source.py::C": 0}
    effects = unresolved["pkg/source.py::C"]
    assert len(effects) == 5
    assert any("unresolved method inherited" in e for e in effects)
    assert any("getattr receives self" in e for e in effects)
    assert sum("dynamic attribute name" in e for e in effects) == 2
    assert any("install receives self" in e for e in effects)


@pytest.mark.parametrize(
    "source",
    ["class C:\n def __init__():\n  pass\n", "class C:\n pass\n"],
)
def test_source_without_an_instance_receiver_has_no_known_assignments(source: str) -> None:
    """Malformed runtime signatures remain parseable source without fabricated attributes."""
    figures, unresolved = inspect(source)
    assert not unresolved
    assert all(value == 0 for value in figures.values())
