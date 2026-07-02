# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — version-conflict resolution advice regressions

from __future__ import annotations

from pathlib import Path

import pytest

from synapse_channel.core.version_constraints import VersionInterval
from synapse_channel.core.version_resolution import (
    ConsumerConstraint,
    advise_package,
    render_interval,
    render_intervals,
    render_resolution_markdown,
    resolution_to_json,
    run_resolution_advice,
)


def _consumer(repo: str, constraint: str) -> ConsumerConstraint:
    return ConsumerConstraint(repo=repo, constraint=constraint, manifest="pyproject.toml")


class TestAdvisePackage:
    def test_single_outlier_is_named_with_the_remainder_range(self) -> None:
        advice = advise_package(
            "shared-dep",
            "python",
            (
                _consumer("alpha", ">=4.1,<5"),
                _consumer("beta", ">=4.2,<4.3"),
                _consumer("gamma", "==2.9"),
            ),
        )

        assert [odd.repo for odd in advice.odd_ones_out] == ["gamma"]
        assert advice.odd_ones_out[0].constraint == "==2.9"
        assert advice.odd_ones_out[0].remainder == ">=4.2, <4.3"
        assert advice.unassessed == ()

    def test_two_disjoint_consumers_are_each_the_outlier(self) -> None:
        advice = advise_package(
            "shared-dep",
            "python",
            (_consumer("alpha", "==1.0"), _consumer("beta", "==2.0")),
        )

        assert [(odd.repo, odd.remainder) for odd in advice.odd_ones_out] == [
            ("alpha", "==2.0"),
            ("beta", "==1.0"),
        ]

    def test_mutually_disjoint_camps_have_no_outlier(self) -> None:
        advice = advise_package(
            "shared-dep",
            "python",
            (
                _consumer("alpha", "==1.0"),
                _consumer("beta", "==2.0"),
                _consumer("gamma", "==3.0"),
            ),
        )

        assert advice.odd_ones_out == ()

    def test_unmodellable_declaration_is_listed_not_silently_skipped(self) -> None:
        advice = advise_package(
            "shared-dep",
            "python",
            (
                _consumer("alpha", "==1.0"),
                _consumer("beta", "==2.0"),
                _consumer("delta", ">=1.0a1"),
            ),
        )

        assert [item.repo for item in advice.unassessed] == ["delta"]
        assert [item.repo for item in advice.consumers] == ["alpha", "beta"]

    def test_lone_comparable_declaration_has_no_others_to_reconcile(self) -> None:
        # with every other declaration outside the model there is nothing to
        # intersect against, so no outlier claim is made
        advice = advise_package(
            "shared-dep",
            "python",
            (_consumer("alpha", "==1.0"), _consumer("delta", ">=1.0a1")),
        )

        assert advice.odd_ones_out == ()
        assert [item.repo for item in advice.unassessed] == ["delta"]

    def test_empty_constraint_models_as_any_version(self) -> None:
        advice = advise_package(
            "shared-dep",
            "python",
            (
                _consumer("alpha", "==1.0"),
                _consumer("beta", "==2.0"),
                _consumer("open", ""),
            ),
        )

        # removing 'open' leaves ==1.0 vs ==2.0, still disjoint; removing
        # alpha or beta leaves the other pin intersected with 'any version'
        assert [(odd.repo, odd.remainder) for odd in advice.odd_ones_out] == [
            ("alpha", "==2.0"),
            ("beta", "==1.0"),
        ]


