# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — native Windows OpenCode refusals
"""The non-POSIX editor floor refuses before a draft or an unsafe adapter file."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from synapse_channel.opencode_adapter_files import (
    OpenCodeAdapterFileError,
    read_text_snapshot,
    write_text_snapshot,
)
from synapse_channel.opencode_composer import EDITOR_ABORT, main
from synapse_channel.opencode_composer_files import ComposerError, probe_lock

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="these refusals are the non-POSIX OpenCode floor",
)


def test_windows_editor_callback_aborts_before_reading_the_draft(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-POSIX host rejects the editor transaction and leaves the draft unread."""
    draft = tmp_path / "draft.txt"
    draft.write_bytes(b"secret draft\n")
    marker = tmp_path / "draft-was-read"

    def refused(*_args: object, **_kwargs: object) -> None:
        marker.write_text("read", encoding="utf-8")
        raise AssertionError("the draft was read")

    monkeypatch.setattr("synapse_channel.opencode_composer.observe_native_draft", refused)
    code = main(["--profile", str(tmp_path / "profile"), str(draft)])
    assert code == EDITOR_ABORT
    assert draft.read_bytes() == b"secret draft\n"
    assert not marker.exists()


def test_windows_probe_lock_refuses_before_creating_one(tmp_path: Path) -> None:
    """The lock guard returns before a private directory or a lock file exists."""
    with pytest.raises(ComposerError, match="^native editor readback requires POSIX$"):
        with probe_lock(tmp_path):
            pass
    assert list(tmp_path.iterdir()) == []


def test_windows_adapter_file_stays_owner_only_until_everyone_can_read(tmp_path: Path) -> None:
    """A real write keeps the NT DACL owner-only; Everyone read makes the next read refuse."""
    path = tmp_path / "nested" / "config.json"
    missing = read_text_snapshot(path)
    write_text_snapshot(path, "{}\n", missing)
    captured = read_text_snapshot(path)
    assert captured.text == "{}\n"
    granted = subprocess.run(
        ["icacls", str(path), "/grant", "*S-1-1-0:(R)"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert granted.returncode == 0, granted.stderr
    with pytest.raises(OpenCodeAdapterFileError, match="not owner-only"):
        read_text_snapshot(path)
    assert path.read_bytes() == b"{}\n"
