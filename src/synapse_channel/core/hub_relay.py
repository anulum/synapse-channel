# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — mirror broadcasts to a bounded lite relay log
"""Mirror broadcast messages to a bounded NDJSON relay log.

The hub writes every broadcast it fans out to an optional relay log so a
disconnected observer can catch up from the file later, even when no socket was
connected at the time. :class:`RelayMirror` owns that responsibility on its own —
the append, the lite encoding, and the self-trimming that bounds the file — so the
hub keeps only a one-line call. The log is written in the versioned compact lite
envelope (:func:`~synapse_channel.core.relay.encode_lite`), including structured
payloads and non-core message fields, and trimmed back to ``max_lines`` once that
many lines have accrued since the last trim, bounding it to roughly twice that.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from synapse_channel.core.relay import append_jsonl, trim_jsonl_tail
from synapse_channel.core.relay_codec import encode_lite


class RelayMirror:
    """Append broadcasts to a bounded lite relay log, or do nothing without one.

    Parameters
    ----------
    log_path : pathlib.Path or None
        Destination NDJSON file. When ``None`` the mirror is a no-op — the hub was
        constructed without a relay log, so there is nothing to write.
    max_lines : int
        The line ceiling the log is trimmed back to. Each :meth:`mirror` appends a
        line and, once ``max_lines`` lines have been appended since the last trim,
        the tail is truncated to ``max_lines``, so the file stays bounded to about
        twice this value between trims.
    """

    def __init__(self, log_path: Path | None, max_lines: int) -> None:
        self._log_path = log_path
        self._max_lines = max_lines
        self._appends = 0

    @property
    def log_path(self) -> Path | None:
        """Return the relay log path, or ``None`` when the mirror is disabled."""
        return self._log_path

    @property
    def max_lines(self) -> int:
        """Return the line ceiling the log is trimmed back to."""
        return self._max_lines

    def mirror(self, data: dict[str, Any]) -> None:
        """Append one broadcast to the relay log, trimming when it grows full.

        The line is written even when no socket is connected — catching a later
        observer up from the file is the whole point. When ``log_path`` is ``None``
        this is a no-op.
        """
        if self._log_path is None:
            return
        append_jsonl(self._log_path, encode_lite(data))
        self._appends += 1
        if self._appends >= self._max_lines:
            trim_jsonl_tail(self._log_path, self._max_lines)
            self._appends = 0
