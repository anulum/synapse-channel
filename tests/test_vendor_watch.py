# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — official-source vendor compatibility watch contracts
"""Exercise release parsing, source failure, review age and real CLI reporting."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request

import pytest
import tools.vendor_watch as watch
from tools.vendor_watch import (
    DEFAULT_MATRIX,
    SOURCES,
    VendorWatchError,
    build_report,
    fetch_source,
    load_matrix,
    parse_release,
)


def _release(tag: str, body: str = "Routine fix") -> bytes:
    """Build one bounded stable GitHub release response."""
    return json.dumps(
        {"tag_name": tag, "prerelease": False, "published_at": "2026-09-19T12:00:00Z", "body": body}
    ).encode()


def _source_payload(name: str, version: str, notes: str = "Routine fix") -> bytes:
    """Supply the official source's document shape for one release."""
    if name == "claude-code":
        return (
            f'<Update label="{version}" description="September 19, 2026">\n{notes}\n</Update>'
        ).encode()
    if name == "codex-cli":
        return (
            "<time>2026-09-19</time><h3>Codex CLI "
            f"<span data-release-title>{version}</span></h3><div>{notes}</div>"
        ).encode()
    return _release(version, notes)


def test_official_document_parsers_refuse_missing_and_prerelease() -> None:
    """Recognise current release shapes and fail on ambiguous source evidence."""
    assert parse_release("claude-markdown", _source_payload("claude-code", "2.1.278"))[:2] == (
        "2.1.278",
        "September 19, 2026",
    )
    assert parse_release("codex-html", _source_payload("codex-cli", "0.155.1"))[:2] == (
        "0.155.1",
        "2026-09-19",
    )
    assert parse_release("github-release", _release("v0.60.0"))[:2] == (
        "0.60.0",
        "2026-09-19",
    )
    with pytest.raises(VendorWatchError):
        parse_release("claude-markdown", b"the changelog moved")
    with pytest.raises(VendorWatchError):
        parse_release("codex-html", b"release title unavailable")
    with pytest.raises(VendorWatchError):
        parse_release("github-release", b'{"tag_name":"v1.0.0","prerelease":true}')
    with pytest.raises(VendorWatchError):
        parse_release("github-release", _release("unexpected/tag"))
    with pytest.raises(VendorWatchError):
        parse_release(
            "github-release",
            b'{"tag_name":"v1.0.0","prerelease":false,"published_at":null,"body":null}',
        )
    with pytest.raises(VendorWatchError):
        parse_release("unknown", b"{}")