class TestSuggestedPin:
    def test_inclusive_lower_bound_inside_the_remainder_is_suggested(self) -> None:
        advice = advise_package(
            "shared-dep",
            "python",
            (
                _consumer("alpha", ">=4.1,<5"),
                _consumer("beta", ">=4.2,<4.3"),
                _consumer("gamma", "==2.9"),
            ),
        )

        outlier = advice.odd_ones_out[0]
        assert (outlier.repo, outlier.suggested_pin, outlier.pin_source) == (
            "gamma",
            "4.2",
            "beta",
        )

    def test_exact_pin_of_the_remaining_consumer_is_the_evidence(self) -> None:
        advice = advise_package(
            "shared-dep",
            "python",
            (_consumer("alpha", "==1.0"), _consumer("beta", "==2.0")),
        )

        assert [(odd.repo, odd.suggested_pin, odd.pin_source) for odd in advice.odd_ones_out] == [
            ("alpha", "2.0", "beta"),
            ("beta", "1.0", "alpha"),
        ]

    def test_exclusive_bounds_are_never_evidence(self) -> None:
        # the remaining consumer's fence-posts (>1.0, <2.0) need not exist
        # as published versions, so nothing is suggested
        advice = advise_package(
            "shared-dep",
            "python",
            (_consumer("alpha", ">1.0,<2.0"), _consumer("beta", "==5.0")),
        )

        outlier = next(odd for odd in advice.odd_ones_out if odd.repo == "beta")
        assert outlier.remainder == ">1.0, <2.0"
        assert outlier.suggested_pin is None
        assert outlier.pin_source is None

    def test_declared_version_outside_the_remainder_is_not_suggested(self) -> None:
        # alpha names 1.0 but the remainder starts at 2.5 — only 2.5 qualifies
        advice = advise_package(
            "shared-dep",
            "python",
            (
                _consumer("alpha", ">=1.0,<3"),
                _consumer("beta", ">=2.5,<3"),
                _consumer("gamma", "==9.0"),
            ),
        )

        outlier = next(odd for odd in advice.odd_ones_out if odd.repo == "gamma")
        assert (outlier.suggested_pin, outlier.pin_source) == ("2.5", "beta")

    def test_highest_qualifying_declared_version_wins(self) -> None:
        advice = advise_package(
            "shared-dep",
            "python",
            (
                _consumer("alpha", ">=4.2,<5"),
                _consumer("beta", "<=4.7"),
                _consumer("gamma", "==1.0"),
            ),
        )

        outlier = next(odd for odd in advice.odd_ones_out if odd.repo == "gamma")
        assert (outlier.suggested_pin, outlier.pin_source) == ("4.7", "beta")

    def test_padded_version_tie_falls_to_the_first_repository(self) -> None:
        # 4.2 and 4.2.0 are the same release; the tie is deterministic
        advice = advise_package(
            "shared-dep",
            "python",
            (
                _consumer("zeta", ">=4.2"),
                _consumer("alpha", ">=4.2.0"),
                _consumer("gamma", "==1.0"),
            ),
        )

        outlier = next(odd for odd in advice.odd_ones_out if odd.repo == "gamma")
        assert (outlier.suggested_pin, outlier.pin_source) == ("4.2.0", "alpha")


class TestIntervalRendering:
    def test_unbounded_interval_renders_as_any_version(self) -> None:
        assert render_interval(VersionInterval()) == "any version"

    def test_exact_pin_renders_with_double_equals(self) -> None:
        pin = VersionInterval(low=(1, 2, 3), high=(1, 2, 3))
        assert render_interval(pin) == "==1.2.3"

    def test_bounds_render_with_their_inclusivity(self) -> None:
        interval = VersionInterval(low=(4, 1), low_inclusive=True, high=(5,), high_inclusive=False)
        assert render_interval(interval) == ">=4.1, <5"
        exclusive = VersionInterval(low=(4, 1), low_inclusive=False, high=(5,), high_inclusive=True)
        assert render_interval(exclusive) == ">4.1, <=5"

    def test_half_open_intervals_render_one_bound(self) -> None:
        assert render_interval(VersionInterval(low=(1,))) == ">=1"
        assert render_interval(VersionInterval(high=(2,), high_inclusive=False)) == "<2"

    def test_alternatives_join_with_or(self) -> None:
        rendered = render_intervals(
            (
                VersionInterval(low=(1,), high=(2,), high_inclusive=False),
                VersionInterval(low=(3,), high=(4,), high_inclusive=False),
            )
        )
        assert rendered == ">=1, <2 or >=3, <4"


