# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — parser for Grok CLI `--output-format streaming-json` output
"""Parse the ``streaming-json`` stream emitted by headless ``grok --single``.

.. warning::

   **The Grok stream schema is UNVERIFIED.** Every other provider parser in this package was
   written against output captured from a real invocation (the verified-at-source rule). Grok
   could not be captured the same way: the Grok CLI is RAM-hungry and unreliable on this
   machine and is not run here. This parser is therefore written to the **documented
   convention** rather than a captured trace, and must be re-verified against a real
   ``grok --single --output-format streaming-json`` run before its gated smoke is trusted.
   :data:`GROK_SCHEMA_VERIFIED` records this state.

Grok is a Claude-Code-family CLI — its ``--help`` maps its own flags onto Claude Code's
(``--allow`` ↔ ``--allowedTools``, ``--system-prompt-override`` ↔ ``--system-prompt``) — so its
``streaming-json`` is **assumed** to follow the same line-delimited event convention as Claude
Code's ``stream-json`` (a ``system`` init event carrying ``session_id``, ``assistant`` events
whose message content holds ``thinking`` and ``text`` blocks, and a terminal ``result`` event
that is authoritative for the answer, session token, and cost). On that assumption this parser
delegates to :func:`~synapse_channel.participants.stream_json.parse_claude_stream`, so a single
implementation covers both and there is no second, hand-fabricated schema to drift. If a real
capture later shows Grok diverges, this is the one place to specialise.
"""

from __future__ import annotations

from collections.abc import Iterable

from synapse_channel.participants.stream_json import StreamOutcome, parse_claude_stream

GROK_SCHEMA_VERIFIED = False
"""Whether the Grok stream schema has been captured from a real run. Currently ``False``.

The Grok driver is built for completeness but its output schema is assumed, not captured,
because the CLI is not run on this machine. Flip this to ``True`` only after a real
``grok --single --output-format streaming-json`` trace confirms the event shape.
"""


def parse_grok_stream(lines: Iterable[str]) -> StreamOutcome:
    """Parse Grok ``--output-format streaming-json`` lines into a :class:`StreamOutcome`.

    Delegates to :func:`~synapse_channel.participants.stream_json.parse_claude_stream` on the
    assumption — see this module's warning and :data:`GROK_SCHEMA_VERIFIED` — that Grok's
    streaming-json follows the same Claude-Code-family event convention.

    Parameters
    ----------
    lines : Iterable[str]
        The provider's stdout split into lines.

    Returns
    -------
    StreamOutcome
        The distilled outcome, under the unverified Claude-family schema assumption.
    """
    return parse_claude_stream(lines)
