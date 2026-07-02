# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — turn detected version conflicts into actionable resolution advice
"""Advise how a detected cross-repository version conflict could be resolved.

:mod:`synapse_channel.core.cross_repo_graph` *detects* provable version
conflicts — two repositories declaring disjoint constraints for one package.
Detection names the colliding pair; it does not say which declaration to move.
This module answers that: for every conflicting package it intersects **all**
consumers' declared ranges (the same bounded interval model the detection
uses, so advice and detection never disagree) and reports which single
repository's constraint is the **odd one out** — the declaration whose removal
leaves every other consumer a satisfiable common range, rendered so the
operator can see what the rest already agree on.

Honest scope: a flagged package has at least one provably disjoint pair, so a
range satisfying *every* declaration cannot exist — the advice is about which
declaration to revisit, not a promise that a published version exists in the
remainder range. When the advice names a concrete pin, that version is only
ever lifted from an inclusive bound some remaining consumer already declares
(``==``, ``>=``, ``<=``) and shown with the repository that names it — never
invented, and whether an index actually publishes it is not checked, because
this module reads manifests, not package indexes. Declarations outside the
bounded model are listed as unassessed rather than silently skipped, and the
advice is text for the operator: nothing here rewrites a manifest.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from synapse_channel.core.repo_manifests import (
    discover_repositories,
    read_repo_manifest,
)
from synapse_channel.core.version_constraints import (
    CONFLICT,
    VersionInterval,
    compare_constraints,
    constraint_intervals,
    intersect_intervals,
    interval_is_empty,
)


@dataclass(frozen=True)
class ConsumerConstraint:
    """One repository's declared constraint on the conflicting package.

    Attributes
    ----------
    repo : str
        The consuming repository.
    constraint : str
        The constraint text exactly as its manifest declares it.
    manifest : str
        Repository-relative manifest path that declared it.
    """

    repo: str
    constraint: str
    manifest: str


@dataclass(frozen=True)
class OddOneOut:
    """A single declaration whose removal reconciles every other consumer.

    Attributes
    ----------
    repo : str
        The repository whose declaration is the outlier.
    constraint : str
        The outlier constraint text.
    remainder : str
        The rendered version range every *other* comparable consumer
        accepts once this declaration is set aside.
    suggested_pin : str or None
        A concrete version inside the remainder that some remaining
        consumer already names in an inclusive bound — evidence-based,
        never invented; ``None`` when no declared version falls inside
        the remainder. Whether an index publishes it is not checked.
    pin_source : str or None
        The repository whose declaration names the suggested version.
    """

    repo: str
    constraint: str
    remainder: str
    suggested_pin: str | None = None
    pin_source: str | None = None


@dataclass(frozen=True)
class ResolutionAdvice:
    """The resolution picture for one provably conflicting package.

    Attributes
    ----------
    package : str
        The conflicting package name.
    ecosystem : str
        The ecosystem all listed declarations share.
    consumers : tuple[ConsumerConstraint, ...]
        Every comparable declaration, repository order.
    unassessed : tuple[ConsumerConstraint, ...]
        Declarations outside the bounded interval model — listed, never
        silently skipped, and excluded from the intersections.
    odd_ones_out : tuple[OddOneOut, ...]
        Declarations whose single removal leaves the rest a common range.
        Empty when no single declaration is the outlier — the constraints
        split into mutually disjoint camps.
    """

    package: str
    ecosystem: str
    consumers: tuple[ConsumerConstraint, ...]
    unassessed: tuple[ConsumerConstraint, ...]
    odd_ones_out: tuple[OddOneOut, ...]


def advise_package(
    package: str,
    ecosystem: str,
    declarations: tuple[ConsumerConstraint, ...],
) -> ResolutionAdvice:
    """Compute the odd-one-out advice for one conflicting package.

    Parameters
    ----------
    package : str
        The package name the declarations constrain.
    ecosystem : str
        The declarations' shared ecosystem.
    declarations : tuple[ConsumerConstraint, ...]
        Every scanned consumer's declaration for the package.

    Returns
    -------
    ResolutionAdvice
        Comparable consumers, unassessed declarations, and every single
        declaration whose removal reconciles the rest.
    """
    comparable: list[tuple[ConsumerConstraint, tuple[VersionInterval, ...]]] = []
    unassessed: list[ConsumerConstraint] = []
    for declaration in declarations:
        intervals = constraint_intervals(declaration.constraint, ecosystem)
        if intervals is None:
            unassessed.append(declaration)
        else:
            comparable.append((declaration, intervals))
    odd_ones_out: list[OddOneOut] = []
    for index, (declaration, _intervals) in enumerate(comparable):
        others = [item for position, item in enumerate(comparable) if position != index]
        if not others:
            continue
        remainder = _intersect_all([intervals for _, intervals in others])
        if remainder:
            pin, source = _suggest_pin(others, remainder)
            odd_ones_out.append(
                OddOneOut(
                    repo=declaration.repo,
                    constraint=declaration.constraint,
                    remainder=render_intervals(remainder),
                    suggested_pin=pin,
                    pin_source=source,
                )
            )
    return ResolutionAdvice(
        package=package,
        ecosystem=ecosystem,
        consumers=tuple(declaration for declaration, _ in comparable),
        unassessed=tuple(unassessed),
        odd_ones_out=tuple(odd_ones_out),
    )


def run_resolution_advice(root: str | Path) -> tuple[ResolutionAdvice, ...]:
    """Scan a repository root and advise on every provably conflicting package.

    Parameters
    ----------
    root : str or pathlib.Path
        Directory whose child repositories are scanned, exactly as
        ``synapse cross-repo`` scans them.

    Returns
    -------
    tuple[ResolutionAdvice, ...]
        One advice per package with at least one provably disjoint
        declaration pair, sorted by ``(ecosystem, package)``.

    Raises
    ------
    ValueError
        If ``root`` is not an existing directory.
    """
    path = Path(root)
    if not path.is_dir():
        msg = f"missing repository root: {path}"
        raise ValueError(msg)
    consumers: dict[tuple[str, str], list[ConsumerConstraint]] = {}
    for repo_dir in discover_repositories(path):
        manifest = read_repo_manifest(repo_dir)
        for dependency in manifest.dependencies:
            consumers.setdefault((dependency.ecosystem, dependency.name), []).append(
                ConsumerConstraint(
                    repo=manifest.repo,
                    constraint=dependency.constraint,
                    manifest=dependency.manifest,
                )
            )
    advice: list[ResolutionAdvice] = []
    for (ecosystem, package), declarations in sorted(consumers.items()):
        if len(declarations) < 2:
            continue
        if not _has_conflict(declarations, ecosystem):
            continue
        advice.append(advise_package(package, ecosystem, tuple(declarations)))
    return tuple(advice)


def resolution_to_json(advice: tuple[ResolutionAdvice, ...]) -> list[dict[str, object]]:
    """Return a stable JSON-compatible representation of the advice list."""
    return [
        {
            "package": item.package,
            "ecosystem": item.ecosystem,
            "consumers": [
                {
                    "repo": consumer.repo,
                    "constraint": consumer.constraint,
                    "manifest": consumer.manifest,
                }
                for consumer in item.consumers
            ],
            "unassessed": [
                {
                    "repo": consumer.repo,
                    "constraint": consumer.constraint,
                    "manifest": consumer.manifest,
                }
                for consumer in item.unassessed
            ],
            "odd_ones_out": [
                {
                    "repo": odd.repo,
                    "constraint": odd.constraint,
                    "remainder": odd.remainder,
                    "suggested_pin": odd.suggested_pin,
                    "pin_source": odd.pin_source,
                }
                for odd in item.odd_ones_out
            ],
            "note": "advisory text; nothing rewrites a manifest",
        }
        for item in advice
    ]


def render_resolution_markdown(advice: tuple[ResolutionAdvice, ...]) -> str:
    """Render the advice list as compact Markdown."""
    if not advice:
        return "## Suggested resolutions\n\n- no provable version conflicts"
    lines = [f"## Suggested resolutions ({len(advice)} conflicting package(s))"]
    for item in advice:
        lines.append("")
        lines.append(f"### {item.ecosystem} {item.package}")
        for consumer in item.consumers:
            constraint = consumer.constraint or "(any version)"
            lines.append(f"- {consumer.repo} declares '{constraint}' ({consumer.manifest})")
        for consumer in item.unassessed:
            lines.append(
                f"- {consumer.repo} declares '{consumer.constraint}' "
                f"({consumer.manifest}) — outside the bounded model, not assessed"
            )
        if item.odd_ones_out:
            for odd in item.odd_ones_out:
                suggestion = ""
                if odd.suggested_pin is not None:
                    suggestion = (
                        f"; {odd.suggested_pin} would satisfy them all "
                        f"(a version {odd.pin_source} already declares)"
                    )
                lines.append(
                    f"- ODD ONE OUT: {odd.repo} ('{odd.constraint}') — "
                    f"the other declarations reconcile at {odd.remainder}{suggestion}"
                )
        else:
            lines.append(
                "- no single declaration is the odd one out; the constraints "
                "split into mutually disjoint camps"
            )
    return "\n".join(lines)


def render_interval(interval: VersionInterval) -> str:
    """Render one interval as operator text (``>=1.2, <2.0``)."""
    if interval.low is None and interval.high is None:
        return "any version"
    low_text = _dotted(interval.low)
    high_text = _dotted(interval.high)
    if (
        interval.low is not None
        and interval.high is not None
        and interval.low == interval.high
        and interval.low_inclusive
        and interval.high_inclusive
    ):
        return f"=={low_text}"
    parts: list[str] = []
    if interval.low is not None:
        parts.append(f"{'>=' if interval.low_inclusive else '>'}{low_text}")
    if interval.high is not None:
        parts.append(f"{'<=' if interval.high_inclusive else '<'}{high_text}")
    return ", ".join(parts)


def render_intervals(intervals: tuple[VersionInterval, ...]) -> str:
    """Render OR-alternative intervals, joined explicitly."""
    return " or ".join(render_interval(interval) for interval in intervals)


def _dotted(version: tuple[int, ...] | None) -> str:
    """Render a numeric version tuple in dotted form (empty for ``None``)."""
    return "" if version is None else ".".join(str(part) for part in version)


def _suggest_pin(
    remaining: list[tuple[ConsumerConstraint, tuple[VersionInterval, ...]]],
    remainder: tuple[VersionInterval, ...],
) -> tuple[str | None, str | None]:
    """Pick a declared version inside the remainder, or ``(None, None)``.

    Candidates are only the versions the remaining consumers already name
    in inclusive bounds — a version an author wrote into a manifest as
    acceptable — so the suggestion is lifted from evidence, never
    invented. Exclusive bounds (``<2.0``) are fence-posts that need not
    exist and are never candidates. The highest candidate wins; ties on
    the padded version fall to the lexicographically first repository.
    """
    candidates = [
        (version, declaration.repo)
        for declaration, intervals in remaining
        for interval in intervals
        for version, inclusive in (
            (interval.low, interval.low_inclusive),
            (interval.high, interval.high_inclusive),
        )
        if version is not None and inclusive and _version_in_intervals(version, remainder)
    ]
    if not candidates:
        return None, None
    width = max(len(version) for version, _ in candidates)
    best_version, best_repo = min(
        candidates,
        key=lambda item: (
            tuple(-part for part in item[0] + (0,) * (width - len(item[0]))),
            item[1],
        ),
    )
    return _dotted(best_version), best_repo


def _version_in_intervals(version: tuple[int, ...], intervals: tuple[VersionInterval, ...]) -> bool:
    """Return whether the exact version lies inside any OR-alternative interval.

    Containment is the point interval ``[version, version]`` intersected
    with each alternative — the same interval arithmetic the detection
    uses, so the suggestion can never disagree with it.
    """
    point = VersionInterval(low=version, low_inclusive=True, high=version, high_inclusive=True)
    return any(
        not interval_is_empty(intersect_intervals(point, interval)) for interval in intervals
    )


def _intersect_all(
    interval_sets: list[tuple[VersionInterval, ...]],
) -> tuple[VersionInterval, ...]:
    """Intersect OR-alternative interval sets across every declaration.

    Each declaration allows a union of intervals, so the intersection of the
    declarations is the union of all cross-product pairwise intersections —
    empty results dropped. An empty return means no version satisfies every
    declaration in the set.
    """
    accumulated: list[VersionInterval] = list(interval_sets[0])
    for intervals in interval_sets[1:]:
        accumulated = [
            candidate
            for left in accumulated
            for right in intervals
            if not interval_is_empty(candidate := intersect_intervals(left, right))
        ]
        if not accumulated:
            return ()
    return tuple(accumulated)


def _has_conflict(declarations: list[ConsumerConstraint], ecosystem: str) -> bool:
    """Return whether any declaration pair is provably disjoint."""
    for index, left in enumerate(declarations):
        for right in declarations[index + 1 :]:
            if compare_constraints(left.constraint, right.constraint, ecosystem) == CONFLICT:
                return True
    return False
