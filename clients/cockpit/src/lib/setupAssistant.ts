// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — read-only cockpit setup planning model

import type { FeedStatus } from "./feed";
import type { LiveConnectionStatus } from "./liveTransport";
import type { SnapshotStatus } from "./snapshot";

export type SetupTruth = "installed" | "configured" | "absent" | "unverifiable";
export type SetupProofVerdict = "pass" | "attention" | "absent";
export type SetupPreflightId = "cockpit" | "access" | "hub" | "transport" | "feeds";
export type SetupProofId = "hub" | "access" | "origin" | "transport" | "feeds";

export interface SetupEvidence {
  readonly access: "ready" | "unavailable";
  readonly snapshot: SnapshotStatus;
  readonly transport: LiveConnectionStatus;
  readonly optionalFeeds: readonly FeedStatus[];
  readonly loopbackOrigin: boolean;
}

export interface SetupPreflightRow {
  readonly id: SetupPreflightId;
  readonly truth: SetupTruth;
}

export interface SetupProofRow {
  readonly id: SetupProofId;
  readonly verdict: SetupProofVerdict;
}

export interface SetupProfileInput {
  readonly hubPort: string;
  readonly dashboardPort: string;
  readonly durableEvidence: boolean;
  readonly protectedDashboard: boolean;
}

export interface SetupCommand {
  readonly id: "hub" | "dashboard";
  readonly text: string;
}

export type SetupCommandPlan =
  | { readonly ok: true; readonly commands: readonly SetupCommand[] }
  | { readonly ok: false; readonly error: "hub-port" | "dashboard-port" | "port-collision" };

const LOOPBACK_HOST = "127.0.0.1";
const HUB_DB_PLACEHOLDER = "<HUB_DB_PATH>";
const ACCESS_POLICY_PLACEHOLDER = "<OWNER_ONLY_ACCESS_POLICY_PATH>";
const PORT_PATTERN = /^(?:[1-9][0-9]{3,4})$/u;

function parsedPort(value: string): number | null {
  if (!PORT_PATTERN.test(value)) return null;
  const port = Number(value);
  return port >= 1024 && port <= 65_535 ? port : null;
}

function optionalFeedTruth(statuses: readonly FeedStatus[]): SetupTruth {
  if (statuses.some((status) => status === "live")) return "configured";
  if (statuses.length === 0 || statuses.every((status) => status === "absent")) return "absent";
  return "unverifiable";
}

function transportTruth(status: LiveConnectionStatus): SetupTruth {
  if (status === "live" || status === "fallback") return "configured";
  if (status === "unsupported") return "absent";
  return "unverifiable";
}

/** Classify only facts the loaded cockpit can prove without probing the host. */
export function deriveSetupPreflight(evidence: SetupEvidence): readonly SetupPreflightRow[] {
  return [
    { id: "cockpit", truth: "installed" },
    { id: "access", truth: evidence.access === "ready" ? "configured" : "unverifiable" },
    { id: "hub", truth: evidence.snapshot === "live" ? "configured" : "unverifiable" },
    { id: "transport", truth: transportTruth(evidence.transport) },
    { id: "feeds", truth: optionalFeedTruth(evidence.optionalFeeds) },
  ];
}

/** Convert current browser evidence into a proof checklist without inferring health. */
export function deriveSetupProof(evidence: SetupEvidence): readonly SetupProofRow[] {
  const feedTruth = optionalFeedTruth(evidence.optionalFeeds);
  return [
    { id: "hub", verdict: evidence.snapshot === "live" ? "pass" : "attention" },
    { id: "access", verdict: evidence.access === "ready" ? "pass" : "attention" },
    { id: "origin", verdict: evidence.loopbackOrigin ? "pass" : "attention" },
    {
      id: "transport",
      verdict: evidence.transport === "live"
        ? "pass"
        : evidence.transport === "fallback" || evidence.transport === "unsupported"
          ? "absent"
          : "attention",
    },
    {
      id: "feeds",
      verdict: feedTruth === "configured" ? "pass" : feedTruth === "absent" ? "absent" : "attention",
    },
  ];
}

/** Recognise browser origins that keep the setup proof on the local machine. */
export function isLoopbackHostname(hostname: string): boolean {
  const normalised = hostname.trim().toLowerCase();
  return normalised === "localhost" || normalised === "127.0.0.1" || normalised === "::1" || normalised === "[::1]";
}

/** Build inert loopback command previews; no command is executed by the cockpit. */
export function buildSetupCommandPlan(input: SetupProfileInput): SetupCommandPlan {
  const hubPort = parsedPort(input.hubPort);
  if (hubPort === null) return { ok: false, error: "hub-port" };
  const dashboardPort = parsedPort(input.dashboardPort);
  if (dashboardPort === null) return { ok: false, error: "dashboard-port" };
  if (hubPort === dashboardPort) return { ok: false, error: "port-collision" };

  const hubParts = [
    "synapse", "hub", "--host", LOOPBACK_HOST, "--port", String(hubPort), "--metrics",
  ];
  if (input.durableEvidence) hubParts.push("--db", HUB_DB_PLACEHOLDER);

  const dashboardParts = [
    "synapse", "dashboard", "--uri", `ws://${LOOPBACK_HOST}:${hubPort}`,
    "--host", LOOPBACK_HOST, "--port", String(dashboardPort),
    "--cockpit-dist", "clients/cockpit/dist",
  ];
  if (input.durableEvidence) dashboardParts.push("--feeds-db", HUB_DB_PLACEHOLDER);
  if (input.protectedDashboard) {
    dashboardParts.push("--dashboard-access-file", ACCESS_POLICY_PLACEHOLDER);
  }

  return {
    ok: true,
    commands: [
      { id: "hub", text: hubParts.join(" ") },
      { id: "dashboard", text: dashboardParts.join(" ") },
    ],
  };
}

/** Fail closed if future generator changes add an unsafe bind or inline secret flag. */
export function isSafeSetupCommand(command: string): boolean {
  if (command.includes("\n") || command.includes("\r")) return false;
  if (!command.includes(`--host ${LOOPBACK_HOST}`)) return false;
  if (command.includes("--allow-non-loopback") || command.includes("--insecure-off-loopback")) {
    return false;
  }
  if (/--(?:token|dashboard-token|metrics-token|message-auth-key|observed-token)(?:=|\s)/u.test(command)) {
    return false;
  }
  if (/(?:\/home\/|\/tmp\/|~\/)/u.test(command)) return false;
  const placeholders = command.match(/<[^>]+>/gu) ?? [];
  return placeholders.every(
    (placeholder) => placeholder === HUB_DB_PLACEHOLDER || placeholder === ACCESS_POLICY_PLACEHOLDER,
  );
}
