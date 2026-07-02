# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — declaration-level constraint comparison regressions

from __future__ import annotations

import pytest

from synapse_channel.core.version_constraints import (
    CONFLICT,
    NO_CONFLICT,
    NOT_COMPARABLE,
    VersionInterval,
    compare_constraints,
    constraint_intervals,
)

# --- PEP 440 (python) ---------------------------------------------------------


@pytest.mark.parametrize(
    ("left", "right", "verdict"),
    [
        ("==1.2", "==2.0", CONFLICT),
        ("==1.2", "==1.2.0", NO_CONFLICT),  # zero padding: 1.2 is 1.2.0
        (">=12,<16", ">=16", CONFLICT),  # exclusive high meets inclusive low
        (">=12,<16", ">=15", NO_CONFLICT),
        ("<=2", ">=2", NO_CONFLICT),  # both bounds include 2 itself
        ("<2", ">=2", CONFLICT),
        ("<2", ">2", CONFLICT),  # nothing lies both below and above 2
        ("~=1.2.3", ">=1.3", CONFLICT),  # compatible release stops at 1.3
        ("~=1.2", "==1.9", NO_CONFLICT),  # ~=1.2 spans up to (not incl.) 2.0
        ("==1.2.*", "==1.3.0", CONFLICT),
        ("==1.2.*", ">=1.2.5", NO_CONFLICT),
        (">=1,!=1.5", "==1.5", NO_CONFLICT),  # exclusions ignored: conservative
        ("===1.2", "==1.3", CONFLICT),  # numeric arbitrary equality models as ==
        (" >= 1.2 , < 2 ", "==1.5", NO_CONFLICT),  # whitespace tolerated
        (">=1,,<2", "==3", CONFLICT),  # empty clause skipped
        ("", "==0.1", NO_CONFLICT),  # no constraint means any version
        (">=2,<1", "==1.5", CONFLICT),  # self-contradictory set matches nothing
        ("==1.5", ">=2,<1", CONFLICT),  # ... on either side
        (">2,<=2", "==2", CONFLICT),  # empty by boundary exclusivity
        (">=2,>2", "<=2", CONFLICT),  # tie on low keeps the exclusive bound
        ("<2,<=2", ">=2", CONFLICT),  # tie on high keeps the exclusive bound
    ],
)
def test_python_verdicts(left: str, right: str, verdict: str) -> None:
    assert compare_constraints(left, right, "python") == verdict
    assert compare_constraints(right, left, "python") == verdict


@pytest.mark.parametrize(
    "constraint",
    [
        ">=1.0rc1",  # pre-release segment
        "==1!2.0",  # epoch
        "==1.0+local",  # local version
        "~=1",  # compatible release needs two segments
        "??",  # no recognised operator
        "==.*",  # wildcard with no base
        "@ https://example.invalid/pkg.tar.gz",  # direct URL reference
    ],
)
def test_python_unmodelled_forms_never_claim(constraint: str) -> None:
    assert compare_constraints(constraint, "==1.0", "python") == NOT_COMPARABLE
    assert compare_constraints("==1.0", constraint, "python") == NOT_COMPARABLE


# --- Cargo (rust) ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("left", "right", "verdict"),
    [
        ("^1.2.3", "^2.0", CONFLICT),
        ("^1.2.3", "^1.9", NO_CONFLICT),  # both live under major 1
        ("^0.2.3", "^0.3", CONFLICT),  # zero-major caret pins the minor
        ("^0.0.3", "^0.0.4", CONFLICT),  # zero-minor caret pins the patch
        ("^0.0.0", "^0.0.1", CONFLICT),  # all-zero caret bumps the last segment
        ("^0", "0.5", NO_CONFLICT),  # bare version reads as caret
        ("1.2", "~1.9", NO_CONFLICT),  # bare caret 1.2 spans to 2.0
        ("=1.2.3", "=1.2.4", CONFLICT),
        (">=1, <2", "2.0", CONFLICT),
        ("1.*", "2.*", CONFLICT),
        ("*", "=0.0.1", NO_CONFLICT),
        ("~1", "1.9.9", NO_CONFLICT),  # tilde on a lone major spans the major
        ("^1, ,<1.5", "=1.7", CONFLICT),  # empty clause skipped
        ("", "^9", NO_CONFLICT),
    ],
)
def test_rust_verdicts(left: str, right: str, verdict: str) -> None:
    assert compare_constraints(left, right, "rust") == verdict
    assert compare_constraints(right, left, "rust") == verdict


