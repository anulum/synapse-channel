# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — pinned instrumentation wheel and complete runtime bytes
"""Read the collector's original wheel without installing or executing its contents."""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import os
import re
import stat
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from email.parser import BytesParser
from pathlib import Path, PurePosixPath
from types import MappingProxyType

from .capsule import read_relative_file, require_posix_descriptors
from .inventory import verify_directory_inventory
from .native_provenance import NativeWheelArtifact

_VERSION = "7.17.0"
_DIST = "coverage-" + _VERSION + ".dist-info/"
_TRACER = "coverage/tracer.cpython-312-x86_64-linux-gnu.so"
_TAGS = {
    "cp312-cp312-manylinux1_x86_64",
    "cp312-cp312-manylinux_2_28_x86_64",
    "cp312-cp312-manylinux_2_5_x86_64",
}
_MAX_ARCHIVE = 8 * 1024 * 1024
_MAX_EXPANDED = 32 * 1024 * 1024


@dataclass(frozen=True)
class InstrumentationWheel:
    """Detached original archive and its complete verified regular member set.

    Attributes
    ----------
    artifact:
        Raw wheel whose archive digest and size matched the supplied profile lock.
    members:
        Readonly detached bytes, including metadata, licences and RECORD itself.

    Notes
    -----
    This observation grants no execution or installation authority. The fixed
    preparer must bind these bytes into its independently reviewed capsule.
    """

    artifact: NativeWheelArtifact
    members: Mapping[str, bytes]

    def __post_init__(self) -> None:
        """Detach the mapping so callers cannot replace captured member bytes."""
        object.__setattr__(self, "members", MappingProxyType(dict(self.members)))


def _read(path: Path, maximum: int) -> bytes:
    require_posix_descriptors(os.name)
    parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        return read_relative_file(parent, path.name, maximum)
    finally:
        os.close(parent)


def _pin(lock: Path, filename: str) -> tuple[str, int]:
    record = json.loads(_read(lock, 65536))
    if (
        type(record) is not dict
        or record.get("schema") != "synapse.native-dependency-lock.v1"
        or record.get("native_abi") != "linux-x86_64-cpython312"
        or type(pin := record.get("coverage")) is not dict
        or pin.keys() != {"version", "filename", "sha256", "size_bytes", "source_url"}
        or pin.get("version") != _VERSION
        or pin.get("filename") != filename
        or type(digest := pin.get("sha256")) is not str
        or re.fullmatch("[0-9a-f]{64}", digest) is None
        or type(size := pin.get("size_bytes")) is not int
        or not 0 < size <= _MAX_ARCHIVE
    ):
        raise ValueError("instrumentation wheel lock is malformed or unsupported")
    return digest, size


def _require_member_path(logical: str) -> None:
    """Refuse noncanonical, control-bearing or escaping archive paths."""
    if (
        not logical
        or logical.startswith("/")
        or chr(92) in logical
        or len(logical.encode("utf-8")) > 4096
        or str(PurePosixPath(logical)) != logical
        or any(part in {"", ".", ".."} for part in logical.split("/"))
        or any(ord(c) < 32 or ord(c) == 127 for c in logical)
    ):
        raise ValueError("instrumentation wheel member scope differs")


def _member_name(name: str, *, directory: bool) -> None:
    """Require the canonical member to belong to this complete distribution."""
    logical = name[:-1] if directory else name
    _require_member_path(logical)
    if not (
        logical == "a1_coverage.pth"
        or logical == "coverage"
        or logical.startswith("coverage/")
        or logical == _DIST[:-1]
        or logical.startswith(_DIST)
    ):
        raise ValueError("instrumentation wheel member scope differs")


def _member_info(info: zipfile.ZipInfo) -> bool:
    """Validate one ZIP entry's fixed scope, type and expanded byte bound."""
    directory = info.is_dir()
    _member_name(info.filename, directory=directory)
    kind = stat.S_IFMT(info.external_attr >> 16)
    expected = stat.S_IFDIR if directory else stat.S_IFREG
    if kind not in {0, expected} or info.flag_bits & 1:
        raise ValueError("instrumentation wheel member type differs")
    if directory:
        if info.file_size:
            raise ValueError("instrumentation wheel directory contains bytes")
    elif not 0 <= info.file_size <= _MAX_ARCHIVE:
        raise ValueError("instrumentation wheel member byte bound exceeded")
    return directory


