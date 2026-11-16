// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — strict dashboard access descriptor client

import { authenticatedFetch } from "./auth";

export const DASHBOARD_ACCESS_URL = "/dashboard-access.json";
export type DashboardRole = "viewer" | "operator" | "admin";

export interface DashboardCapabilities {
  readonly read: boolean;
  readonly message_send: boolean;
  readonly task_declare: boolean;
  readonly task_update: boolean;
  /** Whether this principal may plan a change to the dashboard's host: an admin's, when armed. */
  readonly setup_plan: boolean;
  /** Whether this principal may apply a planned change to the dashboard's host. */
  readonly setup_apply: boolean;
}

export interface DashboardAccessDescriptor {
  readonly version: 1;
  readonly principal: string;
  readonly role: DashboardRole;
  readonly capabilities: DashboardCapabilities;
  readonly operator_armed: boolean;
  readonly trust_boundary: string;
}

export type DashboardAccessState =
  | { readonly phase: "loading"; readonly descriptor: null }
  | { readonly phase: "unavailable"; readonly descriptor: null }
  | { readonly phase: "ready"; readonly descriptor: DashboardAccessDescriptor };

export const NO_DASHBOARD_CAPABILITIES: DashboardCapabilities = Object.freeze({
  read: false,
  message_send: false,
  task_declare: false,
  task_update: false,
  setup_plan: false,
  setup_apply: false,
});
export const LOADING_DASHBOARD_ACCESS: DashboardAccessState = Object.freeze({
  phase: "loading",
  descriptor: null,
});
export const UNAVAILABLE_DASHBOARD_ACCESS: DashboardAccessState = Object.freeze({
  phase: "unavailable",
  descriptor: null,
});

const DESCRIPTOR_KEYS = [
  "capabilities",
  "operator_armed",
  "principal",
  "role",
  "trust_boundary",
  "version",
] as const;
const CAPABILITY_KEYS = [
  "message_send",
  "read",
  "setup_apply",
  "setup_plan",
  "task_declare",
  "task_update",
] as const;
const PRINCIPAL_ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/u;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasExactKeys(value: Record<string, unknown>, expected: readonly string[]): boolean {
  const actual = Object.keys(value).sort();
  return actual.length === expected.length && expected.every((key, index) => key === actual[index]);
}

/** Parse the exact capability set: every one of its keys, each a boolean, and nothing else. */
function parseCapabilities(value: unknown): DashboardCapabilities | null {
  if (!isRecord(value) || !hasExactKeys(value, CAPABILITY_KEYS)) return null;
  if (!CAPABILITY_KEYS.every((key) => typeof value[key] === "boolean")) return null;
  return Object.freeze({
    read: value["read"] === true,
    message_send: value["message_send"] === true,
    task_declare: value["task_declare"] === true,
    task_update: value["task_update"] === true,
    setup_plan: value["setup_plan"] === true,
    setup_apply: value["setup_apply"] === true,
  });
}

/** Parse only the version-one, server-authored descriptor shape. */
export function parseDashboardAccess(value: unknown): DashboardAccessDescriptor | null {
  if (!isRecord(value) || !hasExactKeys(value, DESCRIPTOR_KEYS)) return null;
  const principal = value["principal"];
  const role = value["role"];
  const capabilities = parseCapabilities(value["capabilities"]);
  if (value["version"] !== 1 || typeof principal !== "string" || !PRINCIPAL_ID.test(principal)) {
    return null;
  }
  if (role !== "viewer" && role !== "operator" && role !== "admin") return null;
  if (typeof value["operator_armed"] !== "boolean" || typeof value["trust_boundary"] !== "string") {
    return null;
  }
  if (capabilities === null) return null;
  return Object.freeze({
    version: 1,
    principal,
    role,
    capabilities,
    operator_armed: value["operator_armed"],
    trust_boundary: value["trust_boundary"],
  });
}

/** Fetch one non-authoritative presentation descriptor through the shared bearer adapter. */
export async function fetchDashboardAccess(
  fetcher: typeof fetch = authenticatedFetch,
): Promise<DashboardAccessState> {
  try {
    const response = await fetcher(DASHBOARD_ACCESS_URL, { cache: "no-store" });
    if (!response.ok) return UNAVAILABLE_DASHBOARD_ACCESS;
    const descriptor = parseDashboardAccess(await response.json());
    return descriptor === null
      ? UNAVAILABLE_DASHBOARD_ACCESS
      : Object.freeze({ phase: "ready", descriptor });
  } catch {
    return UNAVAILABLE_DASHBOARD_ACCESS;
  }
}

export function capabilitiesOf(access: DashboardAccessState): DashboardCapabilities {
  return access.descriptor?.capabilities ?? NO_DASHBOARD_CAPABILITIES;
}

/**
 * Return `capabilities` without any write: the view a peer scope allows. A peer
 * scope shows an observed hub's state, so nothing done from it may change work.
 */
export function readOnlyCapabilities(capabilities: DashboardCapabilities): DashboardCapabilities {
  return {
    ...capabilities,
    message_send: false,
    task_declare: false,
    task_update: false,
    setup_plan: false,
    setup_apply: false,
  };
}

/** Detect removal of any currently shipped write control. */
export function lostWriteCapability(
  previous: DashboardCapabilities,
  current: DashboardCapabilities,
): boolean {
  return (
    (previous.message_send && !current.message_send) ||
    (previous.task_declare && !current.task_declare) ||
    (previous.task_update && !current.task_update)
  );
}
