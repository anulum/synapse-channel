# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — durable per-identity mailbox cursor persistence

from __future__ import annotations

from pathlib import Path

import pytest

from synapse_channel.mailbox_cursor import cursor_path, load_cursor, save_cursor


class TestCursorPath:
    def test_flattens_a_slashed_identity_into_one_filename(self, tmp_path: Path) -> None:
        path = cursor_path("SYNAPSE-CHANNEL/claude-2759", base=tmp_path)
        assert path.parent == tmp_path
        assert "/" not in path.name
        assert path.name == "SYNAPSE-CHANNEL%2Fclaude-2759"

    def test_defaults_under_the_synapse_home(self) -> None:
        path = cursor_path("BOB")
        assert path.parent == Path.home() / "synapse" / "mailbox-cursor"
        assert path.name == "BOB"


class TestLoad:
    def test_missing_file_reads_as_zero(self, tmp_path: Path) -> None:
        assert load_cursor(tmp_path / "absent") == 0

    def test_a_stored_value_round_trips(self, tmp_path: Path) -> None:
        path = tmp_path / "cur"
        save_cursor(path, 42)
        assert load_cursor(path) == 42

    def test_corrupt_contents_read_as_zero(self, tmp_path: Path) -> None:
        path = tmp_path / "cur"
        path.write_text("not-a-number", encoding="utf-8")
        assert load_cursor(path) == 0

    def test_a_negative_stored_value_is_clamped_on_read(self, tmp_path: Path) -> None:
        path = tmp_path / "cur"
        path.write_text("-5", encoding="utf-8")
        assert load_cursor(path) == 0


class TestSave:
    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        path = tmp_path / "nested" / "deeper" / "cur"
        save_cursor(path, 7)
        assert load_cursor(path) == 7

    def test_clamps_a_negative_value_to_zero(self, tmp_path: Path) -> None:
        path = tmp_path / "cur"
        save_cursor(path, -9)
        assert path.read_text(encoding="utf-8") == "0"

    def test_overwrites_a_previous_value(self, tmp_path: Path) -> None:
        path = tmp_path / "cur"
        save_cursor(path, 3)
        save_cursor(path, 8)
        assert load_cursor(path) == 8

    def test_round_trips_through_cursor_path(self, tmp_path: Path) -> None:
        path = cursor_path("proj/agent-1", base=tmp_path)
        save_cursor(path, 15)
        assert load_cursor(cursor_path("proj/agent-1", base=tmp_path)) == 15


class TestSaveDurability:
    def test_writes_an_owner_only_file(self, tmp_path: Path) -> None:
        from synapse_channel.core.secure_path import assert_owner_only_file_path

        path = tmp_path / "cur"
        save_cursor(path, 5)
        assert_owner_only_file_path(path, purpose="mailbox cursor")

    def test_leaves_no_temporary_file_behind(self, tmp_path: Path) -> None:
        path = tmp_path / "cur"
        save_cursor(path, 5)
        # The atomic write renames a temp into place; nothing but the cursor remains.
        assert [entry.name for entry in tmp_path.iterdir()] == ["cur"]

    def test_a_failed_replace_removes_the_temp_and_reraises(self, tmp_path: Path) -> None:
        # A real failed replace: the destination is a non-empty directory, so the
        # temp is written but the final os.replace raises. The half-written temp
        # must be removed and the error must propagate rather than silently losing
        # the cursor.
        path = tmp_path / "cur"
        path.mkdir()
        (path / "occupant").write_text("x", encoding="utf-8")
        with pytest.raises(OSError):
            save_cursor(path, 5)
        assert (path / "occupant").read_text(encoding="utf-8") == "x"
        assert [entry.name for entry in tmp_path.iterdir()] == ["cur"]
