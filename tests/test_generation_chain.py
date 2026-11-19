# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — unit, corruption, directory-floor, and concurrency tests for generation_chain
"""Verify ``synapse_channel.core.generation_chain`` across single-step and concurrent races."""

from __future__ import annotations

import ctypes
import errno
import json
import multiprocessing
import os
import stat
from pathlib import Path
from typing import Any

import pytest

import synapse_channel.core.generation_chain as gc
from synapse_channel.core.generation_chain import (
    EMPTY_CHAIN_ARTEFACT,
    FORBIDDEN_FS_MAGICS,
    GENERATION_SCHEMA,
    KIND_APPLIED,
    KIND_SEALED,
    MAX_GENERATION,
    STATUS_CREATED,
    STATUS_REFUSED,
    GenerationChainCorruptionError,
    GenerationChainError,
    GenerationRecord,
    chain_head,
    generation_name,
    publish_generation,
    read_generation,
    seal_generation,
    validate_chain_dir,
)
from synapse_channel.core.secure_path import apply_owner_only_dir

OLD_SHA = "a" * 64
NEW_SHA = "b" * 64
THIRD_SHA = "c" * 64


def _private_chain(root: Path, name: str = "chain") -> Path:
    """Create and return an owner-only ``0o700`` chain directory."""
    directory = root / name
    directory.mkdir()
    apply_owner_only_dir(directory)
    return directory


def _run_racer(function: Any, index: int, job: tuple[Any, ...], barrier: Any, results: Any) -> None:
    """Wait at ``barrier`` so all processes race simultaneously, then push the result."""
    barrier.wait()
    results.put((index, function(job)))


def _race(function: Any, jobs: list[tuple[Any, ...]]) -> list[Any]:
    """Run every job in its own process, released together via a barrier."""
    ctx = multiprocessing.get_context(
        "fork" if "fork" in multiprocessing.get_all_start_methods() else None
    )
    barrier = ctx.Barrier(len(jobs))
    results: Any = ctx.SimpleQueue()
    processes = [
        ctx.Process(target=_run_racer, args=(function, idx, job, barrier, results))
        for idx, job in enumerate(jobs)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=30)
        assert process.exitcode == 0
    collected = dict(results.get() for _ in processes)
    return [collected[idx] for idx in range(len(jobs))]


def _chain_racer(args: tuple[str, int, str, str, str, str]) -> tuple[str, str, str]:
    """Execute one real library ``publish_generation`` or ``seal_generation`` call."""
    chain_str, base, operation, attempt, kind, artefact = args
    if kind == KIND_SEALED:
        out = seal_generation(chain_str, base=base, operation=operation, attempt=attempt)
    else:
        out = publish_generation(
            chain_str, base=base, operation=operation, attempt=attempt, artefact=artefact
        )
    return attempt, kind, out.status


def _check_then_write_racer(args: tuple[str, int, str, str, str, str]) -> tuple[str, str, str]:
    """Broken publication control: check existence first and then write the slot file."""
    chain_str, base, operation, attempt, kind, artefact = args
    target = Path(chain_str) / generation_name(base + 1)
    if target.exists():
        return attempt, kind, STATUS_REFUSED
    rec = GenerationRecord(
        generation=base + 1,
        operation=operation,
        attempt=attempt,
        kind=kind,
        artefact=artefact,
    )
    target.write_bytes(rec.canonical_bytes())
    return attempt, kind, STATUS_CREATED


