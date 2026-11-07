# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — observed native dependency closure for qualification
"""Observe actual probe imports and refuse changed or unnecessary native payloads."""

from __future__ import annotations

import json
import platform
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .native_provenance import (
    load_native_wheel,
    run_native_metadata_command,
    verify_dpkg_native_file,
    verify_wheel_native_file,
)

_ENTRYPOINTS = (
    "tools.protected_runtime_probe.bootstrap",
    "tools.protected_runtime_probe.controller",
    "tools.protected_runtime_probe.authority_child",
    "tools.protected_runtime_probe.namespace_child",
)
# The frame helper imports fcntl after its entry module loads. Account
# resolution uses local files; external useradd/setfacl processes are separate.
_DEFERRED_IMPORTS = ("fcntl",)
# The instrumented recorder loads its native file-budget adapter at first use.
_INSTRUMENTED_DEFERRED_IMPORTS = ("resource",)
_INSTRUMENTED_ENTRYPOINTS = (
    "tools.protected_runtime_probe.instrumentation",
    "tools.protected_runtime_probe.instrumentation_recording",
    "tools.protected_runtime_probe.instrumentation_launch",
    "tools.protected_runtime_probe.instrumentation_outputs",
    "tools.protected_runtime_probe.instrumentation_retention",
    "tools.protected_runtime_probe.instrumentation_campaign",
)
_OBSERVE = """import importlib,json,sys
sys.path[:0]=[sys.argv[1],sys.argv[1]+'/src',sys.argv[2]]
names=json.loads(sys.argv[3])
for name in names: importlib.import_module(name)
if sys.argv[4]:
 sys.path.insert(0,sys.argv[4])
 import coverage
 if coverage.__version__!='7.17.0': raise ValueError('collector version differs')
 from tools.protected_runtime_probe.capsule import require_posix_descriptors
 from tools.protected_runtime_probe.instrumentation_recording import snapshot_memory_coverage
 recorder=coverage.Coverage(data_file=None,auto_data=False,branch=True,config_file=False,plugins=[])
 recorder.set_option('run:core','ctrace')
 with recorder.collect(): require_posix_descriptors('posix')
 data=recorder.get_data()
 try:
  raw=snapshot_memory_coverage(data)
  if not raw.startswith(b'SQLite format 3\\x00'): raise ValueError('memory snapshot format differs')
 finally: data.close(force=True)
with open('/proc/self/maps') as stream:
 paths=sorted({parts[5].strip() for line in stream
  if len(parts:=line.split(maxsplit=5))==6 and parts[5].startswith('/')})
print(json.dumps(paths))
"""


@dataclass(frozen=True)
class NativeDependency:
    """One actually mapped native object and its exact packet placement."""

    payload: str
    installed_source: Path
    manager: str


@dataclass(frozen=True)
class NativeRuntimeSources:
    """Physical native source roots, distinct from their packet destinations.

    Alternate roots describe explicit ordinary input trees; classification alone
    does not establish that an object is mapped by a process or installed by dpkg.
    """

    interpreter: Path = Path("/usr/bin/python3.12")
    stdlib: Path = Path("/usr/lib/python3.12")
    libraries: Path = Path("/usr/lib/x86_64-linux-gnu")


SYSTEM_NATIVE_SOURCES = NativeRuntimeSources()


def require_native_runtime_abi(
    operating_system: str, machine: str, python_version: tuple[int, int], pointer_bits: int
) -> None:
    """Require the ABI supported by the qualification runtime.

    Parameters
    ----------
    operating_system:
        Observed interpreter platform, such as sys.platform.
    machine:
        Observed machine architecture.
    python_version:
        Observed interpreter major and minor version.
    pointer_bits:
        Observed interpreter pointer width in bits.

    Raises
    ------
    OSError
        Unless the observations identify native Linux x86_64 CPython3.12, 64 bit.
    """
    if (operating_system, machine, python_version, pointer_bits) != (
        "linux",
        "x86_64",
        (3, 12),
        64,
    ):
        raise OSError("native runtime requires native Linux x86_64 CPython3.12, 64 bit")


