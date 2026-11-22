# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — structure budget: recorded ceilings that may only fall
"""Hold the indicators of a second responsibility to a ledger that may only fall.

``tools/structure_measures.py`` counts, for every class, function and module,
what points at more than one responsibility. This checker compares those
figures with ``tools/structure_budget.toml``:

* a unit above its threshold must be listed with its exact figure;
* a listed figure may fall and must then be lowered in the ledger, so slack is
  never kept;
* against the ledger of a baseline revision, no entry may appear and no figure
  may rise, except as a recorded move of a listed unit or as an exception with
  a reason, an owner and a review date.

The check fails closed: a missing ledger, a source that cannot be parsed, an
unknown revision or a failing ``git`` end with a non-zero status and the reason.

Run ``python -m tools.check_structure_budget --check --baseline HEAD`` from the
repository root before a commit, ``--update --baseline HEAD`` after shortening
a listed unit, and ``--report --baseline HEAD`` for the before and after
figures of a change. CI judges a pushed range commit by commit with
``--check --walk-from BEFORE --to SHA`` (:mod:`tools.structure_budget_walk`).
"""

from __future__ import annotations

import argparse
import datetime
import json
import subprocess
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from tools.structure_measures import MEASURES, Figures, measure_roots
from tools.structure_revision import measure_revision

__all__ = ["Ledger", "LedgerError", "Violation", "check_baseline", "check_tree", "main"]

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LEDGER = "tools/structure_budget.toml"
SCHEMA = 3
_INITIALISATION_SCHEMA = 2
_INITIALISATION_MEASURE = "initialisation_attributes"
_INITIALISATION_THRESHOLD = 20
CONFIGURATION = "configuration"
_EXCEPTION_FIELDS = ("unit", "measure", "allowed", "reason", "owner", "review_by")
_HEADER = (
    "# SPDX-License-" + "Identifier: AGPL-3.0-or-later",
    "# Commercial license available",
    "# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.",
    "# © Code 2020–2026 Miroslav Šotek. All rights reserved.",
    "# ORCID: 0009-0009-3560-0851",
    "# Contact: www.anulum.li | protoscience@anulum.li",
    "# SYNAPSE CHANNEL — structure budget ledger",
)


class LedgerError(ValueError):
    """The ledger is missing, unreadable or does not follow its schema."""


@dataclass(frozen=True)
class Violation:
    """One broken rule.

    Attributes
    ----------
    code : str
        Stable reason code, for example ``above_ceiling``.
    measure : str
        Measure the rule was applied to, or ``configuration``.
    unit : str
        Unit key, threshold name or allowed private name.
    detail : str
        The figures involved, for a person.
    """

    code: str
    measure: str
    unit: str
    detail: str

    def render(self) -> str:
        """Return the one-line report of this violation."""
        return f"{self.code}: {self.measure} {self.unit}: {self.detail}"


@dataclass(frozen=True)
class Ledger:
    """The parsed structure budget.

    Attributes
    ----------
    roots : tuple[str, ...]
        Repository-relative directories that are measured.
    thresholds : dict[str, int]
        Figure above which a unit must be listed, per measure.
    allowed_private : frozenset[str]
        Global ``name._attr`` spellings not counted as private access.
    ceilings : dict[str, dict[str, int]]
        Per measure, the listed units with their recorded figure.
    moves : tuple[dict[str, str], ...]
        Listed units that changed their key in this commit: ``from``, ``to``, ``measure``.
    exceptions : tuple[dict[str, Any], ...]
        Units accepted above their threshold outside the ceilings, each with
        ``allowed``, ``reason``, ``owner`` and ``review_by``.
    allowed_private_by_module : dict[str, frozenset[str]]
        Module-scoped ``name._attr`` spellings allowed only in the named module path.
    """

    roots: tuple[str, ...]
    thresholds: dict[str, int]
    allowed_private: frozenset[str]
    ceilings: dict[str, dict[str, int]]
    moves: tuple[dict[str, str], ...] = ()
    exceptions: tuple[dict[str, Any], ...] = field(default=())
    schema: int = SCHEMA
    allowed_private_by_module: dict[str, frozenset[str]] = field(default_factory=dict)

    def excepted(self, measure: str, unit: str) -> dict[str, Any] | None:
        """Return the exception recorded for a unit under a measure, if any."""
        for entry in self.exceptions:
            if entry["measure"] == measure and entry["unit"] == unit:
                return entry
        return None

    def allows_private_unit(self, unit: str) -> bool:
        """Return whether ``unit`` names a global or module-scoped private allowance."""
        if unit in self.allowed_private:
            return True
        if "::" not in unit:
            return False
        module_path, name = unit.split("::", 1)
        return name in self.allowed_private_by_module.get(module_path, frozenset())