def test_single_step_chain_invariants_and_carried_artefact_seal(tmp_path: Path) -> None:
    """Verify 20-digit naming, publish, seal vs effect ordering, partials, and carry."""
    chain = _private_chain(tmp_path)
    assert chain_head(chain) == 0
    assert read_generation(chain, 1) is None
    assert generation_name(1) == "gen-00000000000000000001.json"
    assert generation_name(MAX_GENERATION) == f"gen-{MAX_GENERATION:020d}.json"

    # Seal on an empty chain carries 64 zeros forward.
    empty_chain = _private_chain(tmp_path, "empty-sealed")
    sealed_zero = seal_generation(empty_chain, base=0, operation="op-0", attempt="att-0")
    assert sealed_zero.status == STATUS_CREATED
    assert sealed_zero.record.artefact == EMPTY_CHAIN_ARTEFACT
    assert sealed_zero.record.kind == KIND_SEALED
    assert read_generation(empty_chain, 1) == sealed_zero.record

    # First publication from base 0 creates generation 1; a second from base 0 is refused.
    first = publish_generation(
        chain, base=0, operation="op-1", attempt="attempt-1", artefact=NEW_SHA
    )
    assert first.status == STATUS_CREATED
    assert chain_head(chain) == 1
    second = publish_generation(
        chain, base=0, operation="op-1", attempt="attempt-2", artefact=OLD_SHA
    )
    assert second.status == STATUS_REFUSED
    assert second.record == first.record
    assert read_generation(chain, 1) == first.record

    # Seal first at base 1 carries generation 1's NEW_SHA forward and refuses a later effect.
    seal_win = seal_generation(chain, base=1, operation="op-2", attempt="attempt-3")
    assert seal_win.status == STATUS_CREATED
    assert seal_win.record.kind == KIND_SEALED
    assert seal_win.record.artefact == NEW_SHA
    late_effect = publish_generation(
        chain, base=1, operation="op-2", attempt="attempt-3", artefact=THIRD_SHA
    )
    assert late_effect.status == STATUS_REFUSED
    assert late_effect.record.kind == KIND_SEALED
    assert late_effect.record.artefact == NEW_SHA

    # Effect first at base 2 refuses a later seal, and the refused seal reads `applied`.
    eff_win = publish_generation(
        chain, base=2, operation="op-3", attempt="attempt-4", artefact=OLD_SHA
    )
    assert eff_win.status == STATUS_CREATED
    late_seal = seal_generation(chain, base=2, operation="op-3", attempt="attempt-4")
    assert late_seal.status == STATUS_REFUSED
    assert late_seal.record.kind == KIND_APPLIED
    assert late_seal.record.artefact == OLD_SHA

    # Applied and sealed records of the same attempt and artefact have distinct canonical bytes.
    applied_rec = GenerationRecord(
        generation=6, operation="op-x", attempt="x", kind=KIND_APPLIED, artefact=NEW_SHA
    )
    sealed_rec = GenerationRecord(
        generation=6, operation="op-x", attempt="x", kind=KIND_SEALED, artefact=NEW_SHA
    )
    assert applied_rec.canonical_bytes() != sealed_rec.canonical_bytes()

    # Unlinked .partial and non-generation files are ignored by chain_head.
    partial = chain / ".crashed.123.deadbeef.partial"
    partial.write_bytes(applied_rec.canonical_bytes())
    note = chain / "README.txt"
    note.write_text("note", encoding="utf-8")
    assert chain_head(chain) == 3
    assert read_generation(chain, 4) is None
    partial.unlink()

    # An attempt whose slot another attempt took is refused and never overwrites the winner.
    winner = publish_generation(
        chain, base=3, operation="op-4", attempt="attempt-6", artefact=THIRD_SHA
    )
    assert winner.status == STATUS_CREATED
    loser = publish_generation(
        chain, base=3, operation="op-4", attempt="attempt-5", artefact=OLD_SHA
    )
    assert loser.status == STATUS_REFUSED
    assert loser.record.attempt == "attempt-6"
    assert read_generation(chain, 4) == winner.record


