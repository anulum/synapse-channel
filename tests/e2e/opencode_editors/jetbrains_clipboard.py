# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — owned X11 clipboard transport for JetBrains acceptance
"""Transfer one complete prompt through an isolated editor's X11 clipboard."""

from __future__ import annotations

import shutil
import subprocess  # nosec B404
import time
from collections.abc import Iterator
from contextlib import contextmanager


@contextmanager
def owned_clipboard(prompt: str, *, deadline: float) -> Iterator[None]:
    """Own the isolated Xvfb clipboard until the caller finishes pasting.

    Wait for exact UTF-8 readback before yielding. The caller must run inside
    the acceptance driver's private Xvfb display; this replaces that display's
    clipboard. Always reap the foreground selection owner, including on input,
    readback, paste, or deadline failure. No model or ACP request is made here.
    """
    executable = shutil.which("xclip")
    if executable is None:
        raise RuntimeError("JetBrains clipboard tool is unavailable: xclip")
    if time.monotonic() >= deadline:
        raise RuntimeError("JetBrains clipboard phase deadline expired")
    owner = subprocess.Popen(  # nosec B603
        [executable, "-selection", "clipboard", "-in", "-quiet"],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        assert owner.stdin is not None
        owner.stdin.write(prompt.encode("utf-8"))
        owner.stdin.close()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError("JetBrains clipboard phase deadline expired")
            if owner.poll() is not None:
                raise RuntimeError("JetBrains clipboard owner exited before paste")
            readback = subprocess.run(  # nosec B603
                [executable, "-selection", "clipboard", "-out"],
                capture_output=True,
                check=False,
                timeout=min(2.0, remaining),
            )
            if readback.returncode == 0 and readback.stdout == prompt.encode("utf-8"):
                break
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
        yield
    finally:
        if owner.stdin is not None and not owner.stdin.closed:
            owner.stdin.close()
        if owner.poll() is None:
            owner.terminate()
            try:
                owner.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                owner.kill()
                owner.wait(timeout=2.0)