def test_rust_pre_release_is_not_comparable() -> None:
    assert compare_constraints("1.2.3-alpha", "^1", "rust") == NOT_COMPARABLE
    assert compare_constraints("^1.0.0-beta", "^1", "rust") == NOT_COMPARABLE


# --- npm (javascript) -----------------------------------------------------------


@pytest.mark.parametrize(
    ("left", "right", "verdict"),
    [
        ("^18", "^19", CONFLICT),
        ("~4.17", "^5", CONFLICT),
        ("~4.17 || ^5", "^5.2", NO_CONFLICT),  # OR alternatives
        ("~4.17 || ^5", "^6", CONFLICT),  # every alternative disjoint
        ("1.2.3 - 2.3.4", "2.3.4", NO_CONFLICT),  # hyphen upper is inclusive
        ("1.2 - 2.3", "2.3.9", NO_CONFLICT),  # partial upper spans its prefix
        ("1.2 - 2.3", "2.4.0", CONFLICT),
        ("1.2.x", "1.3.0", CONFLICT),
        ("1.2", "1.3", CONFLICT),  # bare partials read as their prefix range
        ("1.2.3", "1.2.3", NO_CONFLICT),  # bare full versions are exact
        ("1.2.3", "1.2.4", CONFLICT),
        (">=1.2 <2", "^2.1", CONFLICT),  # space-separated AND group
        ("*", "=2.0.0", NO_CONFLICT),
        ("x", "^7", NO_CONFLICT),
        ("v1.2.3", "v1.2.4", CONFLICT),  # npm tolerates the v prefix
        ("^1 ||", "^9", NO_CONFLICT),  # empty OR group means any version
        ("", "^9", NO_CONFLICT),
    ],
)
def test_javascript_verdicts(left: str, right: str, verdict: str) -> None:
    assert compare_constraints(left, right, "javascript") == verdict
    assert compare_constraints(right, left, "javascript") == verdict


@pytest.mark.parametrize(
    "constraint",
    [
        "^1.0.0-beta",  # pre-release
        ">=x",  # operator without a numeric version
        "a - 2.0",  # hyphen range with a non-numeric side
        "1.2.3 - beta",  # ... on the upper side
        "1.2.3 -",  # dangling hyphen token
        "1.x.3",  # wildcard not in trailing position
    ],
)
def test_javascript_unmodelled_forms_never_claim(constraint: str) -> None:
    assert compare_constraints(constraint, "^1", "javascript") == NOT_COMPARABLE


# --- go and unknown ecosystems ----------------------------------------------


def test_go_constraints_are_never_compared() -> None:
    # A go.mod requirement is a minimum that MVS reconciles by taking the
    # maximum; declaration-level disjointness is not a defined notion there.
    assert compare_constraints("v1.2.3", "v9.9.9", "go") == NOT_COMPARABLE


def test_unknown_ecosystem_is_never_compared() -> None:
    assert compare_constraints("1.0", "2.0", "haskell") == NOT_COMPARABLE


# --- interval modelling shapes ------------------------------------------------


def test_empty_constraint_models_as_one_unbounded_interval() -> None:
    for ecosystem in ("python", "rust", "javascript"):
        assert constraint_intervals("  ", ecosystem) == (VersionInterval(),)


