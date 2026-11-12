# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — run the effect-safety suite's variants and write their signed records
"""Run each variant in a fresh workspace, judge it from the observer's readings, sign it.

A variant's verdict is ``pass`` when every reading matches what the case table
expects, ``fail`` with the mismatches otherwise, ``unknown`` when a reading
could not be made, and ``not_applicable`` with its reason. The verdict never
comes from the system under test's own report.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import synapse_channel
from synapse_channel.effect_suite import reference_adapter
from synapse_channel.effect_suite.cases import (
    PROFILE,
    PROFILE_SENTENCE,
    SUITE_VERSION,
    VARIANTS,
    Expected,
    Variant,
)
from synapse_channel.effect_suite.observer import (
    EFFECT_COUNT_SOURCE,
    OBSERVER,
    GitReading,
    GitState,
    JournalReading,
    Observer,
    journal_reading,
)
from synapse_channel.effect_suite.record import RECORD_SCHEMA, SigningKey, sign_record
from synapse_channel.effect_suite.reference_adapter import (
    APPROVER,
    EXECUTOR,
    REVIEWER,
    ReferenceAdapter,
)
from synapse_channel.effect_suite.session import HubSession, SuiteSessionError
from synapse_channel.effect_suite.variants import DRIVERS, VERDICT_GATE_OFF, Stage
from synapse_channel.effect_suite.workspace import prepare_repository, suite_hub
from synapse_channel.git.git_runtime import GitError

STRANGER = "OTHER/stranger"


@dataclass(frozen=True)
class Outcome:
    """One variant's verdict and what it rests on.

    ``states`` holds the branch's state before the variant acted and after it ended.
    """

    variant: Variant
    verdict: str
    reason: str
    answers: tuple[str, ...] = ()
    git: GitReading | None = None
    journal: JournalReading | None = None
    states: tuple[GitState, GitState] | None = None


def _digest_of_tree(root: Path) -> str:
    """Return SHA-256 over the relative paths and bytes of every ``.py`` file under ``root``."""
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        digest.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
        digest.update(path.read_bytes() + b"\0")
    return digest.hexdigest()


def system_identity() -> dict[str, str]:
    """Return the system under test's name, version, source digest and adapter digest."""
    package = Path(synapse_channel.__file__).parent
    adapter = Path(reference_adapter.__file__).read_bytes()
    return {
        "name": "synapse-channel",
        "version": synapse_channel.__version__,
        "source_sha256": _digest_of_tree(package),
        "adapter_sha256": hashlib.sha256(adapter).hexdigest(),
    }


def judge(
    expected: Expected, answers: tuple[str, ...], git: GitReading, journal: JournalReading
) -> tuple[str, str]:
    """Return ``pass`` or ``fail`` with the readings that do not match."""
    readings = (
        ("answers", answers, expected.answers),
        ("head", git.head, expected.head),
        ("attempts", journal.attempts, expected.attempts),
        ("effects", git.effects, expected.effects),
        ("verified", git.verified, expected.verified),
        ("gate_refusals", journal.gate_refusals, expected.gate_refusals),
    )
    mismatches = [
        f"{name} {seen!r}, expected {wanted!r}" for name, seen, wanted in readings if seen != wanted
    ]
    return ("fail", "; ".join(mismatches)) if mismatches else ("pass", "every reading matches")


async def run_variant(variant: Variant, directory: Path, *, git: str) -> Outcome:
    """Run ``variant`` in ``directory`` and return its verdict."""
    if variant.expected is None:
        return Outcome(variant, "not_applicable", variant.not_applicable)
    task_id = f"suite-{variant.name}"
    try:
        repository = prepare_repository(directory, git=git)
        async with suite_hub(directory, verdict_gate=variant.name not in VERDICT_GATE_OFF) as hub:
            async with contextlib.AsyncExitStack() as stack:
                names = (EXECUTOR, REVIEWER, APPROVER, OBSERVER, STRANGER)
                ex, rv, ap, ob, st = [
                    await stack.enter_async_context(HubSession(n, hub.uri)) for n in names
                ]
                stage = Stage(
                    repository,
                    ReferenceAdapter(repository, ex, rv, ap, task_id),
                    Observer(repository, ob, task_id),
                    st,
                )
                before = stage.observer.state()
                await stage.sut.register_executor()
                await DRIVERS[variant.name](stage)
        reading = stage.observer.git_reading(repository.commits.x)
        after = stage.observer.state()
        journal = journal_reading(hub.journal, task_id)
    except (SuiteSessionError, GitError) as exc:
        return Outcome(variant, "unknown", f"a reading could not be made: {exc}")
    answers = tuple(stage.answers)
    verdict, reason = judge(variant.expected, answers, reading, journal)
    return Outcome(variant, verdict, reason, answers, reading, journal, (before, after))


def compose_record(outcome: Outcome, system: dict[str, str]) -> dict[str, Any]:
    """Return the unsigned record of ``outcome``."""
    variant, expected = outcome.variant, outcome.variant.expected
    observed: dict[str, Any] | None = None
    if outcome.git is not None and outcome.journal is not None and outcome.states is not None:
        before, after = outcome.states
        observed = {
            "state_before": {"head": before.head, "tree": before.tree},
            "state_after": {"head": after.head, "tree": after.tree},
            "answers": list(outcome.answers),
            "head": outcome.git.head,
            "attempts": outcome.journal.attempts,
            "effects": outcome.git.effects,
            "effects_counted_from": EFFECT_COUNT_SOURCE,
            "verified": outcome.git.verified,
            "gate_refusals": outcome.journal.gate_refusals,
            "journal_answers": list(outcome.journal.answers),
            "journal_last_seq": outcome.journal.last_seq,
        }
    return {
        "schema": RECORD_SCHEMA,
        "suite_version": SUITE_VERSION,
        "case": variant.case,
        "variant": variant.name,
        "title": variant.title,
        "profile_claimed": PROFILE,
        "profile_computed": PROFILE,
        "profile_note": PROFILE_SENTENCE,
        "system": system,
        "expected": None
        if expected is None
        else {
            "answers": list(expected.answers),
            "head": expected.head,
            "attempts": expected.attempts,
            "effects": expected.effects,
            "verified": expected.verified,
            "gate_refusals": expected.gate_refusals,
        },
        "observed": observed,
        "verdict": outcome.verdict,
        "reason": outcome.reason,
    }


async def run_suite(
    output: Path, key: SigningKey, *, git: str, names: tuple[str, ...] = ()
) -> list[tuple[Outcome, Path]]:
    """Run the chosen variants (all by default), write one signed record each, return them."""
    output.mkdir(parents=True, exist_ok=True)
    system = system_identity()
    written: list[tuple[Outcome, Path]] = []
    for variant in VARIANTS:
        if names and variant.name not in names:
            continue
        with tempfile.TemporaryDirectory(dir=output, prefix=f"work-{variant.name}-") as work:
            outcome = await run_variant(variant, Path(work), git=git)
        record = sign_record(compose_record(outcome, system), key)
        path = output / f"effect-suite-{variant.name}.json"
        path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        written.append((outcome, path))
    return written
