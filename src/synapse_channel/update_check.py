# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — best-effort "a newer release is available" notice
"""Surface an opt-in upgrade notice when a newer release is on PyPI.

The check is deliberately unobtrusive and private by default. ``synapse --version``
prints only the installed version unless :data:`ENABLE_ENV` is set. When enabled,
a daily on-disk cache means PyPI is queried at most once per
:data:`CACHE_TTL_SECONDS`, every failure path (no network, a slow endpoint,
malformed JSON, an unwritable cache) yields *no notice* rather than an error, and
the whole thing is silenced by the legacy :data:`SUPPRESS_ENV` environment
variable. The public entry point, :func:`update_notice`, takes injectable
``env``/``now``/``cache_path``/``fetch`` parameters so the behaviour is fully
deterministic under test.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from collections.abc import Callable, Mapping
from pathlib import Path

from . import __version__

#: PyPI JSON endpoint that reports the latest released version.
PYPI_URL = "https://pypi.org/pypi/synapse-channel/json"
#: Query PyPI at most once per this many seconds; serve the cache in between.
CACHE_TTL_SECONDS = 86_400.0
#: Setting this environment variable to any non-empty value enables the check.
ENABLE_ENV = "SYNAPSE_UPDATE_CHECK"
#: Setting this environment variable to any non-empty value disables the check.
SUPPRESS_ENV = "SYNAPSE_NO_UPDATE_CHECK"
_HTTP_TIMEOUT = 2.0

#: A callable that returns the latest version string, or ``None`` if it cannot.
FetchLatest = Callable[[], "str | None"]


def _cache_path(env: Mapping[str, str]) -> Path:
    """Return the cache file path, honouring ``XDG_CACHE_HOME``."""
    base = env.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "synapse-channel" / "update-check.json"


def _parse_version(value: str) -> tuple[int, ...]:
    """Parse a dotted version into an int tuple; a non-numeric suffix stops a part.

    ``"0.31.0"`` becomes ``(0, 31, 0)`` and ``"1.2.0rc1"`` becomes ``(1, 2, 0)`` —
    enough to order the simple ``MAJOR.MINOR.PATCH`` releases this package ships.
    """
    parts: list[int] = []
    for chunk in value.split("."):
        digits = ""
        for char in chunk:
            if char.isdigit():
                digits += char
            else:
                break
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def _is_newer(latest: str, current: str) -> bool:
    """Return ``True`` when ``latest`` orders strictly after ``current``."""
    return _parse_version(latest) > _parse_version(current)


def _fetch_latest(url: str = PYPI_URL, *, timeout: float = _HTTP_TIMEOUT) -> str | None:
    """Return PyPI's latest version for the package, or ``None`` on any failure."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310  # nosec B310
            payload = json.loads(response.read().decode("utf-8"))
        version = payload["info"]["version"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return str(version) if version else None


def _read_cache(path: Path) -> tuple[float, str] | None:
    """Return ``(checked_at, latest)`` from the cache file, or ``None`` if unusable."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return float(data["checked_at"]), str(data["latest"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _write_cache(path: Path, checked_at: float, latest: str) -> None:
    """Write the cache file; a failure to write is swallowed (the cache is optional)."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"checked_at": checked_at, "latest": latest}), encoding="utf-8")
    except OSError:
        pass


def _latest_known(now: float, cache_path: Path, fetch: FetchLatest) -> str | None:
    """Return the latest version, serving a fresh cache or refreshing it from PyPI.

    A fetch failure falls back to a stale cached value (better a day-old answer than
    none); a successful fetch refreshes the cache stamped at ``now``.
    """
    cached = _read_cache(cache_path)
    if cached is not None and now - cached[0] < CACHE_TTL_SECONDS:
        return cached[1]
    fetched = fetch()
    if fetched is not None:
        _write_cache(cache_path, now, fetched)
        return fetched
    return cached[1] if cached is not None else None


def update_notice(
    current: str = __version__,
    *,
    env: Mapping[str, str] | None = None,
    now: float | None = None,
    cache_path: Path | None = None,
    fetch: FetchLatest | None = None,
) -> str | None:
    """Return a one-line upgrade notice if a newer release exists, else ``None``.

    Parameters
    ----------
    current : str, optional
        The running version to compare against; defaults to the installed one.
    env : Mapping[str, str] or None, optional
        Environment mapping (for ``SUPPRESS_ENV`` and ``XDG_CACHE_HOME``); defaults
        to ``os.environ``. Injectable for testing.
    now : float or None, optional
        Current epoch seconds for the cache-freshness test; defaults to ``time.time()``.
    cache_path : Path or None, optional
        Cache file location; defaults to the per-user cache directory.
    fetch : FetchLatest or None, optional
        Callable returning PyPI's latest version; defaults to a live HTTP fetch
        only when ``env`` contains ``SYNAPSE_UPDATE_CHECK``.

    Returns
    -------
    str or None
        The notice text, or ``None`` when up to date, silenced, or offline.
    """
    env = os.environ if env is None else env
    if env.get(SUPPRESS_ENV):
        return None
    if not env.get(ENABLE_ENV):
        return None
    now = time.time() if now is None else now
    cache_path = _cache_path(env) if cache_path is None else cache_path
    fetch = _fetch_latest if fetch is None else fetch
    latest = _latest_known(now, cache_path, fetch)
    if latest is not None and _is_newer(latest, current):
        return (
            f"  → {latest} is available (you have {current}): "
            f"pipx upgrade synapse-channel\n"
            f"    (unset {ENABLE_ENV} or set {SUPPRESS_ENV}=1 to silence)"
        )
    return None
