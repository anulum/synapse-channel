# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — bounded streaming documentation tests
"""Guard the streaming transport choice and retention boundary in the docs."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "streaming.md"


def _read(path: Path) -> str:
    """Read a UTF-8 documentation file."""
    return path.read_text(encoding="utf-8")


def _collapsed(path: Path) -> str:
    """Return lowercase documentation text with normalized whitespace."""
    return " ".join(_read(path).lower().split())


def test_streaming_doc_is_in_the_nav() -> None:
    assert "Bounded streaming responses: streaming.md" in _read(ROOT / "mkdocs.yml")


def test_streaming_doc_pins_the_transport_choice() -> None:
    text = _collapsed(DOC)
    assert "websocket frames, not sse" in text
    assert "chat path" in text
    # the frame fields are documented so a consumer can parse them
    for field in ("stream_id", "seq", "frame_type"):
        assert field in text


def test_streaming_doc_makes_bounds_and_retention_explicit() -> None:
    text = _collapsed(DOC)
    for bound in ("max_chunks", "max_chunk_bytes", "max_total_bytes", "ttl_seconds"):
        assert bound in text
    assert "transient" in text
    assert "not durable task state" in text
    assert "release receipt" in text
    # the future non-journalled type is called out so the boundary is explicit
    assert "non-journalled stream message type" in text
