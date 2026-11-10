# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the byte limit of the mutation-posture repository file read
"""Pin the inclusive byte limit of the repository file read behind mutation posture.

The open-time checks refuse a file that is already over the limit or was swapped;
these tests cover the read loop itself: a file that grows after those checks is
refused by the loop's own byte count, and a file of exactly the limit is read whole.
"""

from __future__ import annotations

import stat
import subprocess
import sys
from pathlib import Path

from synapse_channel.mutation_governance import _READ_LIMIT, _read_repository_text

_READ_GROWTH_PROGRAM = r"""
import os
import sys
from pathlib import Path

from synapse_channel.mutation_governance import _read_repository_text

config, limit_text = sys.argv[1:3]
limit = int(limit_text)
grown = False

def grow_at_the_first_read(frame, event, arg):
    global grown
    if grown or event != "c_call" or arg is not os.read:
        return
    if frame.f_code.co_name != "_read_repository_text":
        return
    grown = True
    extra = (limit + 1) - Path(config).stat().st_size
    descriptor = os.open(config, os.O_WRONLY | os.O_APPEND)
    try:
        os.write(descriptor, b"x" * extra)
    finally:
        os.close(descriptor)

sys.setprofile(grow_at_the_first_read)
try:
    _read_repository_text(Path(config))
except ValueError as exc:
    sys.setprofile(None)
    sys.stdout.write(str(exc))
    raise SystemExit(0 if grown else "the file never grew")
sys.setprofile(None)
raise SystemExit("the read succeeded")
"""


def test_a_file_that_grows_during_the_read_is_refused_by_the_read_loop(tmp_path: Path) -> None:
    """Growth after the open-time checks is caught by the read loop's own byte limit."""
    config = tmp_path / ".pre-commit-config.yaml"
    config.write_text("repos: []\n", encoding="utf-8")
    before = config.stat()

    result = subprocess.run(
        [sys.executable, "-c", _READ_GROWTH_PROGRAM, str(config), str(_READ_LIMIT)],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == f"repository inspection path exceeds {_READ_LIMIT} bytes: {config}"
    after = config.stat()
    assert (after.st_dev, after.st_ino) == (before.st_dev, before.st_ino)
    assert after.st_size == _READ_LIMIT + 1


def test_a_file_of_exactly_the_limit_is_read_whole(tmp_path: Path) -> None:
    """The byte limit is inclusive: a file of exactly the limit is read, not refused."""
    config = tmp_path / ".pre-commit-config.yaml"
    config.write_bytes(b"#" * _READ_LIMIT)
    config.chmod(0o640)

    snapshot = _read_repository_text(config)

    assert snapshot == ("#" * _READ_LIMIT, stat.S_IMODE(config.stat().st_mode))
