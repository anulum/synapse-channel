# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — sealed visual evidence for the JetBrains ACP E2E
"""Capture bounded, owner-only, immutable screenshot artifacts."""

from __future__ import annotations

import os
import stat
import subprocess  # nosec B404
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from e2e.opencode_editors.jetbrains_timing import DEFAULT_JETBRAINS_TIMING
from e2e.opencode_editors.jetbrains_x11_driver import _bounded_poll_sleep, _required_tool

_SCREENSHOT_TIMEOUT_SECONDS = DEFAULT_JETBRAINS_TIMING.screenshot_seconds
_CHAT_OPEN_RETRY_SECONDS = 5.0


def capture_screenshot(path: Path, *, deadline: float | None = None) -> None:
    """Capture one bounded GUI artifact without replacing an existing path."""
    if path.exists() or path.is_symlink():
        raise RuntimeError(f"refusing to replace existing JetBrains screenshot: {path}")
    timeout = _SCREENSHOT_TIMEOUT_SECONDS
    if deadline is not None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("JetBrains screenshot phase deadline expired")
        timeout = min(timeout, remaining)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.stem}.",
        suffix=path.suffix,
        dir=path.parent,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    temporary.chmod(0o600)
    try:
        completed = subprocess.run(  # nosec B603
            [_required_tool("import"), "-window", "root", str(temporary)],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip() or "no diagnostic"
            raise RuntimeError(f"ImageMagick could not capture JetBrains evidence: {detail}")
        metadata = temporary.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_size <= 0
        ):
            raise RuntimeError("ImageMagick produced an unsafe or empty JetBrains screenshot")
        try:
            os.link(temporary, path, follow_symlinks=False)
        except OSError as exc:
            raise RuntimeError(f"JetBrains screenshot could not be sealed: {path}") from exc
    finally:
        temporary.unlink(missing_ok=True)


def trace_has(trace: Path, marker: str) -> bool:
    """Return whether one safe trace artifact contains the required marker."""
    return (
        trace.is_file() and not trace.is_symlink() and marker in trace.read_text(encoding="utf-8")
    )


def wait_for_trace(
    trace: Path,
    marker: str,
    deadline: float,
    process: subprocess.Popen[str],
    *,
    guard: Callable[[], object] | None = None,
) -> None:
    """Wait for trace evidence while enforcing lifecycle and process liveness."""
    while time.monotonic() < deadline:
        if guard is not None:
            guard()
        if trace_has(trace, marker):
            return
        if process.poll() is not None:
            raise RuntimeError(f"IntelliJ IDEA exited before ACP evidence: {process.returncode}")
        _bounded_poll_sleep(deadline)
    raise RuntimeError(f"IntelliJ IDEA ACP trace never contained {marker}")


def wait_for_idea_log(
    log_root: Path,
    markers: str | tuple[str, ...],
    deadline: float,
    poll: Callable[[], int | None],
    *,
    retry: Callable[[], None] | None = None,
    retry_interval_seconds: float = _CHAT_OPEN_RETRY_SECONDS,
    guard: Callable[[], object] | None = None,
    matcher: Callable[[str], bool] | None = None,
    contents_reader: Callable[[], str] | None = None,
) -> None:
    """Wait for exact IDEA log evidence while proving the process remains live."""
    required = (markers,) if isinstance(markers, str) else markers
    if not required:
        raise ValueError("at least one IDEA log marker is required")
    if retry is not None and retry_interval_seconds <= 0:
        raise ValueError("IDEA log retry interval must be positive")
    idea_log = log_root / "idea.log"
    next_retry = 0.0
    while time.monotonic() < deadline:
        if guard is not None:
            guard()
        if contents_reader is not None or idea_log.is_file():
            contents = (
                contents_reader()
                if contents_reader is not None
                else idea_log.read_text(encoding="utf-8", errors="replace")
            )
            if matcher is not None and matcher(contents):
                return
            position = 0
            matched = True
            for marker in required:
                position = contents.find(marker, position)
                if position < 0:
                    matched = False
                    break
                position += len(marker)
            if matched:
                return
        if poll() is not None:
            raise RuntimeError(f"IntelliJ IDEA exited before log evidence {required!r}")
        now = time.monotonic()
        if retry is not None and now >= next_retry:
            retry()
            next_retry = now + retry_interval_seconds
        _bounded_poll_sleep(deadline)
    raise RuntimeError(f"IntelliJ IDEA log never contained ordered markers {required!r}")
