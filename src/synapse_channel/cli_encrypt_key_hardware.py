# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — hardware-backed wrapped key generation CLI (PKCS#11 / TPM 2.0 / cloud HSM)
"""Hardware-backed ``synapse encrypt-key generate-wrapped-*`` commands.

Each command writes an envelope-encrypted at-rest key whose key-encryption key
lives in hardware the operator controls: a PKCS#11 token (YubiKey PIV, network
HSM, SoftHSM), a TPM 2.0 device, or a cloud HSM/KMS provider. The wrapping key
never leaves its device; only the wrapped envelope lands on disk. The provider
modules are imported inside each command so the base CLI works without the
optional hardware extras installed.
"""

from __future__ import annotations

import argparse
import getpass
import os
from collections.abc import Callable

from synapse_channel.core.at_rest_pkcs11 import DEFAULT_KEK_LABEL
from synapse_channel.core.at_rest_tpm2 import DEFAULT_TPM2_TCTI


def _cmd_generate_wrapped_pkcs11(
    args: argparse.Namespace,
    *,
    pin_reader: Callable[[str], str] = getpass.getpass,
) -> int:
    """Create a key file wrapped by a key-encryption key held on a PKCS#11 token.

    The data key is random and wrapped on the token (YubiKey PIV, cloud/network HSM, or SoftHSM);
    the token key never leaves the device. The module path comes from ``--pkcs11-module`` or the
    ``PKCS11_MODULE`` environment variable; the PIN from ``PKCS11_PIN`` or an interactive prompt.
    """
    from synapse_channel.core.at_rest_pkcs11 import generate_wrapped_key_file_pkcs11

    module_path = args.pkcs11_module or os.environ.get("PKCS11_MODULE")
    if not module_path:
        print(
            "synapse encrypt-key generate-wrapped-pkcs11: "
            "a PKCS#11 module is required via --pkcs11-module or PKCS11_MODULE"
        )
        return 2
    pin = os.environ.get("PKCS11_PIN") or pin_reader("PKCS#11 user PIN: ")
    try:
        written = generate_wrapped_key_file_pkcs11(
            args.path,
            module_path=module_path,
            token_label=args.token_label,
            pin=pin,
            key_label=args.key_label,
            create_kek=args.create_kek,
        )
    except FileExistsError as exc:
        print(str(exc))
        return 1
    except (ValueError, RuntimeError) as exc:
        print(f"synapse encrypt-key generate-wrapped-pkcs11: {exc}")
        return 2
    print(f"wrote PKCS#11-wrapped at-rest key (owner-only): {written}")
    return 0


def _cmd_generate_wrapped_tpm2(args: argparse.Namespace) -> int:
    """Create a key file wrapped by a key-encryption key rooted in a TPM 2.0 device.

    The data key is random and wrapped with RSA-OAEP against a decrypt-only primary derived inside
    the TPM; the RSA private key never leaves the chip. The TPM is reached through ``--tcti`` (or
    the ``TPM2_TCTI`` environment variable), defaulting to the in-kernel resource-managed device.
    """
    from synapse_channel.core.at_rest_tpm2 import generate_wrapped_key_file_tpm2

    tcti = args.tcti or os.environ.get("TPM2_TCTI") or DEFAULT_TPM2_TCTI
    try:
        written = generate_wrapped_key_file_tpm2(args.path, tcti=tcti)
    except FileExistsError as exc:
        print(str(exc))
        return 1
    except (ValueError, RuntimeError) as exc:
        print(f"synapse encrypt-key generate-wrapped-tpm2: {exc}")
        return 2
    print(f"wrote TPM-wrapped at-rest key (owner-only): {written}")
    return 0


