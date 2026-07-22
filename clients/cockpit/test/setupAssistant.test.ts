// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — read-only setup planning model tests

import { describe, expect, it } from "vitest";

import {
  buildSetupCommandPlan,
  deriveSetupPreflight,
  deriveSetupProof,
  isLoopbackHostname,
  isSafeSetupCommand,
  type SetupEvidence,
} from "../src/lib/setupAssistant";

const LIVE: SetupEvidence = {
  access: "ready",
  snapshot: "live",
  transport: "live",
  optionalFeeds: ["live", "absent"],
  loopbackOrigin: true,
};

describe("setup assistant model", () => {
  it("classifies only evidence the loaded browser can prove", () => {
    expect(deriveSetupPreflight(LIVE)).toEqual([
      { id: "cockpit", truth: "installed" },
      { id: "access", truth: "configured" },
      { id: "hub", truth: "configured" },
      { id: "transport", truth: "configured" },
      { id: "feeds", truth: "configured" },
    ]);
    expect(deriveSetupPreflight({
      ...LIVE,
      access: "unavailable",
      snapshot: "stale",
      transport: "unsupported",
      optionalFeeds: ["absent", "absent"],
    }).map((row) => row.truth)).toEqual([
      "installed", "unverifiable", "unverifiable", "absent", "absent",
    ]);
    expect(deriveSetupPreflight({
      ...LIVE,
      snapshot: "error",
      transport: "reconnecting",
      optionalFeeds: ["connecting", "error"],
    }).map((row) => row.truth)).toEqual([
      "installed", "configured", "unverifiable", "unverifiable", "unverifiable",
    ]);
    expect(deriveSetupPreflight({ ...LIVE, transport: "fallback", optionalFeeds: [] })[3]).toEqual({
      id: "transport",
      truth: "configured",
    });
  });

  it("keeps pass, attention and optional absence distinct in the proof checklist", () => {
    expect(deriveSetupProof(LIVE).map((row) => row.verdict)).toEqual([
      "pass", "pass", "pass", "pass", "pass",
    ]);
    expect(deriveSetupProof({
      ...LIVE,
      access: "unavailable",
      snapshot: "connecting",
      transport: "fallback",
      optionalFeeds: [],
      loopbackOrigin: false,
    }).map((row) => row.verdict)).toEqual([
      "attention", "attention", "attention", "absent", "absent",
    ]);
    expect(deriveSetupProof({
      ...LIVE,
      transport: "unsupported",
      optionalFeeds: ["connecting", "error"],
    }).map((row) => row.verdict)).toEqual([
      "pass", "pass", "pass", "absent", "attention",
    ]);
    expect(deriveSetupProof({ ...LIVE, transport: "gap" })[3]?.verdict).toBe("attention");
  });

  it("recognises only explicit browser loopback hostnames", () => {
    expect(isLoopbackHostname("localhost")).toBe(true);
    expect(isLoopbackHostname(" LOCALHOST ")).toBe(true);
    expect(isLoopbackHostname("127.0.0.1")).toBe(true);
    expect(isLoopbackHostname("::1")).toBe(true);
    expect(isLoopbackHostname("[::1]")).toBe(true);
    expect(isLoopbackHostname("dashboard.internal")).toBe(false);
  });

  it("builds fixed-loopback previews with inert optional placeholders", () => {
    const basic = buildSetupCommandPlan({
      hubPort: "8876",
      dashboardPort: "8765",
      durableEvidence: false,
      protectedDashboard: false,
    });
    expect(basic).toEqual({
      ok: true,
      commands: [
        { id: "hub", text: "synapse hub --host 127.0.0.1 --port 8876 --metrics" },
        {
          id: "dashboard",
          text: "synapse dashboard --uri ws://127.0.0.1:8876 --host 127.0.0.1 --port 8765 --cockpit-dist clients/cockpit/dist",
        },
      ],
    });
    const protectedPlan = buildSetupCommandPlan({
      hubPort: "18876",
      dashboardPort: "18765",
      durableEvidence: true,
      protectedDashboard: true,
    });
    expect(protectedPlan.ok).toBe(true);
    if (!protectedPlan.ok) throw new Error("expected a valid protected setup plan");
    expect(protectedPlan.commands[0]?.text).toContain("--db <HUB_DB_PATH>");
    expect(protectedPlan.commands[1]?.text).toContain("--feeds-db <HUB_DB_PATH>");
    expect(protectedPlan.commands[1]?.text).toContain(
      "--dashboard-access-file <OWNER_ONLY_ACCESS_POLICY_PATH>",
    );
    expect(protectedPlan.commands.every((command) => isSafeSetupCommand(command.text))).toBe(true);
  });

  it.each([
    ["", "8765", "hub-port"],
    ["01024", "8765", "hub-port"],
    ["1023", "8765", "hub-port"],
    ["65536", "8765", "hub-port"],
    ["8876", "port", "dashboard-port"],
    ["8876", "1000", "dashboard-port"],
    ["8876", "99999", "dashboard-port"],
    ["8876", "8876", "port-collision"],
  ] as const)("rejects invalid port pair %s/%s", (hubPort, dashboardPort, error) => {
    expect(buildSetupCommandPlan({
      hubPort,
      dashboardPort,
      durableEvidence: false,
      protectedDashboard: false,
    })).toEqual({ ok: false, error });
  });

  it("fails closed on multiline, broader-bind, inline-secret and discovered-path commands", () => {
    const safe = "synapse hub --host 127.0.0.1 --port 8876 --metrics";
    expect(isSafeSetupCommand(safe)).toBe(true);
    expect(isSafeSetupCommand(`${safe}\nwhoami`)).toBe(false);
    expect(isSafeSetupCommand(`${safe}\rwhoami`)).toBe(false);
    expect(isSafeSetupCommand("synapse hub --port 8876")).toBe(false);
    expect(isSafeSetupCommand(`${safe} --allow-non-loopback`)).toBe(false);
    expect(isSafeSetupCommand(`${safe} --insecure-off-loopback`)).toBe(false);
    expect(isSafeSetupCommand(`${safe} --token secret`)).toBe(false);
    expect(isSafeSetupCommand(`${safe} --dashboard-token=secret`)).toBe(false);
    expect(isSafeSetupCommand(`${safe} --db /home/operator/hub.db`)).toBe(false);
    expect(isSafeSetupCommand(`${safe} --db /tmp/hub.db`)).toBe(false);
    expect(isSafeSetupCommand(`${safe} --db ~/hub.db`)).toBe(false);
    expect(isSafeSetupCommand(`${safe} --db <UNREVIEWED_PATH>`)).toBe(false);
    expect(isSafeSetupCommand(`${safe} --db <HUB_DB_PATH>`)).toBe(true);
  });
});
