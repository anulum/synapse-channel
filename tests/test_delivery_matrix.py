# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the delivery matrix matches the hub's verb registry
"""Keep docs/delivery-matrix.md in step with the registered request types."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from synapse_channel.core.handlers import VERBS

MATRIX = Path(__file__).resolve().parent.parent / "docs" / "delivery-matrix.md"

pytestmark = pytest.mark.docs_contract


def _rows() -> list[tuple[list[str], str]]:
    """Return each matrix row's request types and delivery class."""
    text = MATRIX.read_text(encoding="utf-8")
    table = text.split("## Matrix", 1)[1].split("\n## ", 1)[0]
    rows = []
    for line in table.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 5 or not cells[0].startswith("`"):
            continue
        rows.append((re.findall(r"`([a-z_]+)`", cells[0]), cells[1]))
    return rows


def test_every_registered_request_type_is_listed_exactly_once() -> None:
    listed = [name for types, _ in _rows() for name in types]
    assert sorted(listed) == sorted(VERBS)
    assert len(listed) == len(set(listed))


def test_each_rows_delivery_class_matches_the_registry() -> None:
    for types, delivery in _rows():
        for name in types:
            spec = VERBS[name]
            if delivery == "read":
                assert not spec.mutates, name
            elif delivery == "apply-once with `idem_key`":
                assert spec.mutates and spec.replay_protected, name
            else:
                assert delivery == "at-least-once", (name, delivery)
                assert spec.mutates and not spec.replay_protected, name