def _members(archive: zipfile.ZipFile) -> dict[str, bytes]:
    infos = archive.infolist()
    if not 0 < len(infos) <= 1024 or len({x.filename for x in infos}) != len(infos):
        raise ValueError("instrumentation wheel member inventory differs")
    if sum(x.file_size for x in infos) > _MAX_EXPANDED:
        raise ValueError("instrumentation wheel expanded byte bound exceeded")
    content = {}
    directories = []
    for info in infos:
        if _member_info(info):
            directories.append(info.filename)
        else:
            content[info.filename] = archive.read(info)
    if any(str(parent) in content for name in content for parent in PurePosixPath(name).parents):
        raise ValueError("instrumentation wheel file shadows a member directory")
    if any(not any(name.startswith(prefix) for name in content) for prefix in directories):
        raise ValueError("instrumentation wheel has an unrecorded empty directory")
    return content


def _verify_record(content: Mapping[str, bytes]) -> None:
    name = _DIST + "RECORD"
    if name not in content:
        raise ValueError("instrumentation wheel RECORD is missing")
    rows = list(csv.reader(io.StringIO(content[name].decode("utf-8"))))
    if (
        any(len(row) != 3 for row in rows)
        or len(rows) != len(content)
        or {row[0] for row in rows} != set(content)
    ):
        raise ValueError("instrumentation wheel RECORD inventory differs")
    for member, digest, size in rows:
        if member == name:
            if digest or size:
                raise ValueError("instrumentation wheel RECORD self-entry differs")
            continue
        raw = content[member]
        expected = (
            base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b"=").decode("ascii")
        )
        if digest != "sha256=" + expected or size != str(len(raw)):
            raise ValueError("instrumentation wheel RECORD member differs")


def _verify_metadata(content: Mapping[str, bytes]) -> None:
    required = {
        _DIST + "METADATA",
        _DIST + "WHEEL",
        _DIST + "licenses/LICENSE.txt",
        _DIST + "licenses/NOTICE.txt",
        _TRACER,
        "coverage/__init__.py",
    }
    if not required <= content.keys():
        raise ValueError("instrumentation wheel metadata, licence or tracer is missing")
    metadata = BytesParser().parsebytes(content[_DIST + "METADATA"])
    tags = BytesParser().parsebytes(content[_DIST + "WHEEL"])
    if (
        metadata.get_all("Name") != ["coverage"]
        or metadata.get_all("Version") != [_VERSION]
        or tags.get_all("Root-Is-Purelib") != ["false"]
        or set(tags.get_all("Tag", [])) != _TAGS
        or {name for name in content if name.endswith(".so")} != {_TRACER}
        or not content[_TRACER].startswith(b"\x7fELF")
    ):
        raise ValueError("instrumentation wheel distribution or native ABI differs")


def capture_instrumentation_wheel(lock: Path, wheel: Path) -> InstrumentationWheel:
    """Capture the exact collector archive and verify every regular member.

    Parameters
    ----------
    lock:
        Source-controlled native profile lock containing the explicit coverage pin.
    wheel:
        Already acquired local wheel. No network, installation or import occurs.

    Returns
    -------
    InstrumentationWheel
        Detached archive and complete RECORD-verified runtime member bytes.

    Raises
    ------
    ValueError, OSError, zipfile.BadZipFile
        On malformed profile metadata, changed archive, missing or changed
        RECORD members, unsafe entries or unsupported native ABI.

    Notes
    -----
    A caller-supplied lock is ordinary input, not an execution grant. The
    preparer binds its fixed source lock and captured bytes into the reviewed
    capsule. Retaining a .pth member never authorises site processing.
    """
    digest, size = _pin(lock, wheel.name)
    raw = _read(wheel, _MAX_ARCHIVE)
    if len(raw) != size or hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError("instrumentation wheel differs from its artifact pin")
    return decode_instrumentation_wheel(NativeWheelArtifact(wheel.name, digest, raw))