def _integer_table(table: object, name: str) -> dict[str, int]:
    if not isinstance(table, dict):
        raise LedgerError(f"ledger_malformed: {name} must be a table")
    for key, value in table.items():
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise LedgerError(f"ledger_malformed: {name}.{key} must be a non-negative integer")
    return dict(table)


def _exception_entries(raw: object) -> tuple[dict[str, Any], ...]:
    if not isinstance(raw, list):
        raise LedgerError("ledger_malformed: exceptions must be an array of tables")
    for entry in raw:
        missing = [name for name in _EXCEPTION_FIELDS if not entry.get(name) and name != "allowed"]
        allowed = entry.get("allowed")
        if missing or isinstance(allowed, bool) or not isinstance(allowed, int):
            raise LedgerError(
                "exception_incomplete: every exception needs unit, measure, an integer allowed, "
                f"reason, owner and review_by ({entry.get('unit', '?')})"
            )
        if entry["measure"] != CONFIGURATION and entry["measure"] not in MEASURES:
            raise LedgerError(
                f"ledger_malformed: exception of {entry['unit']} names an unknown measure"
            )
        try:
            datetime.date.fromisoformat(str(entry["review_by"]))
        except ValueError as error:
            raise LedgerError(
                f"exception_incomplete: review_by of {entry['unit']} is not an ISO date"
            ) from error
    return tuple(dict(entry) for entry in raw)


def _schema(data: dict[str, Any]) -> int:
    """Accept only the retained legacy schemas and the current schema."""
    schema = data.get("schema")
    if (
        not isinstance(schema, int)
        or isinstance(schema, bool)
        or schema not in (1, _INITIALISATION_SCHEMA, SCHEMA)
    ):
        raise LedgerError(
            f"ledger_malformed: schema must be 1, {_INITIALISATION_SCHEMA} or {SCHEMA}"
        )
    return schema


def _allowed_private_modules(raw: object, schema: int) -> dict[str, frozenset[str]]:
    """Parse and validate module-scoped private-access allowances."""
    if raw is None:
        return {}
    if schema < SCHEMA:
        raise LedgerError(
            f"ledger_malformed: module-scoped private allowances require schema {SCHEMA}"
        )
    if not isinstance(raw, dict):
        raise LedgerError("ledger_malformed: allowed_private_access.modules must be a table")
    parsed: dict[str, frozenset[str]] = {}
    for module_path, entries in raw.items():
        if not isinstance(module_path, str) or not module_path.strip():
            raise LedgerError(
                "ledger_malformed: allowed_private_access.modules keys must be non-empty paths"
            )
        if (
            not isinstance(entries, list)
            or not entries
            or not all(isinstance(name, str) and name.strip() for name in entries)
        ):
            raise LedgerError(
                f"ledger_malformed: allowed_private_access.modules.{module_path} "
                "must be a non-empty array of names"
            )
        parsed[module_path] = frozenset(entries)
    return parsed


