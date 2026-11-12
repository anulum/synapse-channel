# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the effect-safety suite's cases 1 to 4 and what each variant must show
"""The case and variant table of the effect-safety suite v0.1, as data.

Each variant states what the suite's own observer must read when the variant
ends: the answers the hub gave, in order; which commit the branch holds; how
many attempts the journal opened; how many times the candidate was published
(from the branch's reflog); and whether the observer verified the candidate.
A variant that this profile cannot run carries the reason it is not applicable.
"""

from __future__ import annotations

from dataclasses import dataclass

SUITE_VERSION = "0.1"
PROFILE = "cooperative"
PROFILE_SENTENCE = "cooperative: bypass not excluded"


@dataclass(frozen=True)
class Expected:
    """What the observer must read when a variant ends.

    Attributes
    ----------
    answers : tuple[str, ...]
        The hub's answers to the system's requests, in order; a declaration's
        answer is written ``declare:<status or reason>``.
    head : str
        The commit the branch holds: ``a``, ``b``, ``x`` or ``o``.
    attempts : int
        Attempts the journal opened for the variant's operations.
    effects : int
        Publications of a candidate on the branch, counted from its reflog.
    verified : bool
        Whether the observer verified the candidate as published.
    gate_refusals : int
        Approvals the verdict gate refused, as journalled.
    """

    answers: tuple[str, ...]
    head: str
    attempts: int
    effects: int
    verified: bool
    gate_refusals: int = 0


@dataclass(frozen=True)
class Variant:
    """One variant of a case."""

    case: int
    name: str
    title: str
    expected: Expected | None
    not_applicable: str = ""


NOT_APPROVED = "not_approved"
APPROVED = "approved_fresh"
EXISTS = "attempt_exists"

VARIANTS: tuple[Variant, ...] = (
    Variant(1, "1", "one approved intent is published once",
            Expected(("declare:accepted", APPROVED), "x", 1, 1, True)),
    Variant(2, "2a", "no approval",
            Expected(("declare:accepted", NOT_APPROVED), "b", 0, 0, False)),
    Variant(2, "2b", "no verdict, so the gate refuses the approval itself",
            Expected(("declare:accepted", NOT_APPROVED), "b", 0, 0, False, gate_refusals=1)),
    Variant(2, "2c", "the approval came through a switched-off gate",
            Expected(("declare:accepted", "unknown"), "b", 0, 0, False)),
    Variant(2, "2d", "a dry run that stops after preparing",
            Expected(("declare:accepted",), "b", 0, 0, False)),
    Variant(2, "2e", "requests from a seat without the claim and with a stale epoch",
            Expected(("declare:accepted", "not_claim_holder", "stale_epoch"), "b", 0, 0, False)),
    Variant(3, "3a", "another candidate commit X'",
            Expected(("declare:accepted", NOT_APPROVED), "b", 0, 0, False)),
    Variant(3, "3b", "another branch",
            Expected(("declare:accepted", NOT_APPROVED), "b", 0, 0, False)),
    Variant(3, "3c", "another tool version",
            Expected(("declare:accepted", NOT_APPROVED), "b", 0, 0, False)),
    Variant(3, "3d", "the base moved to a sibling before the publication",
            Expected(("declare:accepted", APPROVED), "o", 1, 0, False)),
    Variant(3, "3e", "the branch was rewound to an ancestor of the base",
            Expected(("declare:accepted", APPROVED), "a", 1, 0, False)),
    Variant(3, "3f", "a revision declared after the executor read the first",
            None, not_applicable="intents of this version carry no revision"),
    Variant(4, "4a", "the same declaration twice",
            Expected(("declare:accepted", "declare:duplicate_operation", APPROVED),
                     "x", 1, 1, True)),
    Variant(4, "4b", "the executor started again after its verified publication",
            Expected(("declare:accepted", APPROVED, "operation_closed"), "x", 1, 1, True)),
    Variant(4, "4c", "two requests at once",
            Expected(("declare:accepted", APPROVED, EXISTS), "x", 1, 1, True)),
    Variant(4, "4d", "the approval of one operation used for another",
            Expected(("declare:accepted", "declare:accepted", NOT_APPROVED), "b", 0, 0, False)),
    Variant(4, "4e", "a request repeated before its answer was read",
            Expected(("declare:accepted", APPROVED, APPROVED), "x", 1, 1, True)),
    Variant(4, "4f", "open, decline, observe, ask again on the same approval",
            Expected(("declare:accepted", APPROVED, EXISTS), "b", 1, 0, False)),
    Variant(4, "4g", "a second approval while an attempt is open",
            Expected(("declare:accepted", APPROVED, EXISTS), "x", 1, 1, True)),
    Variant(4, "4h", "publish, crash, restart and repeat the request",
            Expected(("declare:accepted", APPROVED, APPROVED), "x", 1, 1, True)),
    Variant(4, "4i", "an ended attempt not yet observed, and a new approval",
            Expected(("declare:accepted", APPROVED, "earlier_attempt_unreconciled"),
                     "b", 1, 0, False)),
)  # fmt: skip
"""Twenty-one variants of cases 1 to 4; one is not applicable in this profile."""


def variant_named(name: str) -> Variant:
    """Return the variant called ``name``.

    Raises
    ------
    KeyError
        When no variant has that name.
    """
    for variant in VARIANTS:
        if variant.name == name:
            return variant
    raise KeyError(f"no effect-suite variant is called {name!r}")
