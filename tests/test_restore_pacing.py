# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests: headroom and spacing before each restored seat
"""The restore's pacing: the limits a host may only tighten, its reading, and the wait."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from synapse_channel.restore_pacing import (
    DEFAULT_LIMITS,
    GIB,
    Limits,
    Reading,
    config_path,
    describe,
    load_limits,
    read_host,
    shortfall,
    wait_for_headroom,
)

ROOMY = Reading(available_bytes=32 * GIB, total_bytes=64 * GIB, load1=1.0, cpus=8, pressure=0.0)


def _config(tmp_path: Path, document: object) -> Path:
    path = tmp_path / "restore.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


# --- the limits ----------------------------------------------------------------------


def test_the_defaults_are_the_architects_floor() -> None:
    assert DEFAULT_LIMITS == Limits(4.0, 0.15, 0.75, 10.0, 30.0, 300.0)
    assert load_limits(None, "ws") == (DEFAULT_LIMITS, [])


def test_a_missing_file_or_an_unnamed_host_gives_the_defaults(tmp_path: Path) -> None:
    assert load_limits(tmp_path / "absent.json", "ws") == (DEFAULT_LIMITS, [])
    path = _config(tmp_path, {"hosts": {"other": {"min_available_gib": 9}}})
    assert load_limits(path, "ws") == (DEFAULT_LIMITS, [])


def test_a_host_may_only_tighten_its_limits(tmp_path: Path) -> None:
    path = _config(
        tmp_path,
        {
            "hosts": {
                "ws": {
                    "min_available_gib": 8,
                    "min_available_share": 0.1,  # looser
                    "max_load_per_cpu": 0.5,
                    "max_memory_pressure": 20,  # looser
                    "spacing_seconds": 60,
                    "timeout_seconds": 600,
                    "max_load_typo": 0.1,
                    "max_memory_pressure_": True,
                }
            }
        },
    )
    limits, notes = load_limits(path, "ws")
    assert limits == Limits(8.0, 0.15, 0.5, 10.0, 60.0, 600.0)
    assert notes == [
        "min_available_share: 0.1 is looser than the default 0.15, ignored",
        "max_memory_pressure: 20 is looser than the default 10.0, ignored",
        "max_load_typo: not a known number, ignored",
        "max_memory_pressure_: not a known number, ignored",
    ]


@pytest.mark.parametrize(
    ("name", "value", "applied"),
    [
        ("min_available_gib", 4.0, True),  # equal is not looser
        ("min_available_gib", 3.9, False),
        ("spacing_seconds", 29, False),
        ("max_load_per_cpu", 0.75, True),
        ("max_load_per_cpu", 0, False),  # no load at all would never start a seat
        ("max_memory_pressure", -1, False),
        ("timeout_seconds", 30, True),
        ("timeout_seconds", 29, False),
        ("timeout_seconds", 3600, True),
        ("timeout_seconds", 3601, False),
        ("min_available_gib", "8", False),
        ("min_available_gib", True, False),
    ],
)
def test_each_value_is_judged_against_its_default(
    tmp_path: Path, name: str, value: object, applied: bool
) -> None:
    limits, notes = load_limits(_config(tmp_path, {"hosts": {"ws": {name: value}}}), "ws")
    assert (limits != DEFAULT_LIMITS or value == getattr(DEFAULT_LIMITS, name)) is applied
    assert (notes == []) is applied


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ("not json", "cannot be read"),
        ([], "must hold an object with a 'hosts' object"),
        ({"hosts": []}, "must hold an object with a 'hosts' object"),
        ({"hosts": {"ws": 4}}, "hosts.ws must be an object"),
    ],
)
def test_a_file_that_cannot_be_read_is_an_error(
    tmp_path: Path, document: object, message: str
) -> None:
    path = tmp_path / "restore.json"
    path.write_text(document if isinstance(document, str) else json.dumps(document), "utf-8")
    with pytest.raises(ValueError, match=message):
        load_limits(path, "ws")


def test_the_configuration_lives_in_the_users_config_directory() -> None:
    assert config_path({"XDG_CONFIG_HOME": "/cfg", "HOME": "/h"}) == Path(
        "/cfg/synapse/restore.json"
    )
    assert config_path({"XDG_CONFIG_HOME": "rel", "HOME": "/h"}) == Path(
        "/h/.config/synapse/restore.json"
    )
    assert config_path({}) is None


def test_the_limits_read_as_one_line() -> None:
    assert describe(DEFAULT_LIMITS) == (
        "MemAvailable >= max(4 GiB, 15% of MemTotal); load1 <= 0.75 x CPUs; "
        "memory PSI some avg10 < 10; 30 s between starts; stop after 300 s without headroom"
    )


# --- reading the host ----------------------------------------------------------------


def _proc(tmp_path: Path, *, pressure: str | None) -> Path:
    root = tmp_path / "proc"
    root.mkdir()
    (root / "meminfo").write_text(
        "MemTotal:       16000000 kB\nMemFree:  100 kB\nMemAvailable:    8000000 kB\nHugePages:\n",
        encoding="ascii",
    )
    (root / "loadavg").write_text("2.50 1.00 0.50 1/100 4242\n", encoding="ascii")
    if pressure is not None:
        (root / "pressure").mkdir()
        (root / "pressure" / "memory").write_text(pressure, encoding="ascii")
    return root


def test_the_host_is_read_from_proc(tmp_path: Path) -> None:
    root = _proc(tmp_path, pressure="some avg10=3.25 avg60=0.00 avg300=0.00 total=1\nfull x\n")
    assert read_host(root, cpus=4) == Reading(8_192_000_000, 16_384_000_000, 2.5, 4, 3.25)


@pytest.mark.parametrize("pressure", [None, "", "some nothing here\n"])
def test_a_kernel_without_memory_pressure_reads_as_none(
    tmp_path: Path, pressure: str | None
) -> None:
    assert read_host(_proc(tmp_path, pressure=pressure), cpus=4).pressure is None


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="/proc is Linux-only")
def test_this_host_can_be_read() -> None:
    reading = read_host()
    assert 0 < reading.available_bytes <= reading.total_bytes and reading.cpus >= 1


# --- the shortfall and the wait ------------------------------------------------------


@pytest.mark.parametrize(
    ("reading", "short"),
    [
        (ROOMY, None),
        (
            Reading(3 * GIB, 8 * GIB, 0.0, 8, None),
            "memory available 3.0 GiB is below 4.0 GiB",
        ),
        (  # 15 % of a large host is above 4 GiB
            Reading(9 * GIB, 64 * GIB, 0.0, 8, None),
            "memory available 9.0 GiB is below 9.6 GiB",
        ),
        (Reading(32 * GIB, 64 * GIB, 6.01, 8, None), "load 6.01 is above 6.00"),
        (Reading(32 * GIB, 64 * GIB, 6.0, 8, 9.99), None),
        (
            Reading(32 * GIB, 64 * GIB, 1.0, 8, 10.0),
            "memory pressure 10.00 is not below 10",
        ),
    ],
)
def test_a_start_waits_for_memory_load_and_pressure(reading: Reading, short: str | None) -> None:
    assert shortfall(DEFAULT_LIMITS, reading) == short


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


async def test_the_spacing_is_waited_out_before_the_headroom_is_read() -> None:
    clock = _Clock()
    short = await wait_for_headroom(
        DEFAULT_LIMITS, read=lambda: ROOMY, clock=clock, sleep=clock.sleep, last_start=990.0
    )
    assert short is None and clock.slept == [20.0]
    first = await wait_for_headroom(
        DEFAULT_LIMITS, read=lambda: ROOMY, clock=clock, sleep=clock.sleep, last_start=None
    )
    assert first is None and clock.slept == [20.0]


async def test_the_wait_goes_on_until_there_is_headroom() -> None:
    clock = _Clock()
    readings = iter([Reading(GIB, 64 * GIB, 0.0, 8, None)] * 3 + [ROOMY])
    short = await wait_for_headroom(
        DEFAULT_LIMITS, read=lambda: next(readings), clock=clock, sleep=clock.sleep, last_start=None
    )
    assert short is None and clock.slept == [5.0, 5.0, 5.0]


async def test_no_headroom_in_time_names_what_stayed_short() -> None:
    clock = _Clock()
    limits = Limits(timeout_seconds=12.0)
    tight = Reading(32 * GIB, 64 * GIB, 7.0, 8, None)
    short = await wait_for_headroom(
        limits, read=lambda: tight, clock=clock, sleep=clock.sleep, last_start=None
    )
    assert short == "load 7.00 is above 6.00"
    assert clock.slept == [5.0, 5.0, 2.0]
