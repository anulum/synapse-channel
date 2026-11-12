# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — every variant of cases 1 to 4 run against this stack, and the records
"""Each applicable variant ends as its case says, read by the suite's own observer.

The tests assert the observer's readings themselves, not only the verdict, so a
judge that always passes cannot hide a wrong reading.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import synapse_channel
from synapse_channel.core.receipt_signing import (
    generate_receipt_signing_key,
    load_receipt_verification_key,
)
from synapse_channel.effect_suite.cases import PROFILE, VARIANTS, Variant, variant_named
from synapse_channel.effect_suite.observer import (
    EFFECT_COUNT_SOURCE,
    GitReading,
    GitState,
    JournalReading,
)
from synapse_channel.effect_suite.record import (
    SIGNATURE_DOMAIN,
    load_signing_key,
    record_bytes,
    verification_refusal,
)
from synapse_channel.effect_suite.runner import (
    Outcome,
    compose_record,
    judge,
    run_suite,
    run_variant,
)
from synapse_channel.effect_suite.variants import DRIVERS, Stage
from synapse_channel.effect_suite.workspace import git_out
from synapse_channel.git.compare_and_swap import git_executable

APPLICABLE = [variant for variant in VARIANTS if variant.expected is not None]
REPLAYED = {"4e", "4h"}
"""Variants whose last request repeats a key; the hub answers it from the journal."""


@pytest.mark.parametrize("variant", APPLICABLE, ids=[variant.name for variant in APPLICABLE])
async def test_every_applicable_variant_ends_as_its_case_says(
    variant: Variant, tmp_path: Path
) -> None:
    expected = variant.expected
    assert expected is not None
    outcome = await run_variant(variant, tmp_path, git=git_executable())
    assert (outcome.verdict, outcome.reason) == ("pass", "every reading matches")
    assert outcome.answers == expected.answers
    assert outcome.git == GitReading(expected.head, expected.effects, expected.verified)
    assert outcome.journal is not None
    attempt_answers = tuple(answer for answer in expected.answers if ":" not in answer)
    journalled = attempt_answers[:-1] if variant.name in REPLAYED else attempt_answers
    assert outcome.journal.answers == journalled
    assert (outcome.journal.attempts, outcome.journal.gate_refusals) == (
        expected.attempts,
        expected.gate_refusals,
    )
    assert outcome.states is not None
    before, after = outcome.states
    assert all(len(name) == 40 for state in (before, after) for name in (state.head, state.tree))
    assert (after == before) is (expected.head == "b")


async def test_a_variant_this_profile_cannot_run_is_not_applicable_and_touches_nothing(
    tmp_path: Path,
) -> None:
    variant = variant_named("3f")
    outcome = await run_variant(variant, tmp_path, git="/nonexistent/git")
    assert outcome == Outcome(
        variant, "not_applicable", "intents of this version carry no revision"
    )
    assert list(tmp_path.iterdir()) == []


async def test_a_reading_that_cannot_be_made_is_unknown_never_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def unreadable(stage: Stage) -> None:
        path, git = stage.repository.path, stage.repository.git
        git_out(path, ["rev-parse", "--verify", "--quiet", "refs/heads/absent"], git=git)

    monkeypatch.setitem(DRIVERS, "1", unreadable)
    outcome = await run_variant(variant_named("1"), tmp_path, git=git_executable())
    assert outcome == Outcome(
        variant_named("1"),
        "unknown",
        "a reading could not be made: git rev-parse failed: exit code 1",
    )


def test_the_judge_names_every_reading_that_differs_and_passes_only_when_all_match() -> None:
    expected = variant_named("1").expected
    assert expected is not None
    wrong = judge(
        expected,
        ("declare:accepted", "not_approved"),
        GitReading("b", 0, False),
        JournalReading(0, ("not_approved",), 1, 9),
    )
    assert wrong == (
        "fail",
        "answers ('declare:accepted', 'not_approved'), expected ('declare:accepted', "
        "'approved_fresh'); head 'b', expected 'x'; attempts 0, expected 1; effects 0, "
        "expected 1; verified False, expected True; gate_refusals 1, expected 0",
    )
    right = judge(
        expected,
        expected.answers,
        GitReading("x", 1, True),
        JournalReading(1, ("approved_fresh",), 0, 9),
    )
    assert right == ("pass", "every reading matches")


async def test_the_suite_writes_one_signed_record_per_chosen_variant(tmp_path: Path) -> None:
    key_path = tmp_path / "keys" / "suite.key"
    key_path.parent.mkdir()
    generate_receipt_signing_key(key_path)
    output = tmp_path / "records"
    written = await run_suite(
        output, load_signing_key(key_path), git=git_executable(), names=("2d", "3f")
    )
    assert [(outcome.variant.name, path.name) for outcome, path in written] == [
        ("2d", "effect-suite-2d.json"),
        ("3f", "effect-suite-3f.json"),
    ]
    assert sorted(path.name for path in output.iterdir()) == [
        "effect-suite-2d.json",
        "effect-suite-3f.json",
    ]
    public = load_receipt_verification_key(key_path.with_name("suite.key.pub"))
    dry, skipped = (json.loads(path.read_text(encoding="utf-8")) for _, path in written)
    assert [verification_refusal(record, public) for record in (dry, skipped)] == [None, None]
    assert dry["system"]["name"] == "synapse-channel"
    assert dry["system"]["version"] == synapse_channel.__version__
    assert [len(dry["system"][name]) for name in ("source_sha256", "adapter_sha256")] == [64, 64]
    assert (dry["profile_claimed"], dry["profile_computed"]) == (PROFILE, PROFILE)
    assert (dry["case"], dry["variant"], dry["verdict"]) == (2, "2d", "pass")
    assert dry["expected"] == {
        "answers": ["declare:accepted"],
        "head": "b",
        "attempts": 0,
        "effects": 0,
        "verified": False,
        "gate_refusals": 0,
    }
    observed = dry["observed"]
    assert {name: observed[name] for name in dry["expected"]} == dry["expected"]
    dry_states = written[0][0].states
    assert dry_states is not None
    assert (
        observed["state_before"]
        == observed["state_after"]
        == {
            "head": dry_states[0].head,
            "tree": dry_states[0].tree,
        }
    )
    assert observed["effects_counted_from"] == EFFECT_COUNT_SOURCE
    assert observed["journal_answers"] == []
    assert observed["journal_last_seq"] > 0
    assert (skipped["expected"], skipped["observed"], skipped["verdict"]) == (
        None,
        None,
        "not_applicable",
    )


GOLDEN_SIGNED_BYTES_SHA256 = "12665a8d86c65733564c90acb24dcfd37a181b881291f325e056b5939d912faf"
"""SHA-256 of the signed bytes of the fixed record below; a layout change shows here."""


def test_the_record_layout_is_pinned_by_the_digest_of_its_signed_bytes() -> None:
    variant = variant_named("3d")
    assert variant.expected is not None
    outcome = Outcome(
        variant,
        "pass",
        "every reading matches",
        variant.expected.answers,
        GitReading("o", 0, False),
        JournalReading(1, ("approved_fresh",), 0, 10),
        (GitState("b" * 40, "c" * 40), GitState("d" * 40, "e" * 40)),
    )
    system = {
        "name": "synapse-channel",
        "version": "0.0.0",
        "source_sha256": "0" * 64,
        "adapter_sha256": "1" * 64,
    }
    signed_bytes = SIGNATURE_DOMAIN + record_bytes(compose_record(outcome, system))
    assert hashlib.sha256(signed_bytes).hexdigest() == GOLDEN_SIGNED_BYTES_SHA256