def _cmd_generate_wrapped_cloud_hsm(args: argparse.Namespace) -> int:
    """Create a key file wrapped by a cloud HSM / cloud KMS provider.

    ``local-aes-kw`` wraps under a local owner-only master key file (offline tests and
    air-gapped drills). ``aws-kms`` uses optional ``boto3`` against a customer master key.
    """
    from synapse_channel.core.at_rest_cloud_hsm import (
        PROVIDER_AWS_KMS,
        PROVIDER_LOCAL_AES_KW,
        AwsKmsCloudHsmProvider,
        CloudHsmProvider,
        LocalAesKwCloudHsmProvider,
        generate_wrapped_key_file_cloud_hsm,
    )

    provider_name = args.provider
    provider: CloudHsmProvider
    try:
        if provider_name == PROVIDER_LOCAL_AES_KW:
            if not args.master_key_file:
                print(
                    "synapse encrypt-key generate-wrapped-cloud-hsm: "
                    "--master-key-file is required for provider local-aes-kw"
                )
                return 2
            provider = LocalAesKwCloudHsmProvider.from_key_file(args.master_key_file)
        elif provider_name == PROVIDER_AWS_KMS:
            if not args.kms_key_id:
                print(
                    "synapse encrypt-key generate-wrapped-cloud-hsm: "
                    "--kms-key-id is required for provider aws-kms"
                )
                return 2
            provider = AwsKmsCloudHsmProvider(args.kms_key_id, region_name=args.region)
        else:
            print(
                f"synapse encrypt-key generate-wrapped-cloud-hsm: "
                f"unsupported provider {provider_name!r}"
            )
            return 2
        written = generate_wrapped_key_file_cloud_hsm(args.path, provider=provider)
    except FileExistsError as exc:
        print(str(exc))
        return 1
    except (ValueError, RuntimeError) as exc:
        print(f"synapse encrypt-key generate-wrapped-cloud-hsm: {exc}")
        return 2
    print(f"wrote cloud-HSM-wrapped at-rest key (owner-only): {written}")
    return 0


def add_hardware_parsers(nested: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the hardware-backed ``generate-wrapped-*`` subcommands."""
    pkcs11 = nested.add_parser(
        "generate-wrapped-pkcs11",
        help="Write a key file wrapped by a key-encryption key on a PKCS#11 token (YubiKey/HSM).",
    )
    pkcs11.add_argument("path", help="Destination wrapped-key-file path (must not already exist).")
    pkcs11.add_argument(
        "--pkcs11-module",
        default=None,
        help="Path to the PKCS#11 module (.so/.dll), or set the PKCS11_MODULE env var.",
    )
    pkcs11.add_argument(
        "--token-label",
        required=True,
        help="Label of the token that holds (or will hold) the key-encryption key.",
    )
    pkcs11.add_argument(
        "--key-label",
        default=DEFAULT_KEK_LABEL,
        help=f"Label of the token key-encryption key object (default {DEFAULT_KEK_LABEL!r}).",
    )
    pkcs11.add_argument(
        "--no-create-kek",
        dest="create_kek",
        action="store_false",
        help="Fail if the key-encryption key is absent instead of generating it on the token.",
    )
    pkcs11.set_defaults(func=_cmd_generate_wrapped_pkcs11, create_kek=True)

    tpm2 = nested.add_parser(
        "generate-wrapped-tpm2",
        help="Write a key file wrapped by a key-encryption key rooted in a TPM 2.0 device.",
    )
    tpm2.add_argument("path", help="Destination wrapped-key-file path (must not already exist).")
    tpm2.add_argument(
        "--tcti",
        default=None,
        help=(
            "TPM transmission interface (e.g. device:/dev/tpmrm0), or set the TPM2_TCTI env var "
            f"(default {DEFAULT_TPM2_TCTI!r})."
        ),
    )
    tpm2.set_defaults(func=_cmd_generate_wrapped_tpm2)

    cloud_hsm = nested.add_parser(
        "generate-wrapped-cloud-hsm",
        help="Write a key file wrapped by a cloud HSM / cloud KMS key-encryption key.",
    )
    cloud_hsm.add_argument(
        "path",
        help="Destination wrapped-key-file path (must not already exist).",
    )
    cloud_hsm.add_argument(
        "--provider",
        required=True,
        choices=("local-aes-kw", "aws-kms"),
        help="Cloud HSM provider: local-aes-kw (offline master key) or aws-kms (optional boto3).",
    )
    cloud_hsm.add_argument(
        "--master-key-file",
        default=None,
        help="Owner-only 32-byte master key file (required for local-aes-kw).",
    )
    cloud_hsm.add_argument(
        "--kms-key-id",
        default=None,
        help="AWS KMS key id / ARN / alias (required for aws-kms).",
    )
    cloud_hsm.add_argument(
        "--region",
        default=None,
        help="AWS region for KMS (optional; falls back to the usual AWS config chain).",
    )
    cloud_hsm.set_defaults(func=_cmd_generate_wrapped_cloud_hsm)
