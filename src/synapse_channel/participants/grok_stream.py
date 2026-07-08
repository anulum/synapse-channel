# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — parser for Grok CLI `--output-format streaming-json` output
"""Parse the ``streaming-json`` stream emitted by headless ``grok --single``.

.. warning::

   **Grok support is ready.** The driver and parser are built and unit-tested. Prior
   workstation-level reliability issues with the Grok CLI were reported in mid-2026 (see
   internal escalation records from June 2026). As of current Grok releases (0.2.91+ observed),
   the binary is present and detected by ``synapse participant list``. The remaining gate is
   schema verification per the verified-at-source rule: the ``streaming-json`` output shape
   has not yet been captured from a real ``grok --single --output-format streaming-json`` run
   against the stable CLI on this host. This parser therefore follows the documented
   Claude-Code-family convention (delegating to the shared stream parser). Re-capture and
   re-verify against a current stable Grok CLI, then set :data:`GROK_SCHEMA_VERIFIED` to
   ``True`` and enable the gated smoke. The flag records this state.

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

Prior June 2026 escalations documented the Grok CLI as heavy/unreliable on the target
workstation (freezes, memory pressure). Those specific issues are no longer observed (binary
present at 0.2.91+, detected by the CLI). The flag remains ``False`` because no real
``grok --single --output-format streaming-json`` trace has been captured on this host against
a stable release (verified-at-source rule). Flip to ``True`` only after such a capture confirms
the event shape, then enable full smoke tests.
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