def _allowed_private(
    data: dict[str, Any], schema: int
) -> tuple[frozenset[str], dict[str, frozenset[str]]]:
    """Parse global and module-scoped private-access allowances."""
    raw = data.get("allowed_private_access", {})
    if not isinstance(raw, dict):
        raise LedgerError("ledger_malformed: allowed_private_access must be a table")
    unknown = set(raw) - {"names", "modules"}
    if unknown:
        raise LedgerError(f"ledger_malformed: unknown allowed_private_access key {sorted(unknown)}")
    names = raw.get("names", [])
    if not isinstance(names, list) or not all(isinstance(n, str) and n.strip() for n in names):
        raise LedgerError(
            "ledger_malformed: allowed_private_access.names must be an array of names"
        )
    modules = _allowed_private_modules(raw.get("modules"), schema)
    return frozenset(names), modules


def _thresholds(data: dict[str, Any], schema: int) -> dict[str, int]:
    """Supply the new indicator only when reading the exact legacy format."""
    thresholds = _integer_table(data.get("thresholds"), "thresholds")
    if schema == 1:
        if _INITIALISATION_MEASURE in thresholds or _INITIALISATION_MEASURE in data.get(
            "ceilings", {}
        ):
            raise LedgerError("ledger_malformed: initialisation measure requires schema 2")
        thresholds[_INITIALISATION_MEASURE] = _INITIALISATION_THRESHOLD
    return thresholds


def parse_ledger(text: str) -> Ledger:
    """Parse and validate the text of a structure budget.

    Parameters
    ----------
    text : str
        Content of ``structure_budget.toml``.

    Returns
    -------
    Ledger
        The validated ledger.

    Raises
    ------
    LedgerError
        When the text is not TOML, has another schema, names an unknown
        measure, holds a non-integer figure, or has an incomplete exception.
    """
    try:
        data = tomllib.loads(text)
    except ValueError as error:
        raise LedgerError(f"ledger_malformed: {error}") from error
    schema = _schema(data)
    roots = data.get("roots")
    if not isinstance(roots, list) or not roots or not all(isinstance(r, str) for r in roots):
        raise LedgerError("ledger_malformed: roots must be a non-empty array of paths")
    thresholds = _thresholds(data, schema)
    ceilings_raw = data.get("ceilings", {})
    unknown = (set(thresholds) ^ set(MEASURES)) | (set(ceilings_raw) - set(MEASURES))
    if unknown:
        raise LedgerError(f"ledger_malformed: unknown or missing measure {sorted(unknown)}")
    moves = data.get("moves", [])
    if not isinstance(moves, list) or not all(
        isinstance(move, dict) and {"from", "to", "measure"} <= set(move) for move in moves
    ):
        raise LedgerError("ledger_malformed: every move needs from, to and measure")
    allowed_private, allowed_private_by_module = _allowed_private(data, schema)
    return Ledger(
        roots=tuple(roots),
        thresholds=thresholds,
        allowed_private=allowed_private,
        ceilings={m: _integer_table(ceilings_raw.get(m, {}), f"ceilings.{m}") for m in MEASURES},
        moves=tuple(dict(move) for move in moves),
        exceptions=_exception_entries(data.get("exceptions", [])),
        schema=schema,
        allowed_private_by_module=allowed_private_by_module,
    )


def render_ledger(ledger: Ledger) -> str:
    """Return the canonical text of a ledger: fixed order, sorted keys."""
    lines = [
        *_HEADER,
        "# Structure budget: recorded figures of units above their threshold.",
        "# Written by `python -m tools.check_structure_budget --update`. A figure may only fall.",
        f"schema = {SCHEMA}",
        f"roots = {json.dumps(list(ledger.roots))}",
        "",
        "[thresholds]",
        *(f"{measure} = {ledger.thresholds[measure]}" for measure in MEASURES),
        "",
        "[allowed_private_access]",
        f"names = {json.dumps(sorted(ledger.allowed_private))}",
    ]
    if ledger.allowed_private_by_module:
        lines += ["", "[allowed_private_access.modules]"]
        lines += [
            f"{json.dumps(module_path)} = {json.dumps(sorted(names))}"
            for module_path, names in sorted(ledger.allowed_private_by_module.items())
        ]
    for measure in MEASURES:
        lines += ["", f"[ceilings.{measure}]"]
        lines += [
            f"{json.dumps(unit)} = {figure}"
            for unit, figure in sorted(ledger.ceilings[measure].items())
        ]
    for move in ledger.moves:
        lines += ["", "[[moves]]", *(f"{key} = {json.dumps(move[key])}" for key in sorted(move))]
    for entry in ledger.exceptions:
        lines += ["", "[[exceptions]]"]
        lines += [f"{key} = {json.dumps(entry[key])}" for key in _EXCEPTION_FIELDS]
    return "\n".join(lines) + "\n"


