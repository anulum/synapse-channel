# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — unit tests for the bounded relay-log mirror

from __future__ import annotations

import json
from pathlib import Path

from synapse_channel.core.hub_relay import RelayMirror
from synapse_channel.relay import decode_lite, read_jsonl_since


def _message(payload: str) -> dict[str, object]:
    return {"type": "chat", "sender": "A", "payload": payload}


def test_disabled_mirror_writes_nothing(tmp_path: Path) -> None:
    mirror = RelayMirror(None, max_lines=4)
    assert mirror.log_path is None
    assert mirror.max_lines == 4

    mirror.mirror(_message("x"))

    assert list(tmp_path.iterdir()) == []


def test_mirror_appends_in_compact_lite_form(tmp_path: Path) -> None:
    log = tmp_path / "relay.ndjson"
    mirror = RelayMirror(log, max_lines=8)

    mirror.mirror(_message("hello"))
    mirror.mirror(_message("world"))

    events, offset = read_jsonl_since(log, 0)
    assert offset > 0
    assert all(set(event) <= {"v", "i", "ty", "s", "to", "p", "t", "h", "c"} for event in events)
    decoded = [decode_lite(event) for event in events]
    assert [d["payload"] for d in decoded] == ["hello", "world"]


def test_mirror_trims_back_to_max_lines_once_full(tmp_path: Path) -> None:
    log = tmp_path / "relay.ndjson"
    mirror = RelayMirror(log, max_lines=2)

    # Five appends with max_lines=2 trigger a trim at the second and fourth append,
    # so the file is bounded and never grows without limit.
    for index in range(5):
        mirror.mirror(_message(str(index)))

    lines = log.read_text(encoding="utf-8").splitlines()
    assert 0 < len(lines) <= mirror.max_lines * 2
    decoded = [decode_lite(json.loads(line)) for line in lines]
    # The newest append always survives the trim.
    assert any(event.get("payload") == "4" for event in decoded)


def test_mirror_resets_its_append_counter_after_a_trim(tmp_path: Path) -> None:
    log = tmp_path / "relay.ndjson"
    mirror = RelayMirror(log, max_lines=2)

    # Exactly max_lines appends fire one trim and reset the counter; a third append
    # must not immediately re-trim (the counter restarted from zero).
    mirror.mirror(_message("0"))
    mirror.mirror(_message("1"))  # counter hits max_lines -> trim, reset to 0
    mirror.mirror(_message("2"))  # counter is 1, below max_lines -> no trim

    lines = log.read_text(encoding="utf-8").splitlines()
    decoded = [decode_lite(json.loads(line)) for line in lines]
    payloads = [event.get("payload") for event in decoded]
    assert payloads[-1] == "2"
    assert len(lines) <= mirror.max_lines + 1