def _soname(source: Path) -> str:
    """Read the installed object's loader name without executing its captured copy."""
    returncode, output = run_native_metadata_command(
        "/usr/bin/readelf", ("--dynamic", "--wide", str(source))
    )
    if returncode or len(output) > 262144:
        raise ValueError("native dependency loader name is unavailable")
    names = [match.group(1) for match in re.finditer(rb"\(SONAME\).*?\[([^\]]+)\]", output)]
    if len(names) != 1 or re.fullmatch(rb"[A-Za-z0-9_.+-]+", names[0]) is None:
        raise ValueError("native dependency loader name is ambiguous")
    return names[0].decode("ascii")


def _dependency(
    path: Path,
    dependency_root: Path,
    sources: NativeRuntimeSources,
    instrumentation_root: Path | None,
) -> NativeDependency:
    """Classify an observed object by its installed system or wheel source."""
    path = path.resolve(strict=True)
    prefix = "runtime/python/lib/python3.12/"
    if path == sources.interpreter.resolve(strict=True):
        return NativeDependency("runtime/python/bin/python3.12", path, "dpkg")
    if path.is_relative_to(sources.stdlib.resolve(strict=True)):
        return NativeDependency(
            prefix + path.relative_to(sources.stdlib.resolve(strict=True)).as_posix(), path, "dpkg"
        )
    if path.is_relative_to(dependency_root):
        name = "websockets/" + path.relative_to(dependency_root).as_posix()
        return NativeDependency(prefix + "dist-packages/" + name, path, "wheel")
    if instrumentation_root is not None and path.is_relative_to(instrumentation_root):
        name = "coverage/" + path.relative_to(instrumentation_root).as_posix()
        return NativeDependency(prefix + "dist-packages/" + name, path, "instrumentation-wheel")
    if path.is_relative_to(sources.libraries.resolve(strict=True)):
        return NativeDependency("runtime/native/lib/" + _soname(path), path, "dpkg")
    raise ValueError("probe loaded a native object outside its declared package sources")


def classify_native_import_paths(
    paths: object,
    dependency_root: Path,
    *,
    sources: NativeRuntimeSources = SYSTEM_NATIVE_SOURCES,
    instrumentation_root: Path | None = None,
) -> tuple[NativeDependency, ...]:
    """Classify a bounded path observation using actual files and ELF metadata.

    Parameters
    ----------
    paths:
        Decoded path list from a native mapping observation.
    dependency_root:
        Physical installed websockets package root.
    sources:
        Physical interpreter, stdlib and shared-library roots. Defaults to the
        actual system. Alternate trees are input data, not live mapping evidence.
    instrumentation_root:
        Optional explicitly declared coverage package root. Classification does
        not verify its archive; the actual observer verifies the complete tree.

    Returns
    -------
    tuple[NativeDependency, ...]
        ELF objects with unique, deterministic packet destinations.

    Raises
    ------
    ValueError, OSError
        For invalid paths, unavailable objects, unknown sources or collisions.

    Notes
    -----
    Only observe_native_import_closure supplies actual process-mapping evidence.
    This classification step neither executes captured objects nor approves them.
    """
    if type(paths) is not list or not 1 <= len(paths) <= 256:
        raise ValueError("actual native import closure is malformed or unbounded")
    if instrumentation_root is not None:
        instrumentation_root = instrumentation_root.resolve(strict=True)
    dependencies = []
    for name in paths:
        if type(name) is not str or not name.startswith("/") or name.endswith(" (deleted)"):
            raise ValueError("actual native import path is unavailable")
        path = Path(name)
        with path.open("rb") as stream:
            if stream.read(4) != b"\x7fELF":
                continue
        dependencies.append(_dependency(path, dependency_root, sources, instrumentation_root))
    if len({item.payload for item in dependencies}) != len(dependencies):
        raise ValueError("actual native closure has colliding payload names")
    return tuple(sorted(dependencies, key=lambda item: item.payload))