def _check_unit(
    ledger: Ledger, measure: str, unit: str, figure: int, today: datetime.date
) -> Violation | None:
    """Judge one measured unit that is above its threshold."""
    entry = ledger.excepted(measure, unit)
    if entry is not None:
        if datetime.date.fromisoformat(str(entry["review_by"])) < today:
            return Violation("exception_expired", measure, unit, f"review_by {entry['review_by']}")
        if figure > entry["allowed"]:
            detail = f"{figure} is above the excepted {entry['allowed']}"
            return Violation("above_ceiling", measure, unit, detail)
        return None
    ceiling = ledger.ceilings[measure].get(unit)
    threshold = ledger.thresholds[measure]
    if ceiling is None:
        detail = f"{figure} is above the threshold {threshold} and not listed"
        return Violation("over_threshold_unlisted", measure, unit, detail)
    if figure > ceiling:
        return Violation("above_ceiling", measure, unit, f"{figure} is above its ceiling {ceiling}")
    if figure < ceiling:
        detail = f"{figure} is below its ceiling {ceiling}; lower the ledger with --update"
        return Violation("stale_ceiling", measure, unit, detail)
    return None


def _check_allowed_private_modules(
    ledger: Ledger,
    figures: Figures,
    observed_private: Mapping[str, frozenset[str]] | None,
) -> list[Violation]:
    """Refuse module-scoped private allowances whose path is missing or name is unused."""
    found: list[Violation] = []
    measured_modules = figures["private_access"]
    for module_path, names in sorted(ledger.allowed_private_by_module.items()):
        if module_path not in measured_modules:
            detail = "module path does not exist in measured roots"
            found.append(Violation("allowed_module_missing", CONFIGURATION, module_path, detail))
            continue
        if observed_private is None:
            continue
        used = observed_private.get(module_path, frozenset())
        for name in sorted(names - used):
            unit = f"{module_path}::{name}"
            detail = "allowed private name is not used in this module; remove it"
            found.append(Violation("stale_allowed_private", CONFIGURATION, unit, detail))
    return found


def check_tree(
    ledger: Ledger,
    figures: Figures,
    today: datetime.date,
    *,
    observed_private: Mapping[str, frozenset[str]] | None = None,
) -> list[Violation]:
    """Compare the measured tree with the ledger.

    Parameters
    ----------
    ledger : Ledger
        The structure budget.
    figures : dict[str, dict[str, int]]
        Output of :func:`tools.structure_measures.measure_roots`.
    today : datetime.date
        Date against which exception review dates are judged.
    observed_private : Mapping[str, frozenset[str]] or None, optional
        Private accesses observed per measured module before allowlist filtering,
        used to catch unused module-scoped allowances.

    Returns
    -------
    list[Violation]
        Units above their threshold that are unlisted, above or below their
        ceiling; ledger entries and exceptions that no longer apply; missing or
        stale module-scoped private allowances; expired exceptions. Empty when
        the ledger describes the tree exactly.
    """
    found: list[Violation] = []
    for measure in MEASURES:
        threshold = ledger.thresholds[measure]
        over = {unit: n for unit, n in figures[measure].items() if n > threshold}
        for unit, figure in sorted(over.items()):
            violation = _check_unit(ledger, measure, unit, figure, today)
            if violation is not None:
                found.append(violation)
        for unit in sorted(set(ledger.ceilings[measure]) - set(over)):
            detail = "the unit is gone or no longer above its threshold; remove it with --update"
            found.append(Violation("stale_entry", measure, unit, detail))
    for entry in ledger.exceptions:
        measure, unit = entry["measure"], entry["unit"]
        if measure == CONFIGURATION:
            continue
        if figures[measure].get(unit, 0) <= ledger.thresholds[measure]:
            detail = "the unit is gone or no longer above its threshold; remove the exception"
            found.append(Violation("exception_unused", measure, unit, detail))
    return (
        found
        + _check_allowed_private_modules(ledger, figures, observed_private)
        + _check_configuration_exceptions(ledger, today)
    )


