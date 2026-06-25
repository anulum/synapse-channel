# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the NDJSON relay log and compact wire format

from __future__ import annotations

from pathlib import Path

from synapse_channel.relay import (
    load_offset,
    save_offset,
)


def test_offset_persistence_roundtrip(tmp_path: Path) -> None:
    marker = tmp_path / "cur" / "offset"
    assert load_offset(marker) == 0
    save_offset(marker, 123)
    assert load_offset(marker) == 123


def test_save_offset_clamps_negative(tmp_path: Path) -> None:
    marker = tmp_path / "offset"
    save_offset(marker, -5)
    assert load_offset(marker) == 0


def test_load_offset_corrupt_returns_zero(tmp_path: Path) -> None:
    marker = tmp_path / "offset"
    marker.write_text("garbage", encoding="utf-8")
    assert load_offset(marker) == 0
