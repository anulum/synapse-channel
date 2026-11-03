# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — fixed finite child instrumentation entry
"""Record one fixed role entry without consuming its construction protocol."""

from __future__ import annotations

import argparse
import os
import re
import runpy
import sys
from dataclasses import dataclass
from pathlib import Path

from .instrumentation import emit_instrumentation_file_limit_error
from .instrumentation_recording import prepare_recording

_MODULES = {
    "authority": "authority_child",
    "client": "authority_child",
    "operator": "authority_child",
    "namespace-wrapper": "namespace_child",
    "writer": "namespace_child",
}


@dataclass(frozen=True)
class RecordingLaunch:
    """Root-selected recorder enrollment carried independently of child stdin.

    Notes
    -----
    This metadata grants no role or execution authority. The independently
    retained controller selects the identities and descriptor; the collector
    re-verifies the complete profile and checks final credentials at save.
    Only the inside writer uses the fixed enrolled namespace output root.
    """

    role: str
    manifest_sha256: str
    uid: int
    gid: int
    output_descriptor: int | None = None

    def __post_init__(self) -> None:
        """Refuse unregistered roles, malformed pins and stdin-selected outputs."""
        if self.role not in _MODULES:
            raise ValueError("unexpected instrumentation child role")
        if (
            type(self.manifest_sha256) is not str
            or re.fullmatch("[0-9a-f]{64}", self.manifest_sha256) is None
        ):
            raise ValueError("invalid instrumentation child manifest pin")
        if any(
            type(value) is not int or not 0 <= value < 2**32 - 1 for value in (self.uid, self.gid)
        ):
            raise ValueError("invalid instrumentation child principal")
        if self.role == "writer":
            if self.output_descriptor is not None:
                raise ValueError("inside writer requires its enrolled namespace output root")
        elif type(self.output_descriptor) is not int or self.output_descriptor <= 2:
            raise ValueError("instrumentation child requires a separate output descriptor")

    def entry_arguments(self, view: Path, module: str) -> list[str]:
        """Build only the exact role's fixed entry arguments.

        Parameters
        ----------
        view:
            Complete immutable view, or the writer's fixed namespace alias.
        module:
            Fixed authority_child or namespace_child selected for this role.

        Returns
        -------
        list[str]
            Recorder arguments followed by the fixed module name. Child
            arguments follow these without modifying the stdin protocol.

        Raises
        ------
        ValueError
            On a relative view or a role/module mismatch.
        """
        if not view.is_absolute() or ".." in view.parts or _MODULES[self.role] != module:
            raise ValueError("instrumentation child entry differs from its role")
        arguments = [
            "--view",
            str(view),
            "--manifest-sha256",
            self.manifest_sha256,
            "--recording-role",
            self.role,
            "--output-uid",
            str(self.uid),
            "--output-gid",
            str(self.gid),
        ]
        if self.output_descriptor is not None:
            arguments += ["--output-descriptor", str(self.output_descriptor)]
        return [*arguments, module]


def main(argv: list[str] | None = None) -> int:
    """Collect the fixed child module from before import through its final exit.

    Parameters
    ----------
    argv:
        Controller-selected profile pin, role, output enrollment and fixed
        module arguments. No construction configuration is read from stdin.

    Returns
    -------
    int
        Zero if the target returns normally and the final dataset saves.
        A target SystemExit is preserved after attempting the terminal save.

    Raises
    ------
    ValueError, OSError
        On invalid enrollment, refused profile or an incomplete final save.
        A save failure overrides any provisional successful target exit.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--view", type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--recording-role", required=True)
    parser.add_argument("--output-uid", type=int, required=True)
    parser.add_argument("--output-gid", type=int, required=True)
    parser.add_argument("--output-descriptor", type=int)
    parser.add_argument("module", choices=("authority_child", "namespace_child"))
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    arguments = parser.parse_args(argv)
    enrollment = RecordingLaunch(
        arguments.recording_role,
        arguments.manifest_sha256,
        arguments.output_uid,
        arguments.output_gid,
        arguments.output_descriptor,
    )
    enrollment.entry_arguments(arguments.view, arguments.module)
    child_arguments = arguments.arguments
    if (enrollment.role == "writer" and child_arguments != ["--inside"]) or (
        enrollment.role == "namespace-wrapper" and child_arguments
    ):
        raise ValueError("instrumentation namespace entry mode differs from its role")
    descriptor = enrollment.output_descriptor
    if descriptor is None:
        descriptor = os.open(
            "/instrumentation-data", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        )
    try:
        recording = prepare_recording(
            arguments.view,
            enrollment.manifest_sha256,
            role=enrollment.role,
            output_descriptor=descriptor,
            uid=enrollment.uid,
            gid=enrollment.gid,
        )
    finally:
        os.close(descriptor)
    sys.argv = ["protected_runtime_probe." + arguments.module, *child_arguments]
    if enrollment.role == "authority":
        sys.argv.append("--graceful-stop")
    elif enrollment.role in {"namespace-wrapper", "writer"}:
        sys.argv.append("--instrumentation")
    try:
        with recording.collector.collect():  # pragma: no cover
            runpy.run_module("protected_runtime_probe." + arguments.module, run_name="__main__")
    finally:
        recording.save()
    return 0  # pragma: no cover


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except OSError as error:
        emit_instrumentation_file_limit_error(error)
        raise