def test_report_distinguishes_drift_unchanged_source_loss_and_priority() -> None:
    """A missing source never appears as unchanged or as a usable latest version."""
    matrix = load_matrix(DEFAULT_MATRIX)
    assert matrix["surfaces"]["claude-code"]["verified_version"] == "2.1.280"
    assert matrix["surfaces"]["opencode"]["verified_version"] == "1.18.32"
    assert matrix["surfaces"]["pi"]["verified_version"] == "0.87.1"
    matrix["surfaces"]["opencode"]["verified_version"] = "1.17.20"
    same_notes = parse_release("claude-markdown", _source_payload("claude-code", "2.1.280"))[2]
    matrix["surfaces"]["claude-code"]["reviewed_notes_sha256"] = hashlib.sha256(
        same_notes.encode()
    ).hexdigest()
    url_to_name = {spec[1]: name for name, spec in SOURCES.items()}

    def fetch(url: str) -> bytes:
        name = url_to_name[url]
        if name == "pi":
            raise OSError("offline")
        if name == "claude-code":
            return _source_payload(name, "2.1.280")
        if name == "opencode":
            return _source_payload(name, "1.18.32", "Breaking Changes: hook permission")
        if name == "gemini-cli":
            return _source_payload(name, "0.60.0", "Security fix for extension validation")
        if name == "mcp-spec":
            return _source_payload(name, "2026-07-28")
        return _source_payload(name, "0.155.1")

    report = build_report(
        matrix,
        fetch=fetch,
        probe=lambda command: "2.1.273" if command and command[0] == "claude" else None,
        now=datetime(2026, 9, 22, tzinfo=timezone.utc),
    )
    rows = report["surfaces"]
    assert rows["claude-code"]["status"] == "current"
    assert rows["claude-code"]["priority"] == "none"
    assert rows["claude-code"]["notes_changed"] is False
    assert rows["claude-code"]["installed_version"] == "2.1.273"
    assert rows["claude-code"]["capability_class"] == "native"
    assert rows["claude-code"]["owning_adapter"] == "integrations/claude-code"
    assert rows["opencode"]["status"] == "needs_validation"
    assert rows["opencode"]["notes_changed"] is True
    assert rows["opencode"]["priority"] == "breaking_review"
    assert rows["gemini-cli"]["priority"] == "security_review"
    assert rows["pi"]["status"] == "source_unavailable"
    assert "latest_version" not in rows["pi"]
    assert rows["mcp-spec"]["status"] == "unverified"
    assert report["review_overdue"] is False
    overdue = build_report(
        matrix,
        fetch=fetch,
        probe=lambda command: None,
        now=datetime(2026, 9, 30, tzinfo=timezone.utc),
    )
    assert overdue["review_overdue"] is True
    matrix["surfaces"]["claude-code"]["reviewed_notes_sha256"] = "0" * 64
    changed_notes = build_report(
        matrix,
        fetch=fetch,
        probe=lambda command: None,
        now=datetime(2026, 9, 22, tzinfo=timezone.utc),
    )
    assert changed_notes["surfaces"]["claude-code"]["status"] == "needs_review"
    with pytest.raises(VendorWatchError):
        build_report(matrix, fetch=fetch, now=datetime(2026, 9, 22))


def test_matrix_validation_and_network_allowlist(tmp_path: Path) -> None:
    """Corrupt matrix shape and nonofficial URLs fail before any network request."""
    assert set(load_matrix(DEFAULT_MATRIX)["surfaces"]) == set(SOURCES)
    with pytest.raises(VendorWatchError):
        fetch_source("http://127.0.0.1:8876/secret")
    path = tmp_path / "matrix.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(VendorWatchError):
        load_matrix(path)
    matrix = json.loads(DEFAULT_MATRIX.read_text(encoding="utf-8"))
    matrix["surfaces"].pop("pi")
    path.write_text(json.dumps(matrix), encoding="utf-8")
    with pytest.raises(VendorWatchError):
        load_matrix(path)
    matrix = json.loads(DEFAULT_MATRIX.read_text(encoding="utf-8"))
    matrix["review_owner"] = ""
    path.write_text(json.dumps(matrix), encoding="utf-8")
    with pytest.raises(VendorWatchError):
        load_matrix(path)
    matrix["review_owner"] = "SYNAPSE-CHANNEL/test"
    matrix.pop("reviewed_at")
    path.write_text(json.dumps(matrix), encoding="utf-8")
    with pytest.raises(VendorWatchError):
        load_matrix(path)
    matrix["reviewed_at"] = "2026-09-19"
    matrix["review_interval_days"] = 0
    path.write_text(json.dumps(matrix), encoding="utf-8")
    with pytest.raises(VendorWatchError):
        load_matrix(path)
    matrix["review_interval_days"] = 7
    matrix["surfaces"]["pi"] = []
    path.write_text(json.dumps(matrix), encoding="utf-8")
    with pytest.raises(VendorWatchError):
        load_matrix(path)
    matrix["surfaces"]["pi"] = {"verified_version": 12}
    path.write_text(json.dumps(matrix), encoding="utf-8")
    with pytest.raises(VendorWatchError):
        load_matrix(path)
    matrix["surfaces"]["pi"] = {"verified_version": None, "reviewed_notes_sha256": "bad"}
    path.write_text(json.dumps(matrix), encoding="utf-8")
    with pytest.raises(VendorWatchError):
        load_matrix(path)
    matrix["surfaces"]["pi"] = {
        "verified_version": None,
        "capability_class": "unknown",
        "owning_adapter": "C03",
        "migration_decision": "Review later",
    }
    path.write_text(json.dumps(matrix), encoding="utf-8")
    with pytest.raises(VendorWatchError):
        load_matrix(path)
    matrix["surfaces"]["pi"]["capability_class"] = "unsupported"
    matrix["surfaces"]["pi"]["owning_adapter"] = ""
    path.write_text(json.dumps(matrix), encoding="utf-8")
    with pytest.raises(VendorWatchError):
        load_matrix(path)
    matrix["surfaces"]["pi"]["owning_adapter"] = "C03"
    matrix["surfaces"]["pi"]["migration_decision"] = ""
    path.write_text(json.dumps(matrix), encoding="utf-8")
    with pytest.raises(VendorWatchError):
        load_matrix(path)