def _write(repo: Path, relative: str, content: str) -> None:
    target = repo / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def _conflicting_org(tmp_path: Path) -> Path:
    root = tmp_path / "org"
    root.mkdir()
    for repo, pins in (
        ("alpha", '"shared-dep>=4.1,<5", "harmless>=1"'),
        ("beta", '"shared-dep==2.9", "harmless>=1"'),
        ("gamma", '"shared-dep>=4.2,<4.6", "lonely==1"'),
    ):
        directory = root / repo
        directory.mkdir()
        _write(
            directory,
            "pyproject.toml",
            f'[project]\nname = "{repo}-pkg"\ndependencies = [{pins}]\n',
        )
    return root


class TestRunResolutionAdvice:
    def test_only_provably_conflicting_packages_get_advice(self, tmp_path: Path) -> None:
        advice = run_resolution_advice(_conflicting_org(tmp_path))

        # 'harmless' overlaps and 'lonely' has one consumer — only
        # 'shared-dep' carries a provable conflict
        assert [item.package for item in advice] == ["shared-dep"]
        assert [odd.repo for odd in advice[0].odd_ones_out] == ["beta"]
        assert advice[0].odd_ones_out[0].remainder == ">=4.2, <4.6"

    def test_missing_root_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="missing repository root"):
            run_resolution_advice(tmp_path / "absent")


class TestRenderings:
    def test_json_carries_consumers_outliers_and_the_note(self, tmp_path: Path) -> None:
        advice = run_resolution_advice(_conflicting_org(tmp_path))

        payload = resolution_to_json(advice)

        assert payload[0]["package"] == "shared-dep"
        assert payload[0]["note"] == "advisory text; nothing rewrites a manifest"
        consumers = payload[0]["consumers"]
        assert isinstance(consumers, list)
        assert {item["repo"] for item in consumers} == {"alpha", "beta", "gamma"}
        outliers = payload[0]["odd_ones_out"]
        assert isinstance(outliers, list)
        assert outliers[0] == {
            "repo": "beta",
            "constraint": "==2.9",
            "remainder": ">=4.2, <4.6",
            "suggested_pin": "4.2",
            "pin_source": "gamma",
        }

    def test_markdown_names_the_outlier_and_the_remainder(self, tmp_path: Path) -> None:
        text = render_resolution_markdown(run_resolution_advice(_conflicting_org(tmp_path)))

        assert "## Suggested resolutions (1 conflicting package(s))" in text
        assert "### python shared-dep" in text
        assert (
            "- ODD ONE OUT: beta ('==2.9') — the other declarations reconcile at "
            ">=4.2, <4.6; 4.2 would satisfy them all (a version gamma already declares)" in text
        )

    def test_markdown_without_pin_evidence_states_only_the_range(self) -> None:
        text = render_resolution_markdown(
            (
                advise_package(
                    "shared-dep",
                    "python",
                    (_consumer("alpha", ">1.0,<2.0"), _consumer("beta", "==5.0")),
                ),
            )
        )

        assert (
            "- ODD ONE OUT: beta ('==5.0') — the other declarations reconcile at >1.0, <2.0" in text
        )
        assert "reconcile at >1.0, <2.0;" not in text

    def test_markdown_of_no_conflicts_says_so(self) -> None:
        assert "no provable version conflicts" in render_resolution_markdown(())

    def test_markdown_renders_camps_unassessed_and_open_declarations(self) -> None:
        advice = (
            advise_package(
                "shared-dep",
                "python",
                (
                    _consumer("alpha", "==1.0"),
                    _consumer("beta", "==2.0"),
                    _consumer("gamma", "==3.0"),
                    _consumer("delta", ">=1.0a1"),
                    _consumer("open", ""),
                ),
            ),
        )

        text = render_resolution_markdown(advice)

        assert "- open declares '(any version)' (pyproject.toml)" in text
        assert "- delta declares '>=1.0a1' (pyproject.toml) — outside the bounded model" in text
        assert "split into mutually disjoint camps" in text