def _check_configuration_exceptions(ledger: Ledger, today: datetime.date) -> list[Violation]:
    """Hold standing configuration allowances to their date and recorded maximum."""
    found: list[Violation] = []
    for entry in ledger.exceptions:
        if entry["measure"] != CONFIGURATION:
            continue
        unit = entry["unit"]
        if datetime.date.fromisoformat(str(entry["review_by"])) < today:
            detail = f"review_by {entry['review_by']}"
            found.append(Violation("exception_expired", CONFIGURATION, unit, detail))
        elif unit in ledger.thresholds:
            figure = ledger.thresholds[unit]
            if figure > entry["allowed"]:
                detail = f"{figure} is above the excepted {entry['allowed']}"
                found.append(Violation("above_ceiling", CONFIGURATION, unit, detail))
        elif not ledger.allows_private_unit(unit):
            detail = "the configuration allowance is unused; remove the exception"
            found.append(Violation("exception_unused", CONFIGURATION, unit, detail))
    return found


def _check_allowed_private_growth(ledger: Ledger, baseline: Ledger) -> list[Violation]:
    """Refuse growth of global or module-scoped private allowances without an exception."""
    found: list[Violation] = []
    for name in sorted(ledger.allowed_private - baseline.allowed_private):
        if ledger.excepted(CONFIGURATION, name) is None:
            detail = "allowed private name added without an exception"
            found.append(Violation("allowed_list_grown", CONFIGURATION, name, detail))
    for module_path, names in sorted(ledger.allowed_private_by_module.items()):
        before = baseline.allowed_private_by_module.get(module_path, frozenset())
        for name in sorted(names - before):
            unit = f"{module_path}::{name}"
            if ledger.excepted(CONFIGURATION, unit) is None:
                detail = "allowed private name added without an exception"
                found.append(Violation("allowed_list_grown", CONFIGURATION, unit, detail))
    return found


def _check_configuration(ledger: Ledger, baseline: Ledger) -> list[Violation]:
    """Reject reduced measurement scope and unrecorded configuration growth."""
    found: list[Violation] = []
    if ledger.schema < baseline.schema:
        found.append(Violation("schema_downgrade", CONFIGURATION, "schema", "schema may not fall"))
    removed_roots = sorted(set(baseline.roots) - set(ledger.roots))
    if removed_roots:
        detail = f"measured roots removed: {removed_roots}"
        found.append(Violation("roots_removed", CONFIGURATION, "roots", detail))
    for measure in MEASURES:
        before, after = baseline.thresholds[measure], ledger.thresholds[measure]
        if after > before and ledger.excepted(CONFIGURATION, measure) is None:
            detail = f"{before} -> {after} without an exception"
            found.append(Violation("threshold_raised", CONFIGURATION, measure, detail))
    found.extend(_check_allowed_private_growth(ledger, baseline))
    return found


