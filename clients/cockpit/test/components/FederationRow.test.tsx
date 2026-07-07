// @vitest-environment jsdom
// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — federation row behaviour tests

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { FederationRow } from "../../src/components/FederationRow";
import { parseFederation, type FederationState } from "../../src/lib/federation";

afterEach(cleanup);

function stateOf(raw: unknown, status: FederationState["status"], error: string | null = null): FederationState {
  return { data: parseFederation(raw), status, fetchedAt: 1, error };
}

describe("FederationRow", () => {
  it("states an absent posture surface as a fact, with the version pin when known", () => {
    render(
      <FederationRow
        state={{ data: null, status: "absent", fetchedAt: null, error: null }}
        hubVersion="0.98.4"
        configEpoch="deadbeefcafe"
      />,
    );
    expect(screen.getByText("posture surface not served (/federation.json)")).toBeTruthy();
    expect(screen.getByText(/v0\.98\.4/).textContent).toContain("epoch deadbeef");
  });

  it("hides the pin chip entirely when the hub predates it", () => {
    render(<FederationRow state={{ data: null, status: "connecting", fetchedAt: null, error: null }} />);
    expect(screen.getByText("waiting for the hub")).toBeTruthy();
    expect(document.querySelector(".fed-row__epoch")).toBeNull();
  });

  it("states a failed feed with its reason", () => {
    render(
      <FederationRow state={{ data: null, status: "error", fetchedAt: null, error: "hub returned 500" }} />,
    );
    expect(screen.getByText("feed failed: hub returned 500")).toBeTruthy();
  });

  it("shows hub identity, peer lifecycle dots, and quiet no-peering honesty", () => {
    render(
      <FederationRow
        state={stateOf(
          {
            hub_id: "hub-a",
            domain: "alpha.example",
            peerings: [
              { domain: "beta.example", state: "active" },
              { domain: "gamma.example", state: "revoked" },
              { domain: "delta.example", state: "expired" },
            ],
            namespaces: [],
          },
          "live",
        )}
      />,
    );
    expect(screen.getByText("hub-a")).toBeTruthy();
    expect(document.querySelectorAll(".fed-peer__dot--active")).toHaveLength(1);
    expect(document.querySelectorAll(".fed-peer__dot--revoked")).toHaveLength(1);
    expect(document.querySelectorAll(".fed-peer__dot--expired")).toHaveLength(1);
    cleanup();
    render(
      <FederationRow
        state={stateOf({ hub_id: "hub-a", domain: "", peerings: [], namespaces: [] }, "live")}
      />,
    );
    expect(screen.getByText("no peerings imported")).toBeTruthy();
  });

  it("raises a partition to an alert naming the contested namespace", () => {
    render(
      <FederationRow
        state={stateOf(
          {
            hub_id: "hub-a",
            domain: "alpha.example",
            peerings: [{ domain: "beta.example", state: "active" }],
            namespaces: [
              { namespace: "quantum", outcome: "partitioned", owner_hub: "", contesting: ["hub-a", "hub-b"] },
            ],
          },
          "live",
        )}
      />,
    );
    const alert = screen.getByRole("alert");
    expect(alert.className).toContain("fed-row--partitioned");
    expect(alert.textContent).toContain("quantum (hub-a vs hub-b)");
    expect(alert.textContent).toContain("claims there are refused until the split heals");
  });
});
