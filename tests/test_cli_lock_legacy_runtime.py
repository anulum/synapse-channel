# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — published no-receipt hub compatibility
"""Refuse manual mutations on complete published hubs without exact confirmation."""

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

_WHEELS = {
    "0.48.0": "d4ee59fa6a32fd6830a3b45c86f206e847a31e2f2fb6c3a398300ce4f6357c01",
    "0.99.27": "bf444c5537e98c97dfae63c4d80931f6a0798cb44e8e52f3db57dce776f8ed52",
}


@pytest.fixture(scope="session")
def legacy_release_profile(
    tmp_path_factory: pytest.TempPathFactory, request: pytest.FixtureRequest
) -> Iterator[Path]:
    """Verify and unpack the complete published wheel without installing over the candidate."""
    root = Path(__file__).resolve().parent.parent
    wheel_dir = Path(
        os.environ.get("SYNAPSE_LEGACY_WHEEL_DIR", root / ".pytest_cache/legacy-release")
    )
    version = str(getattr(request, "param", "0.48.0"))
    wheel = wheel_dir / f"synapse_channel-{version}-py3-none-any.whl"
    assert wheel.is_file(), (
        "Prepare the historical hub fixture: python -m pip download --require-hashes "
        "--no-deps --only-binary=:all: -r .github/requirements/requirements-legacy-release.txt "
        "--dest .pytest_cache/legacy-release; also prepare requirements-release-admission.txt "
        "(or set SYNAPSE_LEGACY_WHEEL_DIR)"
    )
    payload = wheel.read_bytes()
    assert hashlib.sha256(payload).hexdigest() == _WHEELS[version]
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
@pytest.mark.parametrize("legacy_release_profile", tuple(_WHEELS), indirect=True)
@pytest.mark.parametrize("receipt_json", [False, True])
async def test_published_hub_without_confirmation_refuses_manual_release(
    tmp_path: Path, legacy_release_profile: Path, receipt_json: bool
) -> None:
    """A real old server keeps the durable claim when exact support is absent."""
    profile = legacy_release_profile
    expected_version = (
        next(profile.glob("synapse_channel-*.dist-info")).name.split("-")[1].removesuffix(".dist")
    )
    repo = git_repo(tmp_path / "repository")
    environment = dict(os.environ)
    environment.update(
        PYTHONPATH=str(profile),
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
    assert reported_version == installed_version == expected_version
    assert Path(module_path).resolve().is_relative_to(profile.resolve())
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
            *(
                ["--identity-pins", str(tmp_path / "pins.json")]
                if expected_version == "0.99.27"
                else []
            ),
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
            if expected_version == "0.99.27":
                owned = await asyncio.to_thread(
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
                    "--reply-timeout",
                    "1",
                    uri=uri,
                    cwd=repo,
                    env={"SYNAPSE_TOKEN": "", "SYNAPSE_TOKEN_FILE": ""},
                )
                assert owned.ok(), owned.output
            else:
                claimant = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-m",
                    "synapse_channel.cli",
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
                    "--uri",
                    uri,
                    cwd=repo,
                    env=environment,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                try:
                    claimed_out, claimed_err = await asyncio.wait_for(claimant.communicate(), 10)
                finally:
                    if claimant.returncode is None:
                        claimant.kill()
                    await claimant.wait()
                assert claimant.returncode == 0, (claimed_out + claimed_err).decode()
            result = await asyncio.to_thread(
                run_cli,
                "release",
                "legacy-edit",
                "--name",
                "legacy-owner",
                *(["--receipt-json"] if receipt_json else []),
                "--reply-timeout",
                "0.2",
                uri=uri,
                cwd=repo,
                env={"SYNAPSE_TOKEN": "", "SYNAPSE_TOKEN_FILE": ""},
            )
            assert result.returncode == 1, result.output
            assert "no release sent" in result.stdout
            with contextlib.closing(
                sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
            ) as reader:
                releases = reader.execute(
                    "SELECT payload FROM events WHERE kind='release'"
                ).fetchall()
                claims = reader.execute("SELECT payload FROM events WHERE kind='claim'").fetchall()
            assert not releases
            hook = await asyncio.to_thread(
                run_cli,
                "lock",
                "legacy-hook",
                "--name",
                "hook-owner",
                "--",
                sys.executable,
                "-c",
                "print('hook command ran')",
                uri=uri,
                cwd=repo,
                env={"SYNAPSE_TOKEN": "", "SYNAPSE_TOKEN_FILE": ""},
            )
            assert hook.ok(), hook.output
            assert "hook command ran" in hook.stdout
            with contextlib.closing(
                sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
            ) as reader:
                hook_releases = reader.execute(
                    "SELECT payload FROM events WHERE kind='release'"
                ).fetchall()
            assert [json.loads(row[0])["task_id"] for row in hook_releases] == ["legacy-hook"]
            assert [json.loads(row[0])["task_id"] for row in claims] == ["legacy-edit"]
            from hub_e2e_helpers import close_agents, connect_agent
            from synapse_channel.core.protocol import MessageType

            observer = await connect_agent("legacy-observer", uri)
            try:
                await observer.agent.request_state()
                state = await observer.recorder.wait_for(
                    lambda data: data.get("type") == MessageType.STATE_SNAPSHOT
                )
                assert any(
                    row["task_id"] == "legacy-edit" and row["owner"] == "legacy-owner"
                    for row in state["snapshot"]["active_claims"]
                )
            finally:
                await close_agents(observer)
        finally:
            if hub.returncode is None:
                hub.terminate()
            try:
                await asyncio.wait_for(hub.wait(), timeout=5)
            except asyncio.TimeoutError:
                hub.kill()
                await hub.wait()
