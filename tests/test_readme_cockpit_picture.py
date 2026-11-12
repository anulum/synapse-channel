# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — the README cockpit picture is bound to its generator
"""Guard the cockpit picture shown in the README.

The picture is taken by a browser journey from the built cockpit and a
demonstration fleet. The journey writes a small record beside the picture. These
tests bind the committed picture to that record and the record to the
demonstration fleet, so a picture replaced by hand, or a fleet changed without
a new picture, is refused. They do not compare pixels: whether the picture
still matches the running cockpit is decided by taking it again.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import struct
import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.docs_contract

ROOT = Path(__file__).resolve().parents[1]
PICTURE = ROOT / "docs" / "assets" / "feature-cockpit.png"
RECORD = ROOT / "docs" / "assets" / "feature-cockpit.json"
JOURNEY = "clients/cockpit/e2e/readme-picture.spec.ts"
FLEET = "clients/cockpit/e2e/demo_fleet.py"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_PICTURE_BYTES = 500_000


def _record() -> dict[str, Any]:
    """Return the generation record written beside the picture."""
    loaded = json.loads(RECORD.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _png_size(data: bytes) -> tuple[int, int]:
    """Return the width and height stated in a PNG header."""
    assert data[:8] == PNG_SIGNATURE, "the picture is not a PNG file"
    assert data[12:16] == b"IHDR", "the picture has no leading IHDR chunk"
    width, height = struct.unpack(">II", data[16:24])
    return int(width), int(height)


def _demo_fleet() -> Any:
    """Load the demonstration fleet script as a module."""
    spec = importlib.util.spec_from_file_location("cockpit_demo_fleet", ROOT / FLEET)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


def test_picture_is_a_png_of_the_recorded_size_within_the_file_budget() -> None:
    """The committed picture has the recorded dimensions and stays small."""
    data = PICTURE.read_bytes()
    record = _record()

    assert _png_size(data) == (record["width"], record["height"]) == (1280, 900)
    assert len(data) <= MAX_PICTURE_BYTES


def test_picture_is_the_one_its_generation_record_describes() -> None:
    """A picture replaced without the journey no longer matches its record."""
    record = _record()

    assert record["picture"] == PICTURE.name
    assert record["sha256"] == hashlib.sha256(PICTURE.read_bytes()).hexdigest()
    assert record["journey"] == JOURNEY
    assert record["fleet"] == FLEET
    assert (ROOT / JOURNEY).is_file()
    assert record["colour_scheme"] == "dark"
    assert record["language"] == "EN"


def test_record_names_the_demonstration_fleet_as_it_is_written() -> None:
    """A changed demonstration fleet needs a new picture."""
    record = _record()
    fleet = _demo_fleet()

    assert record["participants"] == list(fleet.PARTICIPANTS)
    assert record["tasks"] == [task_id for task_id, _title, _needs in fleet.TASKS]
    assert record["claims"] == [task_id for _owner, task_id, _paths in fleet.CLAIMS]


def test_readme_shows_the_picture_at_its_real_width() -> None:
    """The README names the picture once, at the width the file really has."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    shown = re.findall(
        r'<img src="docs/assets/feature-cockpit\.png" width="(\d+)" alt="([^"]+)">', readme
    )

    assert len(shown) == 1
    width, alternative = shown[0]
    assert int(width) == _png_size(PICTURE.read_bytes())[0]
    for named in ("roster", "claims", "task board", "risk rail", "findings", "signal log"):
        assert named in alternative


def test_client_readme_states_how_the_picture_is_taken_again() -> None:
    """The regeneration command is written where the journey lives."""
    text = (ROOT / "clients" / "cockpit" / "README.md").read_text(encoding="utf-8")

    assert "SYNAPSE_COCKPIT_README_PICTURE" in text
    assert "e2e/readme-picture.spec.ts" in text
    assert "docs/assets/feature-cockpit.png" in text
