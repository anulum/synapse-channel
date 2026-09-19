# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — read-only official vendor release and compatibility watch
"""Compare official releases with host-tested integration versions, without upgrades."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import subprocess  # nosec B404 - fixed version-only commands, never a shell
import sys
from collections.abc import Callable, Mapping
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MATRIX = ROOT / "integrations" / "vendor-watch" / "compatibility.json"
MAX_SOURCE_BYTES = 2_000_000
MAX_VERSION_OUTPUT = 4096
SOURCES: dict[str, tuple[str, str, tuple[str, ...] | None]] = {
    "claude-code": (
        "claude-markdown",
        "https://code.claude.com/docs/en/changelog.md",
        ("claude", "--version"),
    ),
    "codex-cli": (
        "codex-html",
        "https://learn.chatgpt.com/docs/changelog",
        ("codex", "--version"),
    ),
    "gemini-cli": (
        "github-release",
        "https://api.github.com/repos/google-gemini/gemini-cli/releases/latest",
        ("gemini", "--version"),
    ),
    "pi": (
        "github-release",
        "https://api.github.com/repos/earendil-works/pi/releases/latest",
        ("pi", "--version"),
    ),
    "opencode": (
        "github-release",
        "https://api.github.com/repos/anomalyco/opencode/releases/latest",
        ("opencode", "--version"),
    ),
    "mcp-spec": (
        "github-release",
        "https://api.github.com/repos/modelcontextprotocol/modelcontextprotocol/releases/latest",
        None,
    ),
}
_VERSION = re.compile(r"\b\d+\.\d+\.\d+\b")
_CLAUDE = re.compile(r'<Update\s+label="(\d+\.\d+\.\d+)"\s+description="([^"]+)"')
_CODEX = re.compile(r"Codex CLI\s*<span[^>]*data-release-title[^>]*>(\d+\.\d+\.\d+)", re.S)
_SECURITY = re.compile(
    r"\b(?:security|vulnerability|vulnerabilities|CVE-\d+|auth(?:entication)? bypass)\b", re.I
)
_BREAKING = re.compile(r"\b(?:breaking changes?|removed APIs?|permission changes?)\b", re.I)


class VendorWatchError(ValueError):
    """An official source or tracked compatibility record is unusable."""


def fetch_source(url: str) -> bytes:
    """Fetch only a declared HTTPS source with a size and final-host bound."""
    if url not in {source[1] for source in SOURCES.values()}:
        raise VendorWatchError("source URL is not in the official allowlist")
    request = Request(
        url,
        headers={
            "User-Agent": "Synapse-Channel-Vendor-Watch/0.1",
            "Accept": "application/json,text/markdown,text/html",
        },
    )
    with urlopen(request, timeout=15) as response:  # nosec B310 - exact HTTPS allowlist
        final = urlsplit(response.geturl())
        if final.scheme != "https" or final.hostname != urlsplit(url).hostname:
            raise VendorWatchError("official source redirected to a different host")
        data = cast(bytes, response.read(MAX_SOURCE_BYTES + 1))
    if len(data) > MAX_SOURCE_BYTES:
        raise VendorWatchError("official source exceeded the byte limit")
    return data


def parse_release(kind: str, data: bytes) -> tuple[str, str, str]:
    """Extract a stable release id, date and bounded notes from one source."""
    text = data.decode("utf-8")
    if kind == "claude-markdown":
        match = _CLAUDE.search(text)
        if match is None:
            raise VendorWatchError("Claude changelog has no recognised release")
        section = text[match.end() :].split("<Update ", 1)[0]
        return match.group(1), match.group(2)[:80], section[:100_000]
    if kind == "codex-html":
        match = _CODEX.search(text)
        if match is None:
            raise VendorWatchError("Codex changelog has no recognised CLI release")
        previous = text[max(0, match.start() - 1000) : match.start()]
        dates = re.findall(r"<time[^>]*>(\d{4}-\d{2}-\d{2})</time>", previous)
        section = text[match.end() :].split("data-release-entry-id=", 1)[0]
        clean = html.unescape(re.sub(r"<[^>]+>", " ", section))
        return match.group(1), dates[-1] if dates else "unknown", clean[:100_000]
    if kind == "github-release":
        release = json.loads(text)
        if not isinstance(release, dict) or release.get("prerelease") is not False:
            raise VendorWatchError("GitHub latest is not a stable release object")
        tag = release.get("tag_name")
        published = release.get("published_at")
        body = release.get("body")
        if not isinstance(tag, str) or not re.fullmatch(
            r"v?(?:\d+\.\d+\.\d+|\d{4}-\d{2}-\d{2})", tag
        ):
            raise VendorWatchError("GitHub release tag has an unexpected shape")
        if not isinstance(published, str) or not isinstance(body, str):
            raise VendorWatchError("GitHub release evidence is incomplete")
        date.fromisoformat(published[:10])
        return tag.removeprefix("v"), published[:10], body[:100_000]
    raise VendorWatchError("unknown official source kind")


def installed_version(command: tuple[str, ...] | None) -> str | None:
    """Probe one installed CLI with its fixed version flag and no model turn."""
    if command is None:
        return None
    try:
        result = subprocess.run(  # nosec B603 - constant argv from SOURCES
            command, capture_output=True, text=True, timeout=8, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    output = (result.stdout + result.stderr)[:MAX_VERSION_OUTPUT]
    match = _VERSION.search(output)
    return match.group(0) if result.returncode == 0 and match else None


def load_matrix(path: Path) -> dict[str, Any]:
    """Validate the owned review date and exact supported-source inventory."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise VendorWatchError("compatibility matrix must be an object")
    matrix = cast(dict[str, Any], raw)
    if (
        matrix.get("schema") != 1
        or not isinstance(matrix.get("surfaces"), dict)
        or set(matrix["surfaces"]) != set(SOURCES)
    ):
        raise VendorWatchError("compatibility matrix schema or source inventory is invalid")
    if not isinstance(matrix.get("review_owner"), str) or not matrix["review_owner"]:
        raise VendorWatchError("compatibility review has no owner")
    reviewed_value = matrix.get("reviewed_at")
    if not isinstance(reviewed_value, str):
        raise VendorWatchError("compatibility review date is missing")
    reviewed = date.fromisoformat(reviewed_value)
    interval = matrix.get("review_interval_days")
    if not isinstance(interval, int) or isinstance(interval, bool) or not 1 <= interval <= 31:
        raise VendorWatchError("review interval must be 1..31 days")
    for item in matrix["surfaces"].values():
        if not isinstance(item, dict):
            raise VendorWatchError("surface record must be an object")
        version = item.get("verified_version")
        if version is not None and not isinstance(version, str):
            raise VendorWatchError("verified version must be a string or null")
        digest = item.get("reviewed_notes_sha256")
        if digest is not None and (
            not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        ):
            raise VendorWatchError("reviewed notes digest must be SHA-256 or null")
        if item.get("capability_class") not in {"native", "emulated", "manual", "unsupported"}:
            raise VendorWatchError("surface capability class is invalid")
        if not isinstance(item.get("owning_adapter"), str) or not item["owning_adapter"]:
            raise VendorWatchError("surface has no owning adapter")
        if not isinstance(item.get("migration_decision"), str) or not item["migration_decision"]:
            raise VendorWatchError("surface has no migration decision")
    matrix["next_review_at"] = (reviewed + timedelta(days=interval)).isoformat()
    return matrix