def observe_native_import_closure(
    source: Path,
    dependency_root: Path,
    *,
    instrumentation_root: Path | None = None,
    instrumentation_wheel: Path | None = None,
) -> tuple[NativeDependency, ...]:
    """Observe the native objects loaded by actual entry modules in a clean interpreter.

    Parameters
    ----------
    source:
        Current candidate checkout. Only ordinary imports run, without root effects.
    dependency_root:
        Installed websockets package whose Python parent is added explicitly.
    instrumentation_root, instrumentation_wheel:
        Both absent for production. Both present selects the complete verified
        original collector tree, explicit C tracer and own-memory serialization.
        The root names the coverage package, with full wheel tree in its parent.

    Returns
    -------
    tuple[NativeDependency, ...]
        Exact loaded interpreter, system libraries, stdlib extensions and wheel natives.

    Raises
    ------
    ValueError, OSError, TimeoutError
        On unsupported host, failed imports, ambiguous placement or unknown native source.

    Notes
    -----
    This uses system Python and current installed system objects. The explicit
    instrumentation option executes only the complete original verified collector
    tree as ordinary preparation input; it does not run a proposed captured
    interpreter or loader. Actual root-stage modules and deferred frame imports
    are observed without calling their root entrypoints.
    Separate host account/ACL tools may load NSS in their own processes; their
    execution is not part of this interpreter import observation.
    """
    require_native_runtime_abi(
        sys.platform, platform.machine(), sys.version_info[:2], sys.maxsize.bit_length() + 1
    )
    source, dependency_root = source.resolve(strict=True), dependency_root.resolve(strict=True)
    if (instrumentation_root is None) != (instrumentation_wheel is None):
        raise ValueError(
            "instrumentation observation requires both complete tree and original wheel"
        )
    if instrumentation_root is not None and instrumentation_wheel is not None:
        from .instrumentation_artifacts import (
            capture_instrumentation_wheel,
            verify_instrumentation_tree,
        )

        wheel = capture_instrumentation_wheel(
            source / "tools/protected_runtime_probe/native-dependencies.lock.json",
            instrumentation_wheel,
        )
        verify_instrumentation_tree(instrumentation_root.parent, wheel)
        instrumentation_root = instrumentation_root.resolve(strict=True)
        if instrumentation_root.name != "coverage":
            raise ValueError("instrumentation observation requires the fixed coverage package")
    returncode, output = run_native_metadata_command(
        "/usr/bin/python3.12",
        (
            "-I",
            "-B",
            "-S",
            "-c",
            _OBSERVE,
            str(source),
            str(dependency_root.parent),
            json.dumps(
                _ENTRYPOINTS
                + _DEFERRED_IMPORTS
                + (
                    _INSTRUMENTED_ENTRYPOINTS + _INSTRUMENTED_DEFERRED_IMPORTS
                    if instrumentation_root is not None
                    else ()
                )
            ),
            str(instrumentation_root.parent) if instrumentation_root is not None else "",
        ),
        timeout=30,
    )
    if returncode or len(output) > 262144:
        raise ValueError("actual probe native import closure could not be observed")
    return classify_native_import_paths(
        json.loads(output), dependency_root, instrumentation_root=instrumentation_root
    )


