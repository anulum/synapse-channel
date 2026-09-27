# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — published no-receipt hub compatibility
"""Run the current release CLI against the complete published 0.48.0 hub."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import shutil
import sqlite3
import sys
from collections.abc import Iterator
from io import BytesIO
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

import pytest

from cli_e2e_helpers import free_port, git_repo, run_cli

_WHEEL_NAME = "synapse_channel-0.48.0-py3-none-any.whl"
_WHEEL_SHA256 = "d4ee59fa6a32fd6830a3b45c86f206e847a31e2f2fb6c3a398300ce4f6357c01"


@pytest.fixture(scope="session")
def legacy_release_profile(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """Verify and unpack the complete published wheel without installing over the candidate."""
    root = Path(__file__).resolve().parent.parent
    wheel_dir = Path(
        os.environ.get("SYNAPSE_LEGACY_WHEEL_DIR", root / ".pytest_cache/legacy-release")
    )
    wheel = wheel_dir / _WHEEL_NAME
    assert wheel.is_file(), (
        "Prepare the historical hub fixture: python -m pip download --require-hashes "
        "--no-deps --only-binary=:all: -r .github/requirements/requirements-legacy-release.txt "
        "--dest .pytest_cache/legacy-release (or set SYNAPSE_LEGACY_WHEEL_DIR)"
    )
    payload = wheel.read_bytes()
    assert hashlib.sha256(payload).hexdigest() == _WHEEL_SHA256
    profile = tmp_path_factory.mktemp("legacy-release") / "site-packages"
    profile.mkdir()
    try:
        with ZipFile(BytesIO(payload)) as archive:
            for entry in archive.infolist():
                relative = PurePosixPath(entry.filename)
                assert not relative.is_absolute() and ".." not in relative.parts
                if entry.is_dir():
                    continue
                destination = profile.joinpath(*relative.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(archive.read(entry))
        yield profile
    finally:
        current = profile.parent.with_name("legacy-releasecurrent")
        if current.is_symlink() and current.resolve() == profile.parent.resolve():
            current.unlink()
        shutil.rmtree(profile.parent)


@pytest.mark.asyncio
@pytest.mark.parametrize("receipt_json", [False, True])
async def test_published_hub_without_receipts_confirms_current_cli_release(
    tmp_path: Path, legacy_release_profile: Path, receipt_json: bool
) -> None:
    """A genuine old hub grant confirms release without inventing verified evidence."""
    repo = git_repo(tmp_path / "repository")
    environment = dict(os.environ)
    environment.update(
        PYTHONPATH=str(legacy_release_profile),
        PYTHONIOENCODING="utf-8",
        SYN_HOME=str(tmp_path / "legacy-home"),
        SYNAPSE_TOKEN="",
        SYNAPSE_TOKEN_FILE="",
    )
    version = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import json,synapse_channel; from importlib.metadata import version; "
        "print(json.dumps([synapse_channel.__version__,version('synapse-channel'),"
        "synapse_channel.__file__]))",
        env=environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(version.communicate(), timeout=10)
    finally:
        if version.returncode is None:
            version.kill()
        await version.wait()
    assert version.returncode == 0, stderr.decode()
    reported_version, installed_version, module_path = json.loads(stdout)
    assert reported_version == installed_version == "0.48.0"
    assert Path(module_path).resolve().is_relative_to(legacy_release_profile.resolve())
    port = free_port()
    uri = f"ws://127.0.0.1:{port}"
    database = tmp_path / "legacy-hub.db"
    log_path = tmp_path / "legacy-hub.log"
    with log_path.open("wb") as log:
        hub = await asyncio.create_subprocess_exec(
            sys.executable,
            "-u",
            "-m",
            "synapse_channel.cli",
            "hub",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--db",
            str(database),
            env=environment,
            stdout=log,
            stderr=log,
        )
        try:
            deadline = asyncio.get_running_loop().time() + 10
            while True:
                assert hub.returncode is None, log_path.read_text(encoding="utf-8")
                try:
                    _reader, writer = await asyncio.open_connection("127.0.0.1", port)
                except OSError:
                    assert asyncio.get_running_loop().time() < deadline, log_path.read_text(
                        encoding="utf-8"
                    )
                    await asyncio.sleep(0.02)
                    continue
                writer.close()
                await writer.wait_closed()
                break
            claim = await asyncio.to_thread(
                run_cli,
                "git-claim",
                "legacy-edit",
                "--name",
                "legacy-owner",
                "--paths",
                "README.md",
                "--base",
                "HEAD",
                "--auto-release-on",
                "manual",
                uri=uri,
                cwd=repo,
                env={"SYNAPSE_TOKEN": "", "SYNAPSE_TOKEN_FILE": ""},
            )
            assert claim.ok(), claim.output
            result = await asyncio.to_thread(
                run_cli,
                "release",
                "legacy-edit",
                "--name",
                "legacy-owner",
                *(["--receipt-json"] if receipt_json else []),
                uri=uri,
                cwd=repo,
                env={"SYNAPSE_TOKEN": "", "SYNAPSE_TOKEN_FILE": ""},
            )
            assert result.ok(), result.output
            with contextlib.closing(
                sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
            ) as reader:
                releases = reader.execute(
                    "SELECT payload FROM events WHERE kind='release'"
                ).fetchall()
            assert [json.loads(row[0])["task_id"] for row in releases] == ["legacy-edit"]
            if receipt_json:
                receipt = json.loads(result.stdout)
                assert receipt["task_id"] == "legacy-edit"
                assert receipt["owner"] == "legacy-owner"
                assert receipt["released"] is True
                assert receipt["evidence"] == []
                assert receipt["epistemic_status"] == "unsupported"
            else:
                assert result.stdout.strip() == "released 'legacy-edit'"
        finally:
            if hub.returncode is None:
                hub.terminate()
            try:
                await asyncio.wait_for(hub.wait(), timeout=5)
            except asyncio.TimeoutError:
                hub.kill()
                await hub.wait()
