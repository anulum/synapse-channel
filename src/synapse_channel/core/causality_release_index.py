# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — released claims indexed by the worktree they can contend in
"""Find the latest release that gated a new claim without scanning every release.

:func:`~synapse_channel.core.path_identity.claim_scopes_conflict` contends two
claim scopes only when their worktrees match or their local object identities
are comparable. Every released claim is therefore filed under the keys at which
such a match is possible, and a new claim is compared only with the releases
filed under its own keys. The exact predicate still decides each candidate, so
the index narrows the search and never decides on its own.

The keys follow ``claim_worktrees_match`` case by case:

* two legacy claims match when their worktree display strings are equal;
* two identity-bearing claims match only when their worktree paths are equal
  under the shared case policy, which implies equal case-folded labels;
* a legacy and an identity-bearing claim match when the legacy display,
  labelled under the identity's case policy, equals the identity's path;
* object identities are comparable when both name the same filesystem
  namespace and worktree object.
"""

from __future__ import annotations

import heapq
from collections import defaultdict
from collections.abc import Iterator
from typing import TYPE_CHECKING

from synapse_channel.core.path_identity import claim_scopes_conflict, comparison_worktree

if TYPE_CHECKING:
    from synapse_channel.core.causality import CausalNode

_Entry = tuple[int, "CausalNode"]
_CASE_POLICIES = (True, False)


class ReleasedClaimIndex:
    """Released claims, filed by every key under which they can contend.

    Entries are appended in event order, so every bucket holds ascending
    release sequences and the newest candidate is always at a bucket's end.
    """

    def __init__(self) -> None:
        """Create an empty index."""
        self._legacy_by_display: defaultdict[str, list[_Entry]] = defaultdict(list)
        self._legacy_by_label: defaultdict[tuple[bool, str], list[_Entry]] = defaultdict(list)
        self._identity_by_folded: defaultdict[str, list[_Entry]] = defaultdict(list)
        self._identity_by_label: defaultdict[tuple[bool, str], list[_Entry]] = defaultdict(list)
        self._identity_by_object: defaultdict[tuple[str, str], list[_Entry]] = defaultdict(list)

    def add(self, release_seq: int, freed: CausalNode) -> None:
        """File the claim that ``release_seq`` released.

        Parameters
        ----------
        release_seq : int
            Sequence of the release event; it must exceed every earlier one.
        freed : CausalNode
            The live claim node the release ended.
        """
        entry = (release_seq, freed)
        identity = freed.path_identity
        if identity is None:
            self._legacy_by_display[freed.worktree].append(entry)
            for case_sensitive in _CASE_POLICIES:
                label = comparison_worktree(freed.worktree, case_sensitive=case_sensitive)
                self._legacy_by_label[(case_sensitive, label)].append(entry)
            return
        folded = comparison_worktree(identity.worktree_path, case_sensitive=False)
        self._identity_by_folded[folded].append(entry)
        self._identity_by_label[(identity.case_sensitive, identity.worktree_path)].append(entry)
        if identity.filesystem_namespace and identity.worktree_object_id:
            key = (identity.filesystem_namespace, identity.worktree_object_id)
            self._identity_by_object[key].append(entry)

    def latest_conflicting(self, claim: CausalNode) -> int | None:
        """Return the latest earlier release by another owner whose scope contends.

        Parameters
        ----------
        claim : CausalNode
            A fresh claim node.

        Returns
        -------
        int or None
            The release sequence, or ``None`` when no earlier release by another
            owner contends with the claim's scope.
        """
        for release_seq, freed in self._newest_first(claim):
            if release_seq >= claim.seq or freed.owner == claim.owner:
                continue
            if claim_scopes_conflict(
                freed.worktree,
                freed.paths,
                freed.path_identity,
                claim.worktree,
                claim.paths,
                claim.path_identity,
            ):
                return release_seq
        return None

    def _newest_first(self, claim: CausalNode) -> Iterator[_Entry]:
        """Yield each candidate once, newest release first."""
        merged = heapq.merge(
            *(reversed(bucket) for bucket in self._buckets(claim)),
            key=lambda entry: entry[0],
            reverse=True,
        )
        previous: int | None = None
        for entry in merged:
            if entry[0] != previous:
                previous = entry[0]
                yield entry

    def _buckets(self, claim: CausalNode) -> list[list[_Entry]]:
        """Return the buckets holding every release that can contend with ``claim``."""
        identity = claim.path_identity
        if identity is None:
            buckets = [self._legacy_by_display.get(claim.worktree, [])]
            for case_sensitive in _CASE_POLICIES:
                label = comparison_worktree(claim.worktree, case_sensitive=case_sensitive)
                buckets.append(self._identity_by_label.get((case_sensitive, label), []))
            return buckets
        folded = comparison_worktree(identity.worktree_path, case_sensitive=False)
        buckets = [
            self._identity_by_folded.get(folded, []),
            self._legacy_by_label.get((identity.case_sensitive, identity.worktree_path), []),
        ]
        if identity.filesystem_namespace and identity.worktree_object_id:
            key = (identity.filesystem_namespace, identity.worktree_object_id)
            buckets.append(self._identity_by_object.get(key, []))
        return buckets
