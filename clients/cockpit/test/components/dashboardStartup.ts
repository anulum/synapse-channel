// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — wait until a spawned dashboard prints its URL

import { type ChildProcessWithoutNullStreams } from "node:child_process";

// A hang guard, not a measurement. The suite does not time how fast the
// dashboard starts. A coverage run can keep the child from printing its URL
// for longer than five seconds, and that wait is not a failure. A start that
// never prints still fails, and the message keeps the child's stderr.
export const DASHBOARD_START_HANG_GUARD_MS = 60_000;

/**
 * Resolve with the first bytes the child prints.
 * Fail when `hangGuardMs` elapses. `diagnostics` is the child's stderr so far.
 */
export function waitForDashboardUrl(
  child: ChildProcessWithoutNullStreams,
  diagnostics: () => string,
  hangGuardMs: number = DASHBOARD_START_HANG_GUARD_MS,
): Promise<string> {
  return new Promise<string>((resolve, reject) => {
    const timer = setTimeout(() => {
      reject(new Error(`dashboard startup timed out: ${diagnostics()}`));
    }, hangGuardMs);
    const fail = (error: Error): void => {
      clearTimeout(timer);
      reject(error);
    };
    child.once("error", fail);
    child.once("exit", (code, signal) => {
      fail(new Error(`dashboard exited (${code ?? signal}): ${diagnostics()}`));
    });
    child.stdout.once("data", (chunk: Buffer) => {
      clearTimeout(timer);
      resolve(chunk.toString().trim());
    });
  });
}
