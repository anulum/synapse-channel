# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — durable waker transition recovery tests
"""Exercise real transition files, controller exit, and operator acknowledgement."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from _platform_caps import requires_proc
from synapse_channel.waker_config import waker_config_path
from synapse_channel.waker_transition import (
    WakerTransitionError,
    transition_state,
    waker_transition,
)

IDENTITY = "transition/worker"
pytestmark = requires_proc


def test_intent_precedes_effect_and_completion_is_durable(tmp_path: Path) -> None:
    assert transition_state(IDENTITY, home=tmp_path) == "idle"
    with waker_transition(IDENTITY, 1, "install", home=tmp_path) as operation:
        assert transition_state(IDENTITY, home=tmp_path, generation=1) == "pending"
        path = waker_config_path(IDENTITY, home=tmp_path).with_suffix(".transition.json")
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert json.loads(path.read_text())["pid"] == os.getpid()
        with pytest.raises(WakerTransitionError, match="still pending"):
            with waker_transition(IDENTITY, 2, "resume", home=tmp_path):
                pytest.fail("nested transition admitted")
        operation.complete()
    assert transition_state(IDENTITY, home=tmp_path, generation=1) == "idle"
    assert transition_state(IDENTITY, home=tmp_path, generation=2) == "uncertain"


@pytest.mark.parametrize("error", [RuntimeError, KeyboardInterrupt])
def test_exception_retains_uncertainty_even_after_completion_request(
    tmp_path: Path,
    error: type[BaseException],
) -> None:
    with pytest.raises(error):
        with waker_transition(IDENTITY, 1, "install", home=tmp_path) as operation:
            operation.complete()
            raise error()
    assert transition_state(IDENTITY, home=tmp_path) == "uncertain"
    with pytest.raises(WakerTransitionError, match="recovery required"):
        with waker_transition(IDENTITY, 2, "resume", home=tmp_path):
            pytest.fail("uncertain outcome accepted")
    for verb in ("stop", "configure"):
        with waker_transition(IDENTITY, 2, verb, home=tmp_path) as operation:
            assert transition_state(IDENTITY, home=tmp_path) == "uncertain"
            operation.complete()
        assert transition_state(IDENTITY, home=tmp_path) == "uncertain"
    with waker_transition(
        IDENTITY,
        3,
        "resume",
        home=tmp_path,
        acknowledge_uncertain=True,
    ) as operation:
        operation.complete()
    assert transition_state(IDENTITY, home=tmp_path) == "idle"


def test_controller_exit_leaves_orphaned_intent_not_readiness(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import os, sys; from pathlib import Path; "
            "from synapse_channel.waker_transition import waker_transition; "
            "ctx = waker_transition(sys.argv[1], 1, 'resume', home=Path(sys.argv[2])); "
            "ctx.__enter__(); os._exit(0)",
            IDENTITY,
            str(tmp_path),
        ],
        check=False,
        timeout=10,
    )
    assert result.returncode == 0
    assert transition_state(IDENTITY, home=tmp_path) == "uncertain"


def test_zombie_controller_cannot_authorise_start(tmp_path: Path) -> None:
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import os, sys; from pathlib import Path; "
            "from synapse_channel.waker_transition import waker_transition; "
            "ctx = waker_transition(sys.argv[1], 1, 'resume', home=Path(sys.argv[2])); "
            "ctx.__enter__(); os._exit(0)",
            IDENTITY,
            str(tmp_path),
        ],
    )
    try:
        deadline = time.monotonic() + 5
        while Path(f"/proc/{process.pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z":
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert transition_state(IDENTITY, home=tmp_path) == "uncertain"
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


@pytest.mark.parametrize(
    ("operation", "generation", "ack"),
    [("unknown", 1, False), ("resume", 0, False), ("resume", True, False), ("stop", 1, True)],
)
def test_invalid_intent_has_no_record(
    tmp_path: Path,
    operation: str,
    generation: int,
    ack: bool,
) -> None:
    with pytest.raises(WakerTransitionError):
        with waker_transition(
            IDENTITY,
            generation,
            operation,
            home=tmp_path,
            acknowledge_uncertain=ack,
        ):
            pytest.fail("invalid transition admitted")
    assert transition_state(IDENTITY, home=tmp_path) == "idle"


@pytest.mark.parametrize(
    "change",
    [
        {"schema_version": 2},
        {"schema_version": True},
        {"pid": 0},
        {"pid": False},
        {"generation": "1"},
        {"identity": "another/worker"},
        {"state": "broken"},
        {"operation": ""},
        {"boot_id": "\n"},
        {"start_ticks": None},
    ],
)
def test_malformed_persisted_record_cannot_authorise_runtime(
    tmp_path: Path,
    change: dict[str, object],
) -> None:
    with waker_transition(IDENTITY, 1, "install", home=tmp_path):
        pass
    path = waker_config_path(IDENTITY, home=tmp_path).with_suffix(".transition.json")
    document = json.loads(path.read_text())
    document.update(change)
    path.write_text(json.dumps(document))
    assert transition_state(IDENTITY, home=tmp_path) == "uncertain"


@pytest.mark.parametrize("contents", ["{", "[]", "{}"])
def test_unreadable_record_requires_repair(tmp_path: Path, contents: str) -> None:
    with waker_transition(IDENTITY, 1, "install", home=tmp_path):
        pass
    path = waker_config_path(IDENTITY, home=tmp_path).with_suffix(".transition.json")
    path.write_text(contents)
    assert transition_state(IDENTITY, home=tmp_path) == "uncertain"
    with pytest.raises(ValueError):
        with waker_transition(
            IDENTITY,
            2,
            "resume",
            home=tmp_path,
            acknowledge_uncertain=True,
        ):
            pytest.fail("corrupt record acknowledged as valid")


def test_reused_controller_pid_does_not_revive_intent(tmp_path: Path) -> None:
    with waker_transition(IDENTITY, 1, "install", home=tmp_path):
        path = waker_config_path(IDENTITY, home=tmp_path).with_suffix(".transition.json")
        document = json.loads(path.read_text())
        document["start_ticks"] = "0"
        path.write_text(json.dumps(document))
        assert transition_state(IDENTITY, home=tmp_path) == "uncertain"


def test_next_generation_intent_does_not_misclassify_old_live_generation(tmp_path: Path) -> None:
    with waker_transition(IDENTITY, 2, "resume", home=tmp_path):
        assert transition_state(IDENTITY, home=tmp_path, generation=1) == "pending"
        assert transition_state(IDENTITY, home=tmp_path, generation=4) == "uncertain"
