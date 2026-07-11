# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE GITHUB APP — strict bounded JSON tests
"""Pin UTF-8, depth, string-escape, and finite-number behaviour."""

from __future__ import annotations

import json

import pytest

from synapse_github_app.json_boundary import loads_strict_bounded


def test_valid_nested_json_ignores_brackets_and_escapes_inside_strings() -> None:
    raw = b'{"text":"[{}] \\" still text", "items":[{"n":1}]}'
    assert loads_strict_bounded(raw, max_depth=3) == {
        "text": '[{}] " still text',
        "items": [{"n": 1}],
    }


def test_decoder_refuses_depth_nonfinite_utf8_and_bad_limit() -> None:
    with pytest.raises(json.JSONDecodeError, match="exceeds"):
        loads_strict_bounded(b'[[["deep"]]]', max_depth=2)
    with pytest.raises(json.JSONDecodeError, match="non-finite"):
        loads_strict_bounded(b'{"n": NaN}', max_depth=2)
    with pytest.raises(UnicodeDecodeError):
        loads_strict_bounded(b'{"value":"\xff"}', max_depth=2)
    with pytest.raises(ValueError, match="positive"):
        loads_strict_bounded(b"{}", max_depth=0)
