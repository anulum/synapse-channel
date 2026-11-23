# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — one scope's level, its criteria and its judgements
"""Own the level tables. Counts that read attempts stay on the attempt ledger."""

from __future__ import annotations

from dataclasses import dataclass

from synapse_channel.core.operation_level_criteria import CriteriaItem
from synapse_channel.core.operation_levels import DEFAULT_LEVEL, LEVELS


@dataclass(frozen=True)
class LevelCriteria:
    """One recorded criteria: what a raise of ``scope`` to ``target`` must show."""

    seq: int
    scope: str
    target: str
    items: tuple[CriteriaItem, ...]
    declared_by: str


@dataclass(frozen=True)
class LevelJudgement:
    """One recorded judgement of a criteria."""

    digest: str
    verdict: str
    judge: str


@dataclass
class _ScopeLevel:
    """One scope's autonomy level, the epoch of its latest record and its level line."""

    level: str = DEFAULT_LEVEL
    epoch: int = 0
    line: int = 0
    step_down: int = 0
    """The sequence of the scope's latest record that lowered its level; 0 when none."""


class LevelLedger:
    """The level of each scope, and the criteria and judgements recorded for it."""

    def __init__(self) -> None:
        self._levels: dict[str, _ScopeLevel] = {}
        self._criteria: dict[str, LevelCriteria] = {}
        self._judgements: dict[int, LevelJudgement] = {}

    @property
    def levels(self) -> dict[str, _ScopeLevel]:
        """Return the live levels of every scope."""
        return self._levels

    @property
    def criteria(self) -> dict[str, LevelCriteria]:
        """Return the recorded criteria by digest."""
        return self._criteria

    @property
    def judgements(self) -> dict[int, LevelJudgement]:
        """Return the recorded judgements by sequence."""
        return self._judgements

    def publish_from(
        self,
        levels: dict[str, _ScopeLevel],
        criteria: dict[str, LevelCriteria],
        judgements: dict[int, LevelJudgement],
    ) -> None:
        """Install the candidate's level tables by reference."""
        self._levels = levels
        self._criteria = criteria
        self._judgements = judgements

    def note_level(self, scope: str, seq: int, *, level: str, epoch: int) -> None:
        """Record that the row at ``seq`` set ``scope`` to ``level`` at ``epoch``."""
        previous = self._levels.get(scope) or _ScopeLevel()
        lowered = LEVELS.index(level) < LEVELS.index(previous.level)
        step_down = seq if lowered else previous.step_down
        self._levels[scope] = _ScopeLevel(level, epoch, seq, step_down)

    def level_of(self, scope: str) -> tuple[str, int]:
        """Return the level of ``scope`` and its latest record's epoch; ``A1`` and 0 without one."""
        record = self._levels.get(scope)
        return (DEFAULT_LEVEL, 0) if record is None else (record.level, record.epoch)

    def note_criteria(self, digest: str, criteria: LevelCriteria) -> None:
        """Record a criteria under its digest; the first record of a digest stands."""
        self._criteria.setdefault(digest, criteria)

    def note_judgement(self, seq: int, judgement: LevelJudgement) -> None:
        """Record the judgement journalled at ``seq``."""
        self._judgements[seq] = judgement

    def window_start(self, scope: str, criteria_seq: int) -> int:
        """Return where a count of ``scope`` starts: after its criteria and its latest step down."""
        record = self._levels.get(scope)
        return max(criteria_seq, 0 if record is None else record.step_down)