class _Response:
    """Small HTTP response for testing the actual source boundary."""

    def __init__(self, final: str, body: bytes) -> None:
        self.final = final
        self.body = body

    def __enter__(self) -> _Response:
        """Enter the HTTP response context."""
        return self

    def __exit__(self, *_args: object) -> None:
        """Leave the HTTP response context."""

    def geturl(self) -> str:
        """Return the final location after redirects."""
        return self.final

    def read(self, size: int) -> bytes:
        """Return at most the requested byte count."""
        return self.body[:size]


def test_source_fetch_rejects_redirect_and_oversize(monkeypatch: pytest.MonkeyPatch) -> None:
    """An official URL cannot redirect to another host or return unbounded data."""
    url = SOURCES["claude-code"][1]
    seen: list[tuple[Request, int]] = []

    def fake_open(request: Request, timeout: int) -> _Response:
        seen.append((request, timeout))
        return _Response(url, b"valid")

    monkeypatch.setattr(watch, "urlopen", fake_open)
    assert fetch_source(url) == b"valid"
    assert seen[0][1] == 15
    monkeypatch.setattr(
        watch, "urlopen", lambda request, timeout: _Response("https://example.com", b"x")
    )
    with pytest.raises(VendorWatchError):
        fetch_source(url)
    monkeypatch.setattr(
        watch,
        "urlopen",
        lambda request, timeout: _Response(url, b"x" * (watch.MAX_SOURCE_BYTES + 1)),
    )
    with pytest.raises(VendorWatchError):
        fetch_source(url)


def test_installed_probe_and_strict_report_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only version-only process probes run; strict review failures reach the CLI exit code."""
    assert watch.installed_version((sys.executable, "--version")) is not None
    assert watch.installed_version(("missing-vendor-executable-12345", "--version")) is None
    assert watch.installed_version(None) is None
    report = build_report(
        load_matrix(DEFAULT_MATRIX),
        fetch=lambda url: _source_payload(
            next(name for name, source in SOURCES.items() if source[1] == url), "0.0.1"
        ),
        probe=lambda command: None,
        now=datetime(2026, 9, 30, tzinfo=timezone.utc),
    )
    target = tmp_path / "report.json"
    monkeypatch.setattr(watch, "build_report", lambda matrix: report)
    monkeypatch.setattr(sys, "argv", ["vendor-watch", "--report", str(target), "--strict"])
    assert watch.main() == 1
    assert json.loads(target.read_text(encoding="utf-8"))["review_overdue"] is True
    monkeypatch.setattr(sys, "argv", ["vendor-watch", "--report", str(target)])
    assert watch.main() == 0

    def bad_matrix(_path: Path) -> dict[str, object]:
        """Simulate a local matrix read failure without reaching network."""
        raise VendorWatchError("private")

    monkeypatch.setattr(watch, "load_matrix", bad_matrix)
    assert watch.main() == 2


def test_public_tool_reports_bad_matrix_without_secret_echo(tmp_path: Path) -> None:
    """The actual CLI exits with a bounded diagnostic for invalid local input."""
    source = tmp_path / "matrix.json"
    source.write_text("{invalid credential=private}", encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[1] / "tools" / "vendor_watch.py"),
            "--matrix",
            str(source),
            "--report",
            str(tmp_path / "report.json"),
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 2
    assert "private" not in result.stderr
    assert not (tmp_path / "report.json").exists()