def test_two_hundred_concurrent_rounds_of_five_effects_against_one_seal(tmp_path: Path) -> None:
    """Run 200 barrier-synchronised rounds of 5 effects vs 1 seal plus the broken control."""
    rounds = 200
    chain = _private_chain(tmp_path, "race-chain")
    for round_number in range(rounds):
        base = chain_head(chain)
        assert base == round_number
        jobs: list[tuple[Any, ...]] = [
            (
                str(chain),
                base,
                f"op-{round_number}",
                f"r{round_number}-p{idx}",
                KIND_APPLIED,
                NEW_SHA,
            )
            for idx in range(5)
        ]
        jobs.append(
            (str(chain), base, f"op-{round_number}", f"r{round_number}-p0", KIND_SEALED, OLD_SHA)
        )
        outcomes = _race(_chain_racer, jobs)
        winners = [
            (attempt, kind) for attempt, kind, status in outcomes if status == STATUS_CREATED
        ]
        assert len(winners) == 1
        found = read_generation(chain, base + 1)
        assert found is not None
        assert (found.attempt, found.kind) == winners[0]
        if winners[0][1] == KIND_SEALED:
            if base == 0:
                expected_carried = EMPTY_CHAIN_ARTEFACT
            else:
                prev = read_generation(chain, base)
                assert prev is not None
                expected_carried = prev.artefact
            assert found.artefact == expected_carried
        else:
            assert found.artefact == NEW_SHA
        assert chain_head(chain) == base + 1
        assert not list(chain.glob(".*.partial"))

    # Control: check-then-write raced across 200 rounds yields >1 winner in >=1 round.
    control = _private_chain(tmp_path, "control-chain")
    multi_winner_rounds = 0
    for round_number in range(rounds):
        jobs = [
            (
                str(control),
                round_number,
                f"op-{round_number}",
                f"c{round_number}-p{idx}",
                KIND_APPLIED,
                NEW_SHA,
            )
            for idx in range(5)
        ]
        jobs.append(
            (
                str(control),
                round_number,
                f"op-{round_number}",
                f"c{round_number}-p0",
                KIND_SEALED,
                OLD_SHA,
            )
        )
        outcomes = _race(_check_then_write_racer, jobs)
        if sum(status == STATUS_CREATED for _, _, status in outcomes) != 1:
            multi_winner_rounds += 1
    assert multi_winner_rounds > 0


def test_strict_reading_refuses_non_canonical_renamed_corrupt_and_symlinked_slots(
    tmp_path: Path,
) -> None:
    """Refuse whitespace, key order, renamed slots, bad keys, non-ASCII, size, and symlinks."""
    chain = _private_chain(tmp_path)
    valid = GenerationRecord(
        generation=1, operation="op-1", attempt="att-1", kind=KIND_APPLIED, artefact=NEW_SHA
    )
    slot1 = chain / generation_name(1)

    # Trailing newline (valid JSON, non-canonical bytes).
    slot1.write_bytes(valid.canonical_bytes() + b"\n")
    with pytest.raises(GenerationChainCorruptionError, match="canonical JSON encoding"):
        read_generation(chain, 1)

    # Unsorted keys (valid JSON, non-canonical key order).
    unsorted = (
        f'{{"schema":"{GENERATION_SCHEMA}","generation":1,"operation":"op-1",'
        f'"attempt":"att-1","kind":"applied","artefact":"{NEW_SHA}"}}'
    ).encode("ascii")
    slot1.write_bytes(unsorted)
    with pytest.raises(GenerationChainCorruptionError, match="canonical JSON encoding"):
        read_generation(chain, 1)

    # Renamed slot: generation 1 bytes placed under gen-00000000000000000002.json.
    slot1.write_bytes(valid.canonical_bytes())
    slot2 = chain / generation_name(2)
    slot2.write_bytes(valid.canonical_bytes())
    with pytest.raises(GenerationChainCorruptionError, match="records generation 1, expected 2"):
        read_generation(chain, 2)

    # Extra or missing key.
    extra = dict(valid.to_dict(), extra="1")
    slot2.write_bytes(json.dumps(extra, sort_keys=True, separators=(",", ":")).encode("ascii"))
    with pytest.raises(GenerationChainCorruptionError, match="exact synapse-generation.v1 keys"):
        read_generation(chain, 2)

    # Invalid field value inside exact key set.
    bad_field = dict(valid.to_dict(), generation=2, kind="unknown")
    slot2.write_bytes(json.dumps(bad_field, sort_keys=True, separators=(",", ":")).encode("ascii"))
    with pytest.raises(GenerationChainCorruptionError, match="kind must be"):
        read_generation(chain, 2)

    # Malformed JSON / non-ASCII bytes.
    slot2.write_bytes(b"\xff\xfe")
    with pytest.raises(GenerationChainCorruptionError, match="not valid ASCII JSON"):
        read_generation(chain, 2)

    # Oversize slot file.
    slot2.write_bytes(b" " * 5000)
    with pytest.raises(GenerationChainCorruptionError, match="exceeds size bound"):
        read_generation(chain, 2)
    slot2.unlink()

    # Directory at slot path -> not a regular file.
    slot2.mkdir()
    with pytest.raises(GenerationChainCorruptionError, match="not a regular file"):
        read_generation(chain, 2)
    slot2.rmdir()

    # Symlink at slot path -> refused via O_NOFOLLOW.
    outside = tmp_path / "outside.json"
    outside.write_bytes(valid.canonical_bytes())
    slot2.symlink_to(outside)
    with pytest.raises(GenerationChainCorruptionError, match="cannot open generation file"):
        read_generation(chain, 2)


