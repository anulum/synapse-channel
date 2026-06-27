# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL - fuzz target for bounded wire JSON decoding
"""Fuzz the bounded JSON decoder used by hub and A2A wire edges.

The harness accepts arbitrary bytes, feeds them through
``synapse_channel.core.protocol.loads_bounded``, and treats malformed JSON,
invalid UTF-8 bytes, and over-deep JSON as expected rejects. Any other exception
is a harness failure and should be investigated as a decoder bug.

Run a deterministic local smoke corpus with::

    PYTHONPATH=src python tools/fuzz_protocol_decode.py --smoke

Run an open-ended Atheris fuzzing session after installing Atheris with::

    PYTHONPATH=src python tools/fuzz_protocol_decode.py
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import cast

from synapse_channel.core.protocol import MAX_JSON_DEPTH, loads_bounded


@dataclass(frozen=True)
class FuzzSmokeResult:
    """Summary of deterministic fuzz seed corpus execution."""

    total: int
    accepted: int
    rejected: int
    crashed: int


EXPECTED_DECODE_ERRORS = (json.JSONDecodeError, UnicodeDecodeError)
"""Decode failures that represent safe rejection of invalid wire frames."""


def _seed_inputs() -> tuple[bytes, ...]:
    """Return deterministic seeds that exercise normal and hostile frames."""
    shallow_nested = ("[" * MAX_JSON_DEPTH + "]" * MAX_JSON_DEPTH).encode()
    too_deep = ("[" * (MAX_JSON_DEPTH + 1024) + "]" * (MAX_JSON_DEPTH + 1024)).encode()
    quoted_brackets = b'{"payload": "[[[[[[[[]]]]]]]]"}'
    return (
        b'{"sender": "agent", "type": "chat", "payload": "ok"}',
        b'{"sender": "agent", "type": "heartbeat"}',
        b"{not json",
        b"\xff\xfe\xfa",
        quoted_brackets,
        shallow_nested,
        too_deep,
        b"",
    )


def _decode_for_fuzz(data: bytes) -> bool:
    """Decode one fuzz input and return whether it was valid JSON."""
    try:
        loads_bounded(data)
    except EXPECTED_DECODE_ERRORS:
        return False
    return True


def fuzz_one_input(data: bytes) -> None:
    """Atheris-compatible fuzz callback for one raw wire frame."""
    _decode_for_fuzz(data)


def run_seed_corpus(seeds: Sequence[bytes] | None = None) -> FuzzSmokeResult:
    """Run the deterministic seed corpus and return aggregate outcomes."""
    accepted = 0
    rejected = 0
    crashed = 0
    inputs = tuple(_seed_inputs() if seeds is None else seeds)
    for data in inputs:
        try:
            if _decode_for_fuzz(data):
                accepted += 1
            else:
                rejected += 1
        except Exception:
            crashed += 1
    return FuzzSmokeResult(
        total=len(inputs),
        accepted=accepted,
        rejected=rejected,
        crashed=crashed,
    )


def _run_atheris(argv: Sequence[str]) -> int:
    """Start Atheris when it is installed, otherwise print an install hint."""
    try:
        atheris_module: ModuleType = importlib.import_module("atheris")
    except ModuleNotFoundError:
        print(
            "Atheris is not installed. Run smoke mode with --smoke, or install "
            "Atheris to start fuzzing.",
            file=sys.stderr,
        )
        return 2

    setup = cast(Callable[[list[str], Callable[[bytes], None]], None], atheris_module.Setup)
    fuzz = cast(Callable[[], None], atheris_module.Fuzz)
    setup(list(argv), fuzz_one_input)
    fuzz()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run the deterministic smoke corpus or an Atheris fuzzing session."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="run the deterministic seed corpus instead of starting Atheris",
    )
    args, passthrough = parser.parse_known_args(argv)
    if args.smoke:
        result = run_seed_corpus()
        print(
            "protocol fuzz smoke: "
            f"total={result.total} accepted={result.accepted} "
            f"rejected={result.rejected} crashed={result.crashed}"
        )
        return 0 if result.crashed == 0 else 1
    return _run_atheris([str(Path(__file__)), *passthrough])


if __name__ == "__main__":
    raise SystemExit(main())