def check_baseline(ledger: Ledger, baseline: Ledger) -> list[Violation]:
    """Compare the ledger with the ledger of the baseline revision.

    Parameters
    ----------
    ledger : Ledger
        The candidate structure budget.
    baseline : Ledger
        The structure budget at the baseline revision.

    Returns
    -------
    list[Violation]
        Entries that are new, figures that rose, moves that do not connect a
        baseline entry with a ledger entry, thresholds that rose and allowed
        private names that were added. A recorded move carries the baseline
        figure of its origin to its destination.
    """
    found = _check_configuration(ledger, baseline)
    for measure in MEASURES:
        before = baseline.ceilings[measure]
        origin = {m["to"]: m["from"] for m in ledger.moves if m["measure"] == measure}
        for unit, figure in sorted(ledger.ceilings[measure].items()):
            source = unit if unit in before else origin.get(unit)
            if source is None or source not in before:
                detail = f"{figure} is a new entry; debt may not be added"
                found.append(Violation("new_debt", measure, unit, detail))
            elif figure > before[source]:
                detail = f"{before[source]} -> {figure}; a ceiling may not rise"
                found.append(Violation("raised_ceiling", measure, unit, detail))
    for move in ledger.moves:
        measure = move["measure"]
        known = measure in MEASURES
        if (
            not known
            or move["from"] not in baseline.ceilings[measure]
            or move["to"] not in ledger.ceilings[measure]
        ):
            detail = f"{move['from']} -> {move['to']} does not connect a baseline entry"
            found.append(Violation("stale_move", measure, move["to"], detail))
    return found


def _git(root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *arguments],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as error:
        raise LedgerError(f"baseline_unavailable: cannot run git: {error}") from error


def _git_output(root: Path, *arguments: str) -> str:
    done = _git(root, *arguments)
    if done.returncode != 0:
        reason = done.stderr.strip() or f"exit {done.returncode}"
        raise LedgerError(f"baseline_unavailable: git {arguments[0]} failed: {reason}")
    return done.stdout


def load_baseline(root: Path, revision: str, ledger_path: str) -> Ledger | None:
    """Read the ledger of a revision.

    Parameters
    ----------
    root : Path
        Repository root.
    revision : str
        Git revision, for example ``HEAD`` or ``HEAD^``.
    ledger_path : str
        Repository-relative path of the ledger.

    Returns
    -------
    Ledger or None
        The baseline ledger, or ``None`` when the revision exists and holds
        no ledger file: the one case in which the comparison is skipped.

    Raises
    ------
    LedgerError
        When ``git`` cannot run, the revision is unknown, or its ledger is malformed.
    """
    known = _git(root, "rev-parse", "--verify", "--quiet", f"{revision}^{{commit}}")
    if known.returncode != 0:
        raise LedgerError(f"baseline_unavailable: unknown revision {revision!r}")
    commit = known.stdout.strip()
    if not _git_output(root, "ls-tree", "--name-only", commit, "--", ledger_path).strip():
        return None
    baseline = parse_ledger(_git_output(root, "show", f"{commit}:{ledger_path}"))
    if baseline.schema == 1:
        try:
            measured = measure_revision(
                root,
                commit,
                baseline.roots,
                allowed_private=baseline.allowed_private,
            )
        except ValueError as error:
            raise LedgerError(f"baseline_unavailable: {error}") from error
        units = measured[_INITIALISATION_MEASURE]
        ceilings = dict(baseline.ceilings)
        ceilings[_INITIALISATION_MEASURE] = {
            unit: figure for unit, figure in units.items() if figure > _INITIALISATION_THRESHOLD
        }
        baseline = replace(baseline, ceilings=ceilings, schema=_INITIALISATION_SCHEMA)
    return baseline


def _with_measured_ceilings(ledger: Ledger, figures: Figures) -> Ledger:
    """Return the ledger with its ceilings replaced by the measured figures."""
    ceilings = {
        measure: {
            unit: figure
            for unit, figure in figures[measure].items()
            if figure > ledger.thresholds[measure] and ledger.excepted(measure, unit) is None
        }
        for measure in MEASURES
    }
    return replace(ledger, ceilings=ceilings, schema=SCHEMA)


def _report(ledger: Ledger, baseline: Ledger | None) -> list[str]:
    """Return one line per unit whose listed figure differs from the baseline."""
    if baseline is None:
        return ["no baseline ledger: nothing to compare"]
    lines: list[str] = []
    for measure in MEASURES:
        before, after = baseline.ceilings[measure], ledger.ceilings[measure]
        for unit in sorted(set(before) | set(after)):
            if before.get(unit) != after.get(unit):
                old = before.get(unit, "within threshold")
                new = after.get(unit, "within threshold")
                lines.append(f"{measure} {unit}: {old} -> {new}")
    return lines or ["no listed figure changed"]