def test_gap_below_head_and_invalid_generation_filenames_are_refused(tmp_path: Path) -> None:
    """Refuse missing generations below head, generation 0, and numbers > 2**64 - 1."""
    chain = _private_chain(tmp_path)
    rec1 = GenerationRecord(
        generation=1, operation="op-1", attempt="a1", kind=KIND_APPLIED, artefact=OLD_SHA
    )
    rec3 = GenerationRecord(
        generation=3, operation="op-1", attempt="a3", kind=KIND_APPLIED, artefact=NEW_SHA
    )
    (chain / generation_name(1)).write_bytes(rec1.canonical_bytes())
    (chain / generation_name(3)).write_bytes(rec3.canonical_bytes())

    with pytest.raises(
        GenerationChainCorruptionError,
        match="missing gen-00000000000000000002.json",
    ):
        chain_head(chain)

    # Generation 00000000000000000000 is invalid.
    zero_chain = _private_chain(tmp_path, "zero-chain")
    (zero_chain / ("gen-" + "0" * 20 + ".json")).write_bytes(rec1.canonical_bytes())
    with pytest.raises(GenerationChainCorruptionError, match="invalid generation file name"):
        chain_head(zero_chain)

    # 20-digit number exceeding 2**64 - 1 is invalid.
    huge_chain = _private_chain(tmp_path, "huge-chain")
    (huge_chain / ("gen-" + "9" * 20 + ".json")).write_bytes(rec1.canonical_bytes())
    with pytest.raises(GenerationChainCorruptionError, match="invalid generation file name"):
        chain_head(huge_chain)


def test_newline_suffixed_generation_filename_is_ignored(tmp_path: Path) -> None:
    """Ignore newline-suffixed names both alone and beside a real generation file."""
    chain = _private_chain(tmp_path, "newline-chain")
    rec1 = GenerationRecord(
        generation=1, operation="op-1", attempt="a1", kind=KIND_APPLIED, artefact=OLD_SHA
    )
    (chain / (generation_name(1) + "\n")).write_bytes(rec1.canonical_bytes())
    assert chain_head(chain) == 0
    assert read_generation(chain, 1) is None

    published = publish_generation(chain, base=0, operation="op-1", attempt="a1", artefact=OLD_SHA)
    assert published.status == STATUS_CREATED
    assert chain_head(chain) == 1
    assert read_generation(chain, 1) == published.record


