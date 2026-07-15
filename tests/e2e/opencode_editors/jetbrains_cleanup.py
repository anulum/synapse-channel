# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — fail-closed JetBrains evidence and process cleanup
"""Preserve editor evidence without ever skipping process-group cleanup."""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

from e2e.opencode_editors.process_group import terminate_isolated_process_group


class JetBrainsCleanupError(RuntimeError):
    """Aggregate an editor failure with every subsequent cleanup failure."""

    def __init__(self, failures: Sequence[BaseException]) -> None:
        self.failures = tuple(failures)
        detail = "; ".join(f"{type(exc).__name__}: {exc}" for exc in self.failures)
        super().__init__(f"JetBrains editor cleanup failed: {detail}")


def capture_evidence_and_terminate(
    process: subprocess.Popen[str],
    *,
    screenshot: Path,
    capture_screenshot: Callable[[Path], None],
    active_error: BaseException | None,
) -> None:
    """Capture missing GUI evidence and always terminate the editor group.

    Parameters
    ----------
    process:
        Session-leading editor process whose complete process group must stop.
    screenshot:
        Expected screenshot artifact path.
    capture_screenshot:
        Bounded screenshot operation used only when the artifact is absent.
    active_error:
        Exception already propagating from the editor journey, if any. It is
        retained as the first aggregate failure and as the chained root cause.

    Raises
    ------
    Exception
        If cleanup fails when no earlier editor error is propagating.
    JetBrainsCleanupError
        If cleanup fails while another editor error is propagating, or if
        multiple cleanup operations fail.
    """
    failures: list[Exception] = []
    if not screenshot.exists():
        try:
            capture_screenshot(screenshot)
        except Exception as exc:  # noqa: BLE001 - cleanup must continue
            failures.append(exc)
    try:
        terminate_isolated_process_group(process)
    except Exception as exc:  # noqa: BLE001 - aggregate both cleanup failures
        failures.append(exc)

    if not failures:
        return
    if active_error is not None:
        raise JetBrainsCleanupError((active_error, *failures)) from active_error
    if len(failures) == 1:
        raise failures[0]
    raise JetBrainsCleanupError(failures)