def test_python_specifier_set_intersects_to_one_interval() -> None:
    intervals = constraint_intervals(">=12,<16", "python")
    assert intervals == (
        VersionInterval(low=(12,), low_inclusive=True, high=(16,), high_inclusive=False),
    )


def test_javascript_or_groups_model_as_alternatives() -> None:
    intervals = constraint_intervals("~4.17 || ^5", "javascript")
    assert intervals is not None
    assert len(intervals) == 2
    assert intervals[0] == VersionInterval(low=(4, 17), high=(4, 18), high_inclusive=False)
    assert intervals[1] == VersionInterval(low=(5,), high=(6,), high_inclusive=False)


def test_python_compatible_release_interval_shape() -> None:
    intervals = constraint_intervals("~=1.2.3", "python")
    assert intervals == (VersionInterval(low=(1, 2, 3), high=(1, 3), high_inclusive=False),)


def test_weaker_bounds_never_loosen_the_intersection() -> None:
    # A later >=1 must not widen an existing >=2, nor a <=3 an existing <=2.
    intervals = constraint_intervals(">=2,>=1,<=2,<=3", "python")
    assert intervals == (VersionInterval(low=(2,), high=(2,)),)


def test_unmodelled_constraint_returns_none() -> None:
    assert constraint_intervals(">=1.0rc1", "python") is None
    assert constraint_intervals("v1.0.0", "go") is None


class TestUrlPinDivergence:
    def test_same_base_different_hex_revisions_conflict(self) -> None:
        left = "@ git+https://github.com/org/pkg@0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b"
        right = "@ git+https://github.com/org/pkg@f9e8d7c6b5a4938271605f4e3d2c1b0a99887766"
        assert compare_constraints(left, right, "python") == CONFLICT

    def test_same_base_identical_revision_overlaps(self) -> None:
        pin = "@ git+https://github.com/org/pkg@0a1b2c3"
        assert compare_constraints(pin, pin, "python") == NO_CONFLICT

    def test_different_bases_are_not_comparable(self) -> None:
        left = "@ git+https://github.com/org/pkg@0a1b2c3"
        right = "@ git+https://gitlab.com/org/pkg@f9e8d7c"
        assert compare_constraints(left, right, "python") == NOT_COMPARABLE

    def test_branch_or_tag_revisions_never_support_a_claim(self) -> None:
        left = "@ git+https://github.com/org/pkg@main"
        right = "@ git+https://github.com/org/pkg@develop"
        assert compare_constraints(left, right, "python") == NOT_COMPARABLE

    def test_prefix_revisions_could_be_one_commit(self) -> None:
        # a short hash and a longer hash it prefixes may name the same commit
        left = "@ git+https://github.com/org/pkg@0a1b2c3"
        right = "@ git+https://github.com/org/pkg@0a1b2c3d4e5f"
        assert compare_constraints(left, right, "python") == NOT_COMPARABLE

    def test_userinfo_at_sign_stays_in_the_base(self) -> None:
        left = "@ git+ssh://git@github.com/org/pkg@0a1b2c3"
        right = "@ git+ssh://git@github.com/org/pkg@f9e8d7c"
        assert compare_constraints(left, right, "python") == CONFLICT

    def test_fragment_is_stripped_before_the_revision_split(self) -> None:
        left = "@ git+https://github.com/org/pkg@0a1b2c3#egg=pkg"
        right = "@ git+https://github.com/org/pkg@0a1b2c3#subdirectory=sub"
        assert compare_constraints(left, right, "python") == NO_CONFLICT

    def test_url_without_a_revision_is_not_comparable(self) -> None:
        left = "@ https://files.example/pkg-1.0.tar.gz"
        right = "@ git+https://github.com/org/pkg@0a1b2c3"
        assert compare_constraints(left, right, "python") == NOT_COMPARABLE

    def test_url_against_a_version_range_is_not_comparable(self) -> None:
        assert (
            compare_constraints("@ git+https://github.com/org/pkg@0a1b2c3", ">=1,<2", "python")
            == NOT_COMPARABLE
        )
