# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — exact-seat active-waker lifecycle lock tests

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from synapse_channel.waker_config import waker_config_path
from synapse_channel.waker_lock import WakerLockError, waker_control_lock


def test_lock_is_owner_only_and_refuses_a_concurrent_mutation(tmp_path: Path) -> None:
    identity = "repo/codex-1"
    with waker_control_lock(identity, home=tmp_path):
        path = waker_config_path(identity, home=tmp_path).with_suffix(".lock")
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        with pytest.raises(WakerLockError, match="already changing"):
            with waker_control_lock(identity, home=tmp_path):
                pytest.fail("concurrent lock unexpectedly succeeded")

    with waker_control_lock(identity, home=tmp_path):
        pass


def test_lock_refuses_a_symlink_leaf_without_following_it(tmp_path: Path) -> None:
    identity = "repo/codex-1"
    path = waker_config_path(identity, home=tmp_path).with_suffix(".lock")
    path.parent.mkdir(parents=True, mode=0o700)
    target = tmp_path / "target"
    target.write_text("preserve", encoding="utf-8")
    path.symlink_to(target)

    with pytest.raises(WakerLockError, match="lock is unsafe"):
        with waker_control_lock(identity, home=tmp_path):
            pytest.fail("symlink lock unexpectedly succeeded")
    assert target.read_text(encoding="utf-8") == "preserve"


def test_lock_refuses_a_multiply_linked_leaf(tmp_path: Path) -> None:
    identity = "repo/codex-1"
    path = waker_config_path(identity, home=tmp_path).with_suffix(".lock")
    path.parent.mkdir(parents=True, mode=0o700)
    path.write_text("", encoding="utf-8")
    (tmp_path / "second-link").hardlink_to(path)

    with pytest.raises(WakerLockError, match="lock is unsafe"):
        with waker_control_lock(identity, home=tmp_path):
            pytest.fail("multiply linked lock unexpectedly succeeded")