def decode_instrumentation_wheel(artifact: NativeWheelArtifact) -> InstrumentationWheel:
    """Decode an already captured archive without installing or executing it.

    Parameters
    ----------
    artifact:
        Detached original bytes and independently retained artifact digest.

    Returns
    -------
    InstrumentationWheel
        Complete bounded regular payloads after archive, RECORD and ABI checks.

    Raises
    ------
    ValueError, zipfile.BadZipFile
        If the digest, bounds or complete distribution inventory differ.

    Notes
    -----
    This is byte verification, not an execution grant. The enclosing capsule
    and fixed source-controlled lock must establish the independent digest.
    """
    if (
        not 0 < len(artifact.content) <= _MAX_ARCHIVE
        or hashlib.sha256(artifact.content).hexdigest() != artifact.sha256
    ):
        raise ValueError("captured instrumentation archive differs from artifact pin")
    with zipfile.ZipFile(io.BytesIO(artifact.content)) as archive:
        content = _members(archive)
    _verify_record(content)
    _verify_metadata(content)
    return InstrumentationWheel(artifact, content)


def instrumentation_runtime_content(wheel: InstrumentationWheel) -> dict[str, bytes]:
    """Place the verified collector bytes in an explicit captured runtime.

    Parameters
    ----------
    wheel:
        Complete result of capture_instrumentation_wheel for the fixed profile.

    Returns
    -------
    dict[str, bytes]
        Original archive and every regular distribution member under runtime/.

    Notes
    -----
    This performs no writes or imports and does not select the profile. The
    instrumented launcher must retain -I -S and explicit API startup; even the
    captured .pth remains inert data.
    """
    prefix = "runtime/python/lib/python3.12/dist-packages/"
    return {
        **{prefix + name: raw for name, raw in wheel.members.items()},
        "runtime/artifacts/" + wheel.artifact.filename: wheel.artifact.content,
    }


def verify_instrumentation_tree(root: Path, wheel: InstrumentationWheel) -> None:
    """Verify a detached distribution tree against every original wheel member.

    Parameters
    ----------
    root:
        Ordinary tree containing exactly the original regular member paths.
    wheel:
        Complete previously admitted original archive.

    Raises
    ------
    ValueError, OSError
        On changed bytes, linked files or an incomplete/different topology.

    Notes
    -----
    This is unprivileged preparation input validation, not root custody proof.
    No source module or site hook is executed.
    """
    require_posix_descriptors(os.name)
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        verify_directory_inventory(descriptor, frozenset(wheel.members))
        for name, expected in wheel.members.items():
            raw = read_relative_file(descriptor, name, _MAX_ARCHIVE, require_single_link=True)
            if raw != expected:
                raise ValueError("instrumentation source tree differs from original wheel")
    finally:
        os.close(descriptor)


def verify_instrumentation_native_file(
    source: Path, member: str, captured: bytes, wheel: InstrumentationWheel
) -> dict[str, object]:
    """Compare observed tracer bytes with both source and complete original RECORD.

    Parameters
    ----------
    source, member:
        Actual observed native source and its exact collector wheel member name.
    captured:
        Detached native payload bytes proposed for the new runtime.
    wheel:
        Complete original archive already verified against the fixed source lock.

    Returns
    -------
    dict[str, object]
        Exact original wheel and tracer provenance, without an execution grant.

    Raises
    ------
    ValueError, OSError
        For a foreign member, changed source or changed captured bytes.
    """
    if member != _TRACER:
        raise ValueError("instrumentation native member is outside the fixed tracer")
    if captured != _read(source, _MAX_ARCHIVE):
        raise ValueError("captured instrumentation native differs from observed source")
    if captured != wheel.members[_TRACER]:
        raise ValueError("captured instrumentation native differs from original wheel")
    digest = hashlib.sha256(captured).digest()
    return {
        "installed_source": str(source),
        "distribution": "coverage",
        "version": _VERSION,
        "wheel_filename": wheel.artifact.filename,
        "wheel_sha256": wheel.artifact.sha256,
        "wheel_record_hash": "sha256="
        + base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii"),
        "wheel_record_verified": True,
        "sha256": digest.hex(),
        "size_bytes": len(captured),
    }
