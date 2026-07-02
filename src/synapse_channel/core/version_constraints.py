# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — declaration-level version-constraint comparison
"""Decide whether two declared version constraints are provably disjoint.

This is the comparison half of the cross-repository dependency conflict
signal: given the constraint text two repositories declare for the same
package — a PEP 440 specifier set (``>=12,<16``), a Cargo requirement
(``^1.0``), or an npm semver range (``~4.17 || ^5``) — it answers one bounded
question: *can no version satisfy both?* The answer is one of three verdicts:

- :data:`CONFLICT` — the constraints are **provably** disjoint; no release
  version can satisfy both declarations.
- :data:`NO_CONFLICT` — the declared ranges provably overlap. This says the
  declarations are reconcilable, not that a common version is published.
- :data:`NOT_COMPARABLE` — at least one side uses syntax outside the bounded
  model below, so nothing is claimed.

The model is deliberately conservative so a conflict claim is always
defensible: only plain numeric dot-release versions compare (``1.2.3``);
pre-release, post-release, dev, epoch, local, and build parts, direct URL
references, and unrecognised operators all yield :data:`NOT_COMPARABLE`.
PEP 440 exclusions (``!=``) are ignored — dropping an exclusion can only
widen a range, so ignoring them can only suppress a conflict claim, never
invent one. Go constraints are never compared: a ``go.mod`` requirement is a
*minimum* that minimal version selection reconciles by taking the maximum,
and a different major version is a different module path (name), so
declaration-level disjointness is not a defined notion there.

Everything stays declaration-level satisfiability — this is not a resolver:
no lockfiles, no transitive closure, no knowledge of which versions exist.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

CONFLICT = "conflict"
"""Verdict: the two constraints are provably disjoint."""

NO_CONFLICT = "no_conflict"
"""Verdict: the declared ranges provably overlap."""

NOT_COMPARABLE = "not_comparable"
"""Verdict: at least one constraint is outside the bounded numeric model."""

_RELEASE_RE = re.compile(r"^\d+(\.\d+)*$")
_PYTHON_CLAUSE_RE = re.compile(r"^(===|==|!=|~=|>=|<=|>|<)\s*(.+)$")
_RANGE_OPERATOR_RE = re.compile(r"^(>=|<=|>|<|\^|~|=)\s*(.+)$")


@dataclass(frozen=True)
class VersionInterval:
    """One contiguous version interval with optional open ends.

    Attributes
    ----------
    low, high : tuple[int, ...] or None
        Numeric bound; ``None`` means unbounded on that side.
    low_inclusive, high_inclusive : bool
        Whether the bound itself satisfies the interval.
    """

    low: tuple[int, ...] | None = None
    low_inclusive: bool = True
    high: tuple[int, ...] | None = None
    high_inclusive: bool = True


_UNBOUNDED = VersionInterval()


def _parse_version(text: str) -> tuple[int, ...] | None:
    """Parse a plain numeric release version, or ``None`` when it is not one."""
    candidate = text.strip()
    if candidate[:1] in ("v", "V"):
        candidate = candidate[1:]
    if not _RELEASE_RE.match(candidate):
        return None
    return tuple(int(part) for part in candidate.split("."))


def _compare(left: tuple[int, ...], right: tuple[int, ...]) -> int:
    """Compare two release versions with zero padding (``1.2`` == ``1.2.0``)."""
    width = max(len(left), len(right))
    padded_left = left + (0,) * (width - len(left))
    padded_right = right + (0,) * (width - len(right))
    if padded_left < padded_right:
        return -1
    if padded_left > padded_right:
        return 1
    return 0


def _bump(version: tuple[int, ...], index: int) -> tuple[int, ...]:
    """Return the next version boundary: segment ``index`` incremented, rest dropped."""
    return version[:index] + (version[index] + 1,)


def _caret_interval(version: tuple[int, ...]) -> VersionInterval:
    """Return the caret range: the left-most non-zero segment may not change."""
    for index, segment in enumerate(version):
        if segment:
            return VersionInterval(low=version, high=_bump(version, index), high_inclusive=False)
    return VersionInterval(low=version, high=_bump(version, len(version) - 1), high_inclusive=False)


def _tilde_interval(version: tuple[int, ...]) -> VersionInterval:
    """Return the tilde range: patch-level changes only (minor when no minor given)."""
    index = min(1, len(version) - 1)
    return VersionInterval(low=version, high=_bump(version, index), high_inclusive=False)


def _wildcard_base(text: str) -> tuple[int, ...] | None:
    """Parse a trailing-wildcard version (``1.2.*``/``1.2.x``) into its base.

    Returns the numeric base segments, ``()`` for a bare wildcard matching
    everything, or ``None`` when the text is not a trailing-wildcard form.
    """
    parts = text.strip().split(".")
    while parts and parts[-1] in ("*", "x", "X"):
        parts.pop()
    if not parts:
        return ()
    base = ".".join(parts)
    return _parse_version(base)


def _wildcard_interval(base: tuple[int, ...]) -> VersionInterval:
    """Return the interval a trailing-wildcard base spans."""
    if not base:
        return _UNBOUNDED
    return VersionInterval(low=base, high=_bump(base, len(base) - 1), high_inclusive=False)


def _intersect(left: VersionInterval, right: VersionInterval) -> VersionInterval:
    """Return the tightest interval both ``left`` and ``right`` allow."""
    low, low_inclusive = left.low, left.low_inclusive
    if right.low is not None:
        if low is None or _compare(right.low, low) > 0:
            low, low_inclusive = right.low, right.low_inclusive
        elif _compare(right.low, low) == 0:
            low_inclusive = low_inclusive and right.low_inclusive
    high, high_inclusive = left.high, left.high_inclusive
    if right.high is not None:
        if high is None or _compare(right.high, high) < 0:
            high, high_inclusive = right.high, right.high_inclusive
        elif _compare(right.high, high) == 0:
            high_inclusive = high_inclusive and right.high_inclusive
    return VersionInterval(
        low=low, low_inclusive=low_inclusive, high=high, high_inclusive=high_inclusive
    )


def _is_empty(interval: VersionInterval) -> bool:
    """Return whether no version can satisfy ``interval``."""
    if interval.low is None or interval.high is None:
        return False
    order = _compare(interval.low, interval.high)
    if order > 0:
        return True
    return order == 0 and not (interval.low_inclusive and interval.high_inclusive)


def _disjoint(left: VersionInterval, right: VersionInterval) -> bool:
    """Return whether ``left`` and ``right`` provably share no version."""
    if _is_empty(left) or _is_empty(right):
        return True
    for lower, upper in ((left, right), (right, left)):
        if lower.high is None or upper.low is None:
            continue
        order = _compare(lower.high, upper.low)
        if order < 0:
            return True
        if order == 0 and not (lower.high_inclusive and upper.low_inclusive):
            return True
    return False


def _python_clause_interval(operator: str, version_text: str) -> VersionInterval | None:
    """Return the interval one PEP 440 clause allows, or ``None`` if unmodelled."""
    text = version_text.strip()
    if operator in ("==", "===") and text.endswith(".*"):
        base = _parse_version(text[:-2])
        if base is None:
            return None
        return _wildcard_interval(base)
    version = _parse_version(text)
    if version is None:
        return None
    if operator in ("==", "==="):
        return VersionInterval(low=version, high=version)
    if operator == "~=":
        if len(version) < 2:
            return None
        return VersionInterval(
            low=version, high=_bump(version, len(version) - 2), high_inclusive=False
        )
    return _range_interval(operator, version)


def _range_interval(operator: str, version: tuple[int, ...]) -> VersionInterval:
    """Return the half-bounded interval of one ``>=``/``>``/``<=``/``<`` clause."""
    if operator == ">=":
        return VersionInterval(low=version)
    if operator == ">":
        return VersionInterval(low=version, low_inclusive=False)
    if operator == "<=":
        return VersionInterval(high=version)
    return VersionInterval(high=version, high_inclusive=False)


def _python_intervals(constraint: str) -> tuple[VersionInterval, ...] | None:
    """Model a PEP 440 specifier set as a single AND-intersected interval."""
    interval = _UNBOUNDED
    for raw_clause in constraint.split(","):
        clause = raw_clause.strip()
        if not clause:
            continue
        match = _PYTHON_CLAUSE_RE.match(clause)
        if match is None:
            return None
        operator, version_text = match.groups()
        if operator == "!=":
            # An exclusion only removes points; dropping it widens the range,
            # which can only suppress a conflict claim, never invent one.
            continue
        clause_interval = _python_clause_interval(operator, version_text)
        if clause_interval is None:
            return None
        interval = _intersect(interval, clause_interval)
    return (interval,)


def _requirement_clause_interval(clause: str, *, bare_is_caret: bool) -> VersionInterval | None:
    """Return the interval one Cargo/npm clause allows, or ``None`` if unmodelled.

    ``bare_is_caret`` selects the ecosystem's bare-version reading: Cargo
    treats ``1.2.3`` as ``^1.2.3``; npm treats a full bare version as exact
    and a partial one (``1.2``) as its trailing-wildcard range.
    """
    match = _RANGE_OPERATOR_RE.match(clause)
    if match is not None:
        operator, version_text = match.groups()
        version = _parse_version(version_text)
        if version is None:
            return None
        if operator == "^":
            return _caret_interval(version)
        if operator == "~":
            return _tilde_interval(version)
        if operator == "=":
            return VersionInterval(low=version, high=version)
        return _range_interval(operator, version)
    wildcard = _wildcard_base(clause)
    if wildcard is not None and _parse_version(clause) is None:
        return _wildcard_interval(wildcard)
    version = _parse_version(clause)
    if version is None:
        return None
    if bare_is_caret:
        return _caret_interval(version)
    if len(version) >= 3:
        return VersionInterval(low=version, high=version)
    return _wildcard_interval(version)


def _rust_intervals(constraint: str) -> tuple[VersionInterval, ...] | None:
    """Model a Cargo requirement (comma-separated AND clauses)."""
    interval = _UNBOUNDED
    for raw_clause in constraint.split(","):
        clause = raw_clause.strip()
        if not clause:
            continue
        clause_interval = _requirement_clause_interval(clause, bare_is_caret=True)
        if clause_interval is None:
            return None
        interval = _intersect(interval, clause_interval)
    return (interval,)


def _javascript_group_interval(group: str) -> VersionInterval | None:
    """Model one npm AND-group (space-separated clauses, hyphen ranges)."""
    tokens = group.split()
    interval = _UNBOUNDED
    index = 0
    while index < len(tokens):
        if index + 2 < len(tokens) and tokens[index + 1] == "-":
            low = _parse_version(tokens[index])
            high_text = tokens[index + 2]
            high = _parse_version(high_text)
            if low is None or high is None:
                return None
            if len(high) >= 3:
                clause_interval = VersionInterval(low=low, high=high)
            else:
                # A partial upper bound spans its whole prefix: 1.2 - 2.3
                # means >=1.2 and <2.4 in npm's hyphen-range reading.
                clause_interval = VersionInterval(
                    low=low, high=_bump(high, len(high) - 1), high_inclusive=False
                )
            index += 3
        else:
            clause_interval_or_none = _requirement_clause_interval(
                tokens[index], bare_is_caret=False
            )
            if clause_interval_or_none is None:
                return None
            clause_interval = clause_interval_or_none
            index += 1
        interval = _intersect(interval, clause_interval)
    return interval


def _javascript_intervals(constraint: str) -> tuple[VersionInterval, ...] | None:
    """Model an npm range: ``||``-separated OR groups of AND clauses."""
    intervals: list[VersionInterval] = []
    for group in constraint.split("||"):
        stripped = group.strip()
        if not stripped:
            intervals.append(_UNBOUNDED)
            continue
        interval = _javascript_group_interval(stripped)
        if interval is None:
            return None
        intervals.append(interval)
    return tuple(intervals)


def constraint_intervals(constraint: str, ecosystem: str) -> tuple[VersionInterval, ...] | None:
    """Model one declared constraint as OR-alternative version intervals.

    Parameters
    ----------
    constraint : str
        The constraint text exactly as the manifest declares it. An empty
        string means "any version" and models as one unbounded interval.
    ecosystem : str
        ``python``, ``rust``, or ``javascript``. ``go`` and unknown
        ecosystems return ``None`` — a ``go.mod`` requirement is a minimum
        that minimal version selection reconciles, never a range to
        intersect.

    Returns
    -------
    tuple[VersionInterval, ...] or None
        The alternatives a matching version may fall in, or ``None`` when
        the constraint (or the ecosystem) is outside the bounded model.
    """
    text = constraint.strip()
    if ecosystem == "python":
        return (_UNBOUNDED,) if not text else _python_intervals(text)
    if ecosystem == "rust":
        return (_UNBOUNDED,) if not text else _rust_intervals(text)
    if ecosystem == "javascript":
        return (_UNBOUNDED,) if not text else _javascript_intervals(text)
    return None


def compare_constraints(left: str, right: str, ecosystem: str) -> str:
    """Return the verdict for two declared constraints on the same package.

    Parameters
    ----------
    left, right : str
        Constraint texts as two manifests declare them.
    ecosystem : str
        The shared ecosystem of both declarations.

    Returns
    -------
    str
        :data:`CONFLICT` when provably disjoint, :data:`NO_CONFLICT` when
        the modelled ranges overlap, :data:`NOT_COMPARABLE` when either side
        is outside the bounded model.
    """
    left_intervals = constraint_intervals(left, ecosystem)
    right_intervals = constraint_intervals(right, ecosystem)
    if left_intervals is None or right_intervals is None:
        return NOT_COMPARABLE
    for left_interval in left_intervals:
        for right_interval in right_intervals:
            if not _disjoint(left_interval, right_interval):
                return NO_CONFLICT
    return CONFLICT