def verify_native_runtime_content(
    content: Mapping[str, bytes],
    source: Path,
    dependency_root: Path,
    wheel_path: Path,
    *,
    instrumentation_root: Path | None = None,
    instrumentation_wheel: Path | None = None,
) -> dict[str, object]:
    """Verify every captured native object and refuse any outside the observed closure.

    Parameters
    ----------
    content:
        Detached complete runtime bytes, after canonical manifest capture.
    source:
        Candidate checkout whose actual entry-module imports define the closure.
    dependency_root:
        Installed websockets package matching the captured application dependency.
    wheel_path:
        Local wheel artifact matching the candidate's native dependency lock.
    instrumentation_root, instrumentation_wheel:
        Optional all-or-none collector package tree and original artifact. Its
        complete distribution and actually mapped tracer must match the lock.

    Returns
    -------
    dict[str, object]
        Timestamped complete native closure provenance for inclusion in the final capsule.

    Raises
    ------
    ValueError, OSError, TimeoutError
        On missing, extra, changed or unverifiable native bytes. A collector
        observation uses only its complete verified ordinary input tree; the
        proposed runtime interpreter and loader are never executed.
    """
    dependencies = observe_native_import_closure(
        source,
        dependency_root,
        instrumentation_root=instrumentation_root,
        instrumentation_wheel=instrumentation_wheel,
    )
    expected = {item.payload for item in dependencies}
    actual = {
        name
        for name, raw in content.items()
        if name.startswith("runtime/native/")
        or ".so" in Path(name).name
        or name == "runtime/python/bin/python3.12"
        or raw.startswith(b"\x7fELF")
    }
    if actual != expected:
        raise ValueError("captured runtime native objects differ from observed import closure")
    records, instrumented = _native_records(
        content,
        dependencies,
        source / "tools/protected_runtime_probe/native-dependencies.lock.json",
        wheel_path,
        instrumentation_wheel,
    )
    return {
        "schema": "synapse.native-runtime-provenance.v1",
        "observed_at": datetime.now(UTC).isoformat(),
        "entry_modules": list(_ENTRYPOINTS + (_INSTRUMENTED_ENTRYPOINTS if instrumented else ())),
        "deferred_imports": list(
            _DEFERRED_IMPORTS + (_INSTRUMENTED_DEFERRED_IMPORTS if instrumented else ())
        ),
        "instrumentation": "coverage7.17.0-ctrace-memory-snapshot" if instrumented else None,
        "account_resolution": {
            "owned_interpreters": "root-controlled-local-files",
            "external_processes": ["/usr/sbin/useradd", "/usr/bin/setfacl"],
            "external_nss": "outside-interpreter-import-observation",
        },
        "native_objects": records,
    }


def _native_records(
    content: Mapping[str, bytes],
    dependencies: tuple[NativeDependency, ...],
    lock: Path,
    wheel_path: Path,
    instrumentation_wheel: Path | None,
) -> tuple[list[dict[str, object]], bool]:
    """Verify each observed object's system or original wheel provenance."""
    wheel = load_native_wheel(lock, wheel_path)
    collector = None
    if instrumentation_wheel is not None:
        from .instrumentation_artifacts import (
            capture_instrumentation_wheel,
            instrumentation_runtime_content,
        )

        collector = capture_instrumentation_wheel(lock, instrumentation_wheel)
        for name, raw in instrumentation_runtime_content(collector).items():
            if content.get(name) != raw:
                raise ValueError("instrumented runtime differs from complete original wheel")
    records = []
    for item in dependencies:
        raw = content[item.payload]
        if item.manager == "dpkg":
            record = verify_dpkg_native_file(item.installed_source, raw)
        elif collector is not None and item.manager == "instrumentation-wheel":
            from .instrumentation_artifacts import verify_instrumentation_native_file

            member = item.payload.removeprefix("runtime/python/lib/python3.12/dist-packages/")
            record = verify_instrumentation_native_file(
                item.installed_source, member, raw, collector
            )
        else:
            member = item.payload.removeprefix("runtime/python/lib/python3.12/dist-packages/")
            record = verify_wheel_native_file(item.installed_source, member, raw, wheel)
        records.append({"payload": item.payload, "manager": item.manager, **record})
    return records, collector is not None