def _parse_arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check or update the structure budget.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Fail when the budget is broken.")
    mode.add_argument("--update", action="store_true", help="Rewrite the ceilings from the tree.")
    mode.add_argument("--report", action="store_true", help="Print figures changed since baseline.")
    base = parser.add_mutually_exclusive_group(required=True)
    base.add_argument("--baseline", metavar="REV", help="Git revision holding the trusted ledger.")
    base.add_argument("--no-baseline", action="store_true", help="Outside a git checkout only.")
    base.add_argument(
        "--walk-from",
        metavar="REV",
        help="With --check: judge every first-parent commit after REV, up to --to, "
        "against its own parent (all zeros walks from the merge base with --default-branch).",
    )
    parser.add_argument("--to", metavar="REV", default="HEAD", help="Tip of the --walk-from range.")
    parser.add_argument(
        "--default-branch", metavar="REF", help="Anchor of an all-zero --walk-from."
    )
    parser.add_argument("--max-commits", type=int, default=100, help="Largest range to walk.")
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    parser.add_argument("--ledger", default=DEFAULT_LEDGER)
    return parser.parse_args(argv)


def _run(arguments: argparse.Namespace) -> int:
    root = arguments.root.resolve()
    if arguments.walk_from is not None:
        if not arguments.check:
            raise LedgerError("walk_refused: --walk-from judges only, so it runs only with --check")
        from tools.structure_budget_walk import run_walk

        return run_walk(
            root,
            before=arguments.walk_from,
            to=arguments.to,
            ledger_path=arguments.ledger,
            default_branch=arguments.default_branch,
            max_commits=arguments.max_commits,
        )
    path = root / arguments.ledger
    try:
        ledger = parse_ledger(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise LedgerError(f"ledger_missing: {error}") from error
    unresolved: dict[str, tuple[str, ...]] = {}
    observed_private: dict[str, frozenset[str]] = {}
    figures = measure_roots(
        root,
        ledger.roots,
        allowed_private=ledger.allowed_private,
        allowed_private_by_module=ledger.allowed_private_by_module,
        unresolved=unresolved,
        observed_private=observed_private,
    )
    for unit, calls in sorted(unresolved.items()):
        print(f"initialisation unresolved: {unit}: {'; '.join(calls)}")
    baseline = None
    if not arguments.no_baseline:
        baseline = load_baseline(root, arguments.baseline, arguments.ledger)
        if baseline is None:
            print(f"bootstrap: {arguments.baseline} holds no ledger; baseline rules skipped")
    if arguments.update:
        ledger = _with_measured_ceilings(ledger, figures)
    if arguments.report:
        print("\n".join(_report(ledger, baseline)))
        return 0
    found = check_tree(ledger, figures, datetime.date.today(), observed_private=observed_private)
    if baseline is not None:
        found += check_baseline(ledger, baseline)
    for violation in found:
        print(violation.render())
    if found:
        print(f"structure budget: {len(found)} violation(s)")
        return 1
    if arguments.update:
        path.write_text(render_ledger(ledger), encoding="utf-8")
    listed = sum(len(units) for units in ledger.ceilings.values())
    print(f"structure budget holds: listed figures {listed}, exceptions {len(ledger.exceptions)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the checker.

    Parameters
    ----------
    argv : list[str] or None
        Arguments without the program name; ``None`` reads ``sys.argv``.

    Returns
    -------
    int
        ``0`` when the budget holds (or was updated, or reported), ``1`` on
        violations, ``2`` when the ledger, a source file or the baseline
        cannot be read.
    """
    arguments = _parse_arguments(argv)
    try:
        return _run(arguments)
    except (LedgerError, SyntaxError, UnicodeDecodeError, FileNotFoundError) as error:
        print(f"structure budget cannot be judged: {error}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
