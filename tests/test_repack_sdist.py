# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — deterministic sdist repacker tests
from __future__ import annotations

import gzip
import importlib.util
import io
import sys
import tarfile
from pathlib import Path
from types import ModuleType
from typing import cast

import pytest

ROOT = Path(__file__).parents[1]
EPOCH = 1_700_000_000


def _load_tool(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


TOOL = _load_tool("repack_sdist")
SdistRepackError = TOOL.SdistRepackError
repack_sdist = TOOL.repack_sdist


def _archive(
    path: Path,
    members: list[tuple[str, bytes | None, bytes]],
    *,
    mtime: int,
    uid: int,
) -> None:
    with path.open("wb") as raw:
        with gzip.GzipFile(filename="source-name", mode="wb", fileobj=raw, mtime=mtime) as zipped:
            with tarfile.open(fileobj=zipped, mode="w|", format=tarfile.PAX_FORMAT) as archive:
                for name, payload, member_type in members:
                    info = tarfile.TarInfo(name)
                    info.type = member_type
                    info.size = len(payload) if payload is not None else 0
                    info.mode = 0o775 if member_type == tarfile.DIRTYPE else 0o664
                    info.uid = uid
                    info.gid = uid + 1
                    info.uname = f"user-{uid}"
                    info.gname = f"group-{uid}"
                    info.mtime = mtime
                    archive.addfile(info, io.BytesIO(payload) if payload is not None else None)


def test_repack_is_byte_reproducible_and_normalizes_metadata(tmp_path: Path) -> None:
    first = tmp_path / "first.tar.gz"
    second = tmp_path / "second.tar.gz"
    members = [
        ("package/", None, tarfile.DIRTYPE),
        ("package/b.txt", b"bravo", tarfile.REGTYPE),
        ("package/a.txt", b"alpha", tarfile.REGTYPE),
    ]
    _archive(first, members, mtime=10, uid=1000)
    _archive(second, list(reversed(members)), mtime=20, uid=2000)
    first_output = tmp_path / "normalized-first.tar.gz"
    second_output = tmp_path / "normalized-second.tar.gz"

    repack_sdist(first, first_output, source_date_epoch=EPOCH)
    repack_sdist(second, second_output, source_date_epoch=EPOCH)

    assert first_output.read_bytes() == second_output.read_bytes()
    assert int.from_bytes(first_output.read_bytes()[4:8], "little") == EPOCH
    assert first_output.stat().st_mode & 0o777 == 0o644
    with tarfile.open(first_output, mode="r:gz") as archive:
        members_by_name = {member.name: member for member in archive.getmembers()}
        assert list(members_by_name) == ["package", "package/a.txt", "package/b.txt"]
        assert archive.extractfile("package/a.txt").read() == b"alpha"  # type: ignore[union-attr]
        for member in members_by_name.values():
            assert (member.uid, member.gid, member.uname, member.gname, member.mtime) == (
                0,
                0,
                "",
                "",
                EPOCH,
            )
        assert members_by_name["package"].mode == 0o755
        assert members_by_name["package/a.txt"].mode == 0o644


@pytest.mark.parametrize(
    ("members", "message"),
    [
        ([], "must not be empty"),
        ([("/absolute", b"x", tarfile.REGTYPE)], "unsafe member"),
        ([("./package/file", b"x", tarfile.REGTYPE)], "unsafe member"),
        ([("package/./file", b"x", tarfile.REGTYPE)], "unsafe member"),
        ([("package//file", b"x", tarfile.REGTYPE)], "unsafe member"),
        ([("package/../escape", b"x", tarfile.REGTYPE)], "unsafe member"),
        ([("package\\escape", b"x", tarfile.REGTYPE)], "unsafe member"),
        ([("package/file.", b"x", tarfile.REGTYPE)], "non-portable member"),
        ([("package/file ", b"x", tarfile.REGTYPE)], "non-portable member"),
        ([("package/a:b", b"x", tarfile.REGTYPE)], "non-portable member"),
        ([("package/control\x01", b"x", tarfile.REGTYPE)], "non-portable member"),
        ([("package/CON", b"x", tarfile.REGTYPE)], "non-portable member"),
        ([("package/aux.txt", b"x", tarfile.REGTYPE)], "non-portable member"),
        ([("package/COM¹.log", b"x", tarfile.REGTYPE)], "non-portable member"),
        (
            [
                ("package/file", b"canonical", tarfile.REGTYPE),
                ("package/./file", b"collision", tarfile.REGTYPE),
            ],
            "duplicate member",
        ),
        (
            [
                ("package/File.py", b"upper", tarfile.REGTYPE),
                ("package/file.py", b"lower", tarfile.REGTYPE),
            ],
            "duplicate member",
        ),
        (
            [
                ("package/café.py", b"nfc", tarfile.REGTYPE),
                ("package/cafe\u0301.py", b"nfd", tarfile.REGTYPE),
            ],
            "duplicate member",
        ),
        (
            [
                ("package/file", b"one", tarfile.REGTYPE),
                ("package/file", b"two", tarfile.REGTYPE),
            ],
            "duplicate member",
        ),
        (
            [
                ("package/file", b"parent", tarfile.REGTYPE),
                ("package/file/child", b"child", tarfile.REGTYPE),
            ],
            "conflicting member",
        ),
        (
            [
                ("package/file/child", b"child", tarfile.REGTYPE),
                ("package/file", b"parent", tarfile.REGTYPE),
            ],
            "conflicting member",
        ),
        ([("package/link", None, tarfile.SYMTYPE)], "non-file member"),
    ],
)
def test_repack_refuses_unsafe_members(
    tmp_path: Path,
    members: list[tuple[str, bytes | None, bytes]],
    message: str,
) -> None:
    source = tmp_path / "source.tar.gz"
    _archive(source, members, mtime=10, uid=1000)
    output = tmp_path / "output.tar.gz"

    with pytest.raises(SdistRepackError, match=message):
        repack_sdist(source, output, source_date_epoch=EPOCH)

    assert not output.exists()


def test_repack_preserves_one_exact_portable_unicode_spelling(tmp_path: Path) -> None:
    source = tmp_path / "source.tar.gz"
    decomposed_name = "package/cafe\u0301.py"
    _archive(source, [(decomposed_name, b"data", tarfile.REGTYPE)], mtime=10, uid=1000)
    output = tmp_path / "output.tar.gz"

    repack_sdist(source, output, source_date_epoch=EPOCH)

    with tarfile.open(output, mode="r:gz") as archive:
        assert archive.getnames() == [decomposed_name]


def test_repack_refuses_invalid_paths_epoch_and_existing_output(tmp_path: Path) -> None:
    source = tmp_path / "source.tar.gz"
    _archive(source, [("package/file", b"data", tarfile.REGTYPE)], mtime=10, uid=1000)
    link = tmp_path / "source-link.tar.gz"
    link.symlink_to(source)

    with pytest.raises(SdistRepackError, match="non-negative"):
        repack_sdist(source, tmp_path / "negative.tar.gz", source_date_epoch=-1)
    with pytest.raises(SdistRepackError, match="regular non-symlink"):
        repack_sdist(link, tmp_path / "link-output.tar.gz", source_date_epoch=EPOCH)
    with pytest.raises(SdistRepackError, match="paths must differ"):
        repack_sdist(source, source, source_date_epoch=EPOCH)

    output = tmp_path / "existing.tar.gz"
    output.write_bytes(b"preserve")
    with pytest.raises(FileExistsError):
        repack_sdist(source, output, source_date_epoch=EPOCH)
    assert output.read_bytes() == b"preserve"


def test_repack_cleans_partial_output_on_archive_write_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.tar.gz"
    _archive(source, [("package/file", b"data", tarfile.REGTYPE)], mtime=10, uid=1000)
    output = tmp_path / "output.tar.gz"
    real_open = TOOL.tarfile.open

    def _fail_target(*args: object, **kwargs: object) -> tarfile.TarFile:
        if kwargs.get("fileobj") is not None:
            raise tarfile.TarError("synthetic write failure")
        return cast(tarfile.TarFile, real_open(*args, **kwargs))

    monkeypatch.setattr(TOOL.tarfile, "open", _fail_target)
    with pytest.raises(tarfile.TarError, match="synthetic"):
        repack_sdist(source, output, source_date_epoch=EPOCH)
    assert not output.exists()


def test_repack_refuses_unreadable_regular_member(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.tar.gz"
    _archive(source, [("package/file", b"data", tarfile.REGTYPE)], mtime=10, uid=1000)
    output = tmp_path / "output.tar.gz"
    monkeypatch.setattr(tarfile.TarFile, "extractfile", lambda *_args, **_kwargs: None)

    with pytest.raises(SdistRepackError, match="cannot read regular"):
        repack_sdist(source, output, source_date_epoch=EPOCH)
    assert not output.exists()


def test_cli_writes_output_and_reports_invalid_input(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "source.tar.gz"
    _archive(source, [("package/file", b"data", tarfile.REGTYPE)], mtime=10, uid=1000)
    output = tmp_path / "nested" / "output.tar.gz"

    assert (
        TOOL.main(
            [
                "--input",
                str(source),
                "--output",
                str(output),
                "--source-date-epoch",
                str(EPOCH),
            ]
        )
        == 0
    )
    assert output.is_file()

    with pytest.raises(SystemExit) as failure:
        TOOL.main(
            [
                "--input",
                str(tmp_path / "missing.tar.gz"),
                "--output",
                str(tmp_path / "missing-output.tar.gz"),
                "--source-date-epoch",
                str(EPOCH),
            ]
        )
    assert failure.value.code == 2
    assert "regular non-symlink" in capsys.readouterr().err