def build_report(
    matrix: Mapping[str, Any],
    *,
    fetch: Callable[[str], bytes] = fetch_source,
    probe: Callable[[tuple[str, ...] | None], str | None] = installed_version,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Compare all official sources, keeping failed checks distinct from no drift."""
    stamp = now or datetime.now(timezone.utc)
    if stamp.tzinfo is None:
        raise VendorWatchError("report time must include a timezone")
    due = stamp.date() > date.fromisoformat(matrix["next_review_at"])
    rows: dict[str, dict[str, Any]] = {}
    for name, (kind, url, command) in SOURCES.items():
        surface = matrix["surfaces"][name]
        verified = surface["verified_version"]
        row: dict[str, Any] = {
            "source": url,
            "verified_version": verified,
            "installed_version": probe(command),
            "channel": "stable",
            "capability_class": surface["capability_class"],
            "owning_adapter": surface["owning_adapter"],
            "migration_decision": surface["migration_decision"],
        }
        try:
            version, published, notes = parse_release(kind, fetch(url))
            row["latest_version"] = version
            row["published_at"] = published
            row["notes_sha256"] = hashlib.sha256(notes.encode("utf-8")).hexdigest()
            row["notes_changed"] = (
                surface.get("reviewed_notes_sha256") is not None
                and surface["reviewed_notes_sha256"] != row["notes_sha256"]
            )
            if verified is None:
                row["status"] = "unverified"
            else:
                row["status"] = "current" if verified == version else "needs_validation"
            if row["status"] == "current" and row["notes_changed"]:
                row["status"] = "needs_review"
            if row["status"] == "current":
                row["priority"] = "none"
            elif _SECURITY.search(notes):
                row["priority"] = "security_review"
            elif _BREAKING.search(notes):
                row["priority"] = "breaking_review"
            else:
                row["priority"] = "routine_review"
        except (OSError, ValueError, UnicodeError) as exc:
            row.update(
                status="source_unavailable",
                priority="source_failure",
                error=type(exc).__name__,
            )
        rows[name] = row
    return {
        "schema": 1,
        "checked_at": stamp.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "review_owner": matrix["review_owner"],
        "reviewed_at": matrix["reviewed_at"],
        "next_review_at": matrix["next_review_at"],
        "review_overdue": due,
        "surfaces": rows,
    }


def main() -> int:
    """Write one dated JSON report and fail closed on missing sources or stale review."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    try:
        report = build_report(load_matrix(args.matrix))
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    except (OSError, ValueError) as exc:
        print(f"vendor watch failed: {type(exc).__name__}", file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True))
    urgent = any(
        row["status"] == "source_unavailable"
        or row["priority"] in {"security_review", "breaking_review"}
        for row in report["surfaces"].values()
    )
    return 1 if args.strict and (urgent or report["review_overdue"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