def test_directory_security_and_network_or_fuse_statfs_refusals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refuse symlinks, group/world-accessible modes, foreign owners, and NFS/CIFS/SMB/FUSE."""
    real_dir = _private_chain(tmp_path, "real")
    assert validate_chain_dir(real_dir) == real_dir

    # Symlink directory refused.
    link_dir = tmp_path / "chain-link"
    link_dir.symlink_to(real_dir)
    with pytest.raises(GenerationChainError, match="symlink"):
        chain_head(link_dir)

    # Mode 0o775 refused.
    shared_dir = tmp_path / "shared"
    shared_dir.mkdir()
    os.chmod(shared_dir, 0o775)
    with pytest.raises(GenerationChainError, match="accessible by other users"):
        chain_head(shared_dir)

    # Foreign owner simulated via geteuid mismatch.
    monkeypatch.setattr(os, "geteuid", lambda: os.getuid() + 1)
    with pytest.raises(GenerationChainError, match="not owned by the effective user"):
        chain_head(real_dir)
    monkeypatch.undo()

    # Every forbidden network / FUSE statfs magic is refused.
    for magic in FORBIDDEN_FS_MAGICS:

        def _probe(_path: Path, value: int = magic) -> int:
            return value

        with pytest.raises(GenerationChainError, match="network or FUSE filesystem"):
            chain_head(real_dir, statfs_probe=_probe)

    # Non-Linux fallback (libc=None) and statfs64 failure error handling.
    assert gc._linux_statfs_type(real_dir, libc=None) is None

    class _FailingLibc:
        @staticmethod
        def statfs64(_path: bytes, _buf: object) -> int:
            ctypes.set_errno(errno.EIO)
            return -1

    with pytest.raises(GenerationChainError, match="statfs64 failed"):
        gc._linux_statfs_type(real_dir, libc=_FailingLibc())

    # Non-POSIX directory fsync no-op branch.
    gc._fsync_directory(real_dir, posix=False)


def test_validation_bounds_missing_base_and_vanished_slot_edges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cover record/argument validation bounds, missing base refusal, and vanished-slot guard."""
    chain = _private_chain(tmp_path)

    bad_bool: Any = True
    with pytest.raises(GenerationChainError, match="must be an integer"):
        generation_name(bad_bool)
    with pytest.raises(GenerationChainError, match="must lie in"):
        generation_name(0)
    with pytest.raises(GenerationChainError, match="unsupported generation schema"):
        GenerationRecord(1, "op", "att", KIND_APPLIED, OLD_SHA, schema="bad")
    with pytest.raises(GenerationChainError, match="operation has the wrong form"):
        GenerationRecord(1, "bad space", "att", KIND_APPLIED, OLD_SHA)
    with pytest.raises(GenerationChainError, match="attempt has the wrong form"):
        GenerationRecord(1, "op", "", KIND_APPLIED, OLD_SHA)
    with pytest.raises(GenerationChainError, match="artefact must be 64 lower-case hex"):
        GenerationRecord(1, "op", "att", KIND_APPLIED, "A" * 64)

    with pytest.raises(GenerationChainError, match="cannot advance past"):
        publish_generation(
            chain, base=MAX_GENERATION, operation="op", attempt="att", artefact=OLD_SHA
        )
    with pytest.raises(GenerationChainError, match="cannot advance past"):
        seal_generation(chain, base=MAX_GENERATION, operation="op", attempt="att")

    with pytest.raises(GenerationChainError, match="base generation 2 does not exist"):
        publish_generation(chain, base=2, operation="op", attempt="att", artefact=OLD_SHA)
    with pytest.raises(GenerationChainError, match="base generation 2 does not exist"):
        seal_generation(chain, base=2, operation="op", attempt="att")

    # Vanished slot after FileExistsError raises GenerationChainCorruptionError.
    publish_generation(chain, base=0, operation="op", attempt="att-1", artefact=OLD_SHA)
    monkeypatch.setattr(gc, "read_generation", lambda *_a, **_kw: None)
    with pytest.raises(GenerationChainCorruptionError, match="vanished after FileExistsError"):
        publish_generation(chain, base=0, operation="op", attempt="att-2", artefact=NEW_SHA)
    monkeypatch.undo()
    assert stat.S_IMODE(chain.stat().st_mode) == 0o700
