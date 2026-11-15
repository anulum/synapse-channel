// @vitest-environment jsdom
// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE CHANNEL — a peer scope: its choice, its band, its tags and the panels it does not observe

import type { JSX } from "react";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { InspectorPanel } from "../../src/components/InspectorPanel";
import { PeerBand, PeerFeedPanel } from "../../src/components/PeerScope";
import { ScopePicker } from "../../src/components/ScopePicker";
import { ScopeProvider, ScopeTag } from "../../src/components/ScopeTag";
import { WHOLE_HUB, type CockpitScope } from "../../src/lib/scope";
import type { PeerBlock } from "../../src/types";

const EAST: CockpitScope = { kind: "peer", hub: "east" };
const COUNTS = {
  tasksTotal: 3,
  agentsTotal: 2,
  tasksWithoutProject: 0,
  agentsWithoutNamespace: 0,
  namespaces: [{ namespace: "ALPHA", tasks: 3, agents: 2 }],
  peers: ["east"],
};
const REACHED: PeerBlock = { hubId: "east", observed: true, reachable: true, cursor: 6, lag: null, error: "" };

afterEach(cleanup);

function inPeer(children: JSX.Element): JSX.Element {
  return (
    <ScopeProvider scope={EAST} travelling={false}>
      {children}
    </ScopeProvider>
  );
}

describe("the selector's peers", () => {
  it("lists the observed hubs under their own heading and reports a peer chosen", async () => {
    const onChange = vi.fn();
    render(<ScopePicker scope={WHOLE_HUB} counts={COUNTS} onChange={onChange} />);
    const group = screen.getByRole("group", { name: "Observed hubs (advisory)" });

    expect(group.textContent).toBe("observed: east");
    await userEvent.selectOptions(screen.getByRole("combobox"), "peer:east");
    expect(onChange).toHaveBeenCalledWith(EAST);
  });

  it("marks a peer scope apart from a project scope", () => {
    render(<ScopePicker scope={EAST} counts={null} onChange={vi.fn()} />);
    const select = screen.getByRole("combobox") as HTMLSelectElement;

    expect(select.value).toBe("peer:east");
    expect(select.parentElement?.className).toBe("hud__scope hud__scope--on hud__scope--peer");
  });
});

describe("the tags of a peer scope", () => {
  it("says advisory on a panel the snapshot fills and not observed on every other", () => {
    render(inPeer(<><ScopeTag snapshot replayed /><ScopeTag /><ScopeTag scoped={false} /></>));

    const advisory = screen.getByText("advisory · observed from east");
    expect(advisory.getAttribute("title")).toBe(
      "This panel shows the observed hub's folded state: advisory, read only, never this hub's work.",
    );
    expect(screen.getAllByText("not observed")).toHaveLength(2);
  });
});

describe("the band of a peer scope", () => {
  it("renders nothing in a scope of this hub", () => {
    const { container } = render(<PeerBand peer={REACHED} />);
    expect(container.textContent).toBe("");
  });

  it.each<[PeerBlock | null, string]>([
    [null, "waiting for the first pull"],
    [{ ...REACHED, observed: false, reachable: false }, "this dashboard does not observe east"],
    [{ ...REACHED, reachable: false, error: "connection refused" }, "not reached: connection refused"],
    [REACHED, "reached · cursor 6 · lag unknown"],
  ])("states the pull: %j", (peer, status) => {
    render(inPeer(<PeerBand peer={peer} />));
    const band = screen.getByRole("status");

    expect(band.textContent).toBe(`Advisory: observed from east. Read only: writes are off in a peer scope.${status}`);
    expect(band.className.includes("peer-band--down")).toBe(peer?.reachable !== true);
  });
});

describe("the panels a peer scope does not observe", () => {
  it("keep their name and say why they show nothing", () => {
    render(inPeer(<PeerFeedPanel label="Findings stream" title="Findings" hub="east" />));
    const region = screen.getByRole("region", { name: "Findings stream" });

    expect(region.textContent).toContain("not observed");
    expect(screen.getByRole("note").textContent).toBe(
      "Not observed for east: a peer's feeds are not pulled. Leave the peer scope to see this hub's.",
    );
  });

  it("replace an inspector feed tab with the notice", () => {
    render(inPeer(
      <InspectorPanel
        tab="log"
        events={[]}
        fleetView="web"
        onFleetViewChange={vi.fn()}
        fleetSelection={null}
        onFleetSelectionChange={vi.fn()}
        onTabChange={vi.fn()}
        onSelectTask={vi.fn()}
        prefill={null}
      />,
    ));

    expect(screen.getByRole("region", { name: "Signal log" }).textContent).toContain("Not observed for east");
    expect(screen.getByRole("tabpanel").id).toBe("inspector-panel");
  });
});
