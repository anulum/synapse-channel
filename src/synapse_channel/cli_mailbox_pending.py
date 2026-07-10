# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bounded mailbox-pending CLI projection
"""Select and render the operator-facing mailbox-pending summary."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from synapse_channel.core.mailbox_pending import format_pending_line

DEFAULT_MAILBOX_PENDING_LIMIT = 20
"""Positive mailbox identities shown by a default full-roster query."""


@dataclass(frozen=True)
class PendingDisplay:
    """One bounded, deterministically ordered mailbox-pending view."""

    rows: tuple[tuple[str, int], ...]
    total_identities: int
    total_messages: int

    @property
    def hidden_identities(self) -> int:
        """Return how many positive identities the bounded view omits."""
        return self.total_identities - len(self.rows)


def build_pending_display(
    counts: dict[str, int],
    *,
    project: str | None = None,
    limit: int | None = DEFAULT_MAILBOX_PENDING_LIMIT,
) -> PendingDisplay:
    """Return positive counts ordered by urgency, with optional display bound.

    Counts sort highest first so an operator sees the largest unattended
    mailboxes before old diagnostic identities; equal counts sort by identity
    for stable output. ``project`` filters before totals and limiting.
    """
    if limit is not None and limit < 1:
        raise ValueError("mailbox pending display limit must be positive")
    prefix = f"{project}/" if project else ""
    positive = [
        (identity, count)
        for identity, count in counts.items()
        if count > 0 and (project is None or identity == project or identity.startswith(prefix))
    ]
    ordered = tuple(sorted(positive, key=lambda item: (-item[1], item[0])))
    rows = ordered if limit is None else ordered[:limit]
    return PendingDisplay(
        rows=rows,
        total_identities=len(ordered),
        total_messages=sum(count for _identity, count in ordered),
    )


def render_mailbox_pending(
    counts: dict[str, int] | None,
    *,
    project: str | None,
    show_all: bool = False,
    write: Callable[[str], None] = print,
) -> None:
    """Render a bounded summary, an empty verdict, or unavailability."""
    if counts is None:
        write("Mailbox pending: unavailable (hub has no durable projection)")
        return
    display = build_pending_display(
        counts,
        project=project,
        limit=None if show_all else DEFAULT_MAILBOX_PENDING_LIMIT,
    )
    if not display.rows:
        write("Mailbox pending: none")
        return
    identity_noun = "identity" if display.total_identities == 1 else "identities"
    message_noun = "message" if display.total_messages == 1 else "messages"
    scope = f"{display.total_identities} {identity_noun}, {display.total_messages} {message_noun}"
    if display.hidden_identities:
        scope += f"; showing top {len(display.rows)} by count"
    write(f"Mailbox pending ({scope}):")
    for identity, count in display.rows:
        write(f"  {format_pending_line(identity, count)}")
    if display.hidden_identities:
        write(
            f"  ... {display.hidden_identities} more identities; "
            "use `synapse who --all-mailbox-pending` (or `--all`) to show all."
        )
