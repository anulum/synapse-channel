# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — build identity on version, doctor, A2A and MCP
"""The shipped version, doctor, A2A card and MCP instructions name one artefact."""

from __future__ import annotations

import importlib.metadata
import json
import subprocess
from pathlib import Path

import pytest

from synapse_channel import __version__
from synapse_channel.a2a import agent_card_from_manifest
from synapse_channel.build_identity import (
    DEVELOPMENT_MARK,
    WHEEL_NOT_RECORDED,
    changelog_release,
    declared_profile,
    development_mark_lines,
    identity_lines,
    identity_mapping,
    installed_wheel_digest,
    installed_wheel_report,
    mcp_instructions,
    published_release,
    source_commit,
    wheel_digest_from_direct_url,
)
from synapse_channel.cli import main
from synapse_channel.cli_doctor import _cmd_doctor
from synapse_channel.client.diagnostics import Diagnosis

ROOT = Path(__file__).resolve().parents[1]


def _checkout_is_unreleased() -> tuple[str, bool]:
    """Return the checkout's dated release and whether this tree is newer than it."""
    released, notes = changelog_release((ROOT / "CHANGELOG.md").read_text(encoding="utf-8"))
    return released, notes or __version__ != released


def test_version_entry_prints_commit_profile_and_unreleased_mark(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """``synapse --version`` prints the live checkout and the mark its changelog implies.

    A development tree prints the mark; a release cut, whose version equals the
    latest dated heading and whose ``[Unreleased]`` section is empty, prints none.
    """
    monkeypatch.setenv("SYNAPSE_NO_UPDATE_CHECK", "1")
    monkeypatch.delenv("SYNAPSE_QUALIFIED_PROFILE", raising=False)
    with pytest.raises(SystemExit):
        main(["--version"])
    text = capsys.readouterr().out
    commit = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert f"source commit: {commit}" in text
    assert "declared profile: loopback (package default)" in text
    released, unreleased = _checkout_is_unreleased()
    assert (f"published release {released}" in text) is unreleased
    assert (DEVELOPMENT_MARK in text) is unreleased
    assert "wheel digest:" in text


def test_a_real_dist_info_names_a_recorded_wheel_and_an_unrecorded_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dist-info directory, not this host's install, selects both wheel reports."""
    recorded = tmp_path / "recorded.dist-info"
    recorded.mkdir()
    (recorded / "direct_url.json").write_text(
        '{"archive_info": {"hashes": {"sha256": "abc123"}}}\n',
        encoding="utf-8",
    )
    (recorded / "WHEEL").write_text("Wheel-Version: 1.0\n", encoding="utf-8")
    unrecorded = tmp_path / "unrecorded.dist-info"
    unrecorded.mkdir()
    (unrecorded / "WHEEL").write_text("Wheel-Version: 1.0\n", encoding="utf-8")

    def lookup(path: Path) -> None:
        """Point wheel discovery at one dist-info directory."""

        def distribution(_name: str) -> importlib.metadata.Distribution:
            """Read one real dist-info directory."""
            return importlib.metadata.PathDistribution(path)

        monkeypatch.setattr(
            "synapse_channel.build_identity.importlib.metadata.distribution",
            distribution,
        )

    lookup(recorded)
    assert installed_wheel_report() == ("recorded", "abc123")
    recorded_lines = identity_lines(
        commit="abc",
        discover_wheel=True,
        profile="loopback",
        profile_source="package default",
    )
    assert "wheel digest: abc123" in recorded_lines
    lookup(unrecorded)
    assert installed_wheel_report() == ("digest_not_recorded", None)
    unrecorded_lines = identity_lines(
        commit="abc",
        discover_wheel=True,
        profile="loopback",
        profile_source="package default",
    )
    assert f"wheel digest: {WHEEL_NOT_RECORDED}" in unrecorded_lines


async def _quiet_diagnose(**_kwargs: object) -> tuple[int, list[str], list[Diagnosis]]:
    """Return one synthetic check without opening a hub."""
    return 0, ["hub: skipped"], []


def test_doctor_command_prints_the_same_identity(capsys: pytest.CaptureFixture[str]) -> None:
    """``doctor`` leads with the build identity even when checks are supplied."""
    args = type(
        "Args",
        (),
        {
            "uri": "ws://127.0.0.1:9",
            "project": None,
            "id": None,
            "token": None,
            "send_name": None,
            "json": False,
            "mcp_config": None,
        },
    )()
    code = _cmd_doctor(args, diagnose_runner=_quiet_diagnose)
    assert code == 0
    text = capsys.readouterr().out
    expected = list(identity_lines())
    assert text.splitlines()[: len(expected)] == expected


def test_the_changelog_is_the_only_release_source() -> None:
    """Publishing moves the dated heading; the mark follows that heading."""
    released = published_release()
    assert released is not None
    assert _checkout_is_unreleased()[0] == released
    development = (
        f"this build is newer than the published release {released}; "
        f"see CHANGELOG [Unreleased]; {DEVELOPMENT_MARK}",
    )
    assert development_mark_lines() == (development if _checkout_is_unreleased()[1] else ())
    quiet = "## [Unreleased]\n\n## [1.2.3] - 2026-01-01\n\n- shipped\n"
    assert changelog_release(quiet) == ("1.2.3", False)
    assert development_mark_lines("1.2.3", changelog=quiet) == ()
    assert development_mark_lines("1.2.2", changelog=quiet) == ()
    assert "published release 1.2.3" in development_mark_lines("1.2.4", changelog=quiet)[0]
    headings = "## [Unreleased]\n\n### Added\n\n## [9.0.0] - 2026-01-01\n"
    assert changelog_release(headings) == ("9.0.0", False)
    with pytest.raises(ValueError, match="not numeric"):
        development_mark_lines("nope", changelog=quiet)
    with pytest.raises(ValueError, match="no dated release"):
        changelog_release("# Changelog\n")


def test_a_file_url_is_not_hashed(tmp_path: Path) -> None:
    """direct_url.json contributes a digest only from recorded archive_info."""
    wheel = tmp_path / "synapse_channel-0.99.39-py3-none-any.whl"
    wheel.write_bytes(b"these bytes must not be hashed")
    assert wheel_digest_from_direct_url(json.dumps({"url": wheel.as_uri()})) is None
    recorded = wheel_digest_from_direct_url(
        json.dumps(
            {
                "url": wheel.as_uri(),
                "archive_info": {"hashes": {"sha256": "abc"}},
            }
        )
    )
    assert recorded == "abc"
    assert wheel_digest_from_direct_url("{") is None


def test_unknown_profile_stays_on_the_package_default(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Only the four named profiles select; anything else is the package default."""
    monkeypatch.setenv("SYNAPSE_QUALIFIED_PROFILE", "not-a-profile")
    assert declared_profile() == ("loopback", "package default")
    monkeypatch.setenv("SYNAPSE_QUALIFIED_PROFILE", "team-secure")
    assert declared_profile() == ("team-secure", "environment")
    assert "declared profile: team-secure (environment)" in "\n".join(identity_lines())
    with pytest.raises(ValueError, match="declare both"):
        identity_mapping(commit="abc", discover_wheel=False, profile="paranoid")
    with pytest.raises(ValueError, match="declare both"):
        identity_mapping(commit="abc", discover_wheel=False, profile_source="environment")
    with pytest.raises(ValueError, match="declare both"):
        identity_mapping(
            commit="abc",
            discover_wheel=False,
            profile="loopback",
            profile_source="receipt",
        )
    mapping = identity_mapping(
        commit="abc",
        discover_wheel=False,
        profile="paranoid",
        profile_source="environment",
    )
    assert mapping["declared_profile"] == "paranoid"
    assert mapping["profile_source"] == "environment"
    assert "qualified_profile" not in mapping
    assert mapping["source_commit"] == "abc"
    args = type(
        "Args",
        (),
        {
            "uri": "ws://127.0.0.1:9",
            "project": None,
            "id": None,
            "token": None,
            "send_name": None,
            "json": True,
            "mcp_config": None,
        },
    )()
    assert _cmd_doctor(args, diagnose_runner=_quiet_diagnose) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["build"]["declared_profile"] == "team-secure"
    assert payload["build"]["profile_source"] == "environment"
    assert "qualified_profile" not in payload["build"]


def test_agent_card_omits_marks_when_the_build_is_the_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A release build adds no development marks, and skill metadata stays exact."""
    monkeypatch.setattr("synapse_channel.a2a.development_mark_lines", lambda: ())
    card = agent_card_from_manifest(
        [
            {"agent": "plain", "description": "listed", "contracts": ["not-a-contract"]},
            {
                "agent": "checked",
                "description": "listed",
                "verification": {"result": "match", "ignored": "drop-me"},
            },
        ],
        endpoint_url="http://127.0.0.1:9/a2a",
    )
    assert "developmentMarks" not in card
    assert "metadata" not in card["skills"][0]
    recorded = card["skills"][1]["metadata"]["synapse"]["capabilityCardVerification"]
    assert recorded == {"result": "match"}


def test_public_a2a_card_keeps_marks_and_omits_build_secrets() -> None:
    """The unauthenticated card mirrors the mark and omits the commit, wheel or profile."""
    card = agent_card_from_manifest([], endpoint_url="http://127.0.0.1:9/a2a")
    marks = list(development_mark_lines())
    assert card.get("developmentMarks", []) == marks
    assert bool(marks) is _checkout_is_unreleased()[1]
    assert "source_commit" not in card
    assert "wheel_digest" not in card
    assert "qualified_profile" not in card
    assert "declared_profile" not in card
    assert "profile_source" not in card
    assert "build" not in card
    assert (DEVELOPMENT_MARK in mcp_instructions()) is bool(marks)
    assert "declared profile:" in mcp_instructions()
    assert source_commit(ROOT / "missing-root") == "unknown"


def test_an_install_inside_another_git_repo_is_not_that_head(tmp_path: Path) -> None:
    """A prefix inside a git checkout does not report the enclosing HEAD."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    (repo / "pyproject.toml").write_text('[project]\nname = "synapse-channel"\n', encoding="utf-8")
    (repo / "README").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "pyproject.toml", "README"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.email=build-identity@example.com",
            "-c",
            "user.name=build-identity",
            "commit",
            "-q",
            "-m",
            "init",
        ],
        check=True,
    )
    install = repo / ".venv" / "lib" / "python3.12"
    install.mkdir(parents=True)
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert source_commit(install) == "unknown"
    assert source_commit(install) != head


def test_git_failures_on_the_real_checkout_are_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A source checkout that cannot answer git reports unknown."""

    def _explode(*_args: object, **_kwargs: object) -> object:
        """Raise ``OSError`` as a git launch failure."""
        raise OSError("git")

    monkeypatch.setattr("synapse_channel.build_identity.subprocess.run", _explode)
    assert source_commit(ROOT) == "unknown"

    def _timeout(*_args: object, **_kwargs: object) -> object:
        """Raise the timeout git would raise when it does not finish."""
        raise subprocess.TimeoutExpired(cmd="git", timeout=2)

    monkeypatch.setattr("synapse_channel.build_identity.subprocess.run", _timeout)
    assert source_commit(ROOT) == "unknown"

    class _Completed:
        def __init__(self, code: int, stdout: str) -> None:
            """Record one finished git process."""
            self.returncode = code
            self.stdout = stdout

    monkeypatch.setattr(
        "synapse_channel.build_identity.subprocess.run",
        lambda *_args, **_kwargs: _Completed(0, "\n"),
    )
    assert source_commit(ROOT) == "unknown"
    monkeypatch.setattr(
        "synapse_channel.build_identity.subprocess.run",
        lambda *_args, **_kwargs: _Completed(1, ""),
    )
    assert source_commit(ROOT) == "unknown"


def test_a_pyproject_that_is_not_this_project_is_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The name has to be the project table of this distribution."""

    def _other_name(self: Path, encoding: str | None = None, errors: str | None = None) -> str:
        """Return a project table that names a different distribution."""
        del encoding, errors
        assert self.name == "pyproject.toml"
        return '[project]\nname = "other"\n'

    monkeypatch.setattr(Path, "read_text", _other_name)
    assert source_commit(ROOT) == "unknown"

    def _unreadable(self: Path, encoding: str | None = None, errors: str | None = None) -> str:
        """Raise ``OSError`` when the project table cannot be read."""
        del encoding, errors
        assert self.name == "pyproject.toml"
        raise OSError("unreadable")

    monkeypatch.setattr(Path, "read_text", _unreadable)
    assert source_commit(ROOT) == "unknown"

    def _name_outside_table(
        self: Path, encoding: str | None = None, errors: str | None = None
    ) -> str:
        """Return the distribution name outside the project table."""
        del encoding, errors
        assert self.name == "pyproject.toml"
        return 'name = "synapse-channel"\n'

    monkeypatch.setattr(Path, "read_text", _name_outside_table)
    assert source_commit(ROOT) == "unknown"

    def _name_after_another_field(
        self: Path, encoding: str | None = None, errors: str | None = None
    ) -> str:
        """Return the distribution name after another project field."""
        del encoding, errors
        assert self.name == "pyproject.toml"
        return '[project]\ndescription = "x"\nname = "synapse-channel"\n'

    monkeypatch.setattr(Path, "read_text", _name_after_another_field)
    assert source_commit(ROOT) != "unknown"


def test_identity_rejects_bad_metadata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Broken versions and direct URLs stay explicit."""
    with pytest.raises(ValueError, match="not numeric"):
        development_mark_lines("nope", changelog="## [Unreleased]\n\n## [1.0.0] - 2026-01-01\n")
    assert wheel_digest_from_direct_url("[]") is None
    assert wheel_digest_from_direct_url('{"archive_info": {"hashes": {"sha256": 1}}}') is None
    assert (
        wheel_digest_from_direct_url(
            '{"archive_info": {"hashes": "no"}, "url": "http://example/pkg.whl"}'
        )
        is None
    )
    assert wheel_digest_from_direct_url('{"url": "http://example/pkg.whl"}') is None
    missing = tmp_path / "missing.whl"
    assert wheel_digest_from_direct_url(json.dumps({"url": missing.as_uri()})) is None
    assert wheel_digest_from_direct_url('{"archive_info": {"hashes": {"sha256": "abc"}}}') == "abc"
    assert wheel_digest_from_direct_url('{"archive_info": {"hashes": {"sha256": ""}}}') is None
    assert installed_wheel_digest("not-a-real-distribution") is None
    assert installed_wheel_report("not-a-real-distribution") == ("not_a_wheel", None)

    class _Dist:
        def __init__(self, direct: str | None, wheel: str | None) -> None:
            """Hold the direct-url and wheel metadata for one fake distribution."""
            self.direct = direct
            self.wheel = wheel

        def read_text(self, name: str) -> str | None:
            """Return the requested distribution metadata text."""
            if name == "direct_url.json":
                return self.direct
            return self.wheel

    monkeypatch.setattr(
        "synapse_channel.build_identity.importlib.metadata.distribution",
        lambda _name: _Dist(
            '{"archive_info": {"hashes": {"sha256": "deadbeef"}}}', "Wheel-Version: 1.0\n"
        ),
    )
    assert installed_wheel_report() == ("recorded", "deadbeef")
    monkeypatch.setattr(
        "synapse_channel.build_identity.importlib.metadata.distribution",
        lambda _name: _Dist('{"url": "file:///tmp/pkg.whl"}', "Wheel-Version: 1.0\n"),
    )
    assert installed_wheel_report() == ("digest_not_recorded", None)
    recorded_line = identity_lines(
        commit="abc",
        discover_wheel=True,
        profile="loopback",
        profile_source="package default",
    )
    assert f"wheel digest: {WHEEL_NOT_RECORDED}" in recorded_line
    editable = '{"url": "file:///src/synapse-channel", "dir_info": {"editable": true}}'
    monkeypatch.setattr(
        "synapse_channel.build_identity.importlib.metadata.distribution",
        lambda _name: _Dist(editable, "Wheel-Version: 1.0\n"),
    )
    assert installed_wheel_report() == ("not_a_wheel", None)
    editable_line = identity_lines(
        commit="abc",
        discover_wheel=True,
        profile="loopback",
        profile_source="package default",
    )
    assert "wheel digest: not a wheel" in editable_line
    directory = '{"url": "file:///src/synapse-channel", "dir_info": {"editable": false}}'
    monkeypatch.setattr(
        "synapse_channel.build_identity.importlib.metadata.distribution",
        lambda _name: _Dist(directory, "Wheel-Version: 1.0\n"),
    )
    assert installed_wheel_report() == ("not_a_wheel", None)
    for document in ("not-json", "[]", '{"dir_info": "no"}'):
        monkeypatch.setattr(
            "synapse_channel.build_identity.importlib.metadata.distribution",
            lambda _name, document=document: _Dist(document, "Wheel-Version: 1.0\n"),
        )
        assert installed_wheel_report() == ("digest_not_recorded", None)
    monkeypatch.setattr(
        "synapse_channel.build_identity.importlib.metadata.distribution",
        lambda _name: _Dist(None, "Wheel-Version: 1.0\n"),
    )
    assert installed_wheel_report() == ("digest_not_recorded", None)
    monkeypatch.setattr(
        "synapse_channel.build_identity.importlib.metadata.distribution",
        lambda _name: _Dist(None, None),
    )
    assert installed_wheel_report() == ("not_a_wheel", None)
    assert development_mark_lines(root=tmp_path) == ()
    assert published_release(tmp_path) is None
    real_read = Path.read_text

    def _hide_changelog(self: Path, encoding: str | None = None, errors: str | None = None) -> str:
        """Raise ``OSError`` for the changelog and read every other file for real."""
        if self.name == "CHANGELOG.md":
            raise OSError("unreadable")
        return real_read(self, encoding=encoding, errors=errors)

    monkeypatch.setattr(Path, "read_text", _hide_changelog)
    assert published_release(ROOT) is None
    assert development_mark_lines(root=ROOT) == ()
    supplied = identity_lines(
        commit="abc",
        wheel="abc",
        discover_wheel=False,
        profile="loopback",
        profile_source="package default",
    )
    assert "wheel digest: abc" in supplied

    class _EmptyDist:
        def read_text(self, name: str) -> None:
            """Return no metadata for any distribution file."""
            del name
            return None

    monkeypatch.setattr(
        "synapse_channel.build_identity.importlib.metadata.distribution",
        lambda _name: _EmptyDist(),
    )
    assert installed_wheel_digest() is None
    monkeypatch.setattr(
        "synapse_channel.build_identity.identity_mapping",
        lambda **_kwargs: {
            "version": "0.99.39",
            "source_commit": "abc",
            "wheel_digest": None,
            "declared_profile": "loopback",
            "profile_source": "package default",
            "development_marks": "nope",
        },
    )
    with pytest.raises(TypeError, match="development marks"):
        identity_lines()


def test_docs_name_the_unreleased_execution_outcomes() -> None:
    """The build-identity doc does not present the development feature as the release."""
    document = (ROOT / "docs" / "build-identity.md").read_text(encoding="utf-8")
    assert DEVELOPMENT_MARK in document
    assert "pip install synapse-channel" in document
    assert "declared profile: loopback (package default)" in document
    assert "An installed wheel prints no development mark." in document
    assert "A wheel does not ship that changelog" in document
