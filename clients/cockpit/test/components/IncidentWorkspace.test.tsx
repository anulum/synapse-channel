// @vitest-environment jsdom
// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE CHANNEL — guided incident workspace behaviour tests

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState, type JSX } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { IncidentWorkspace } from "../../src/components/IncidentWorkspace";
import type {
  CockpitSelection,
  IncidentStep,
  ReplayState,
} from "../../src/lib/workspace";

const STORAGE_KEY = "incident:test-principal";

interface HarnessProps {
  readonly selection?: CockpitSelection | null;
  readonly onOpenEvidence?: (selection: CockpitSelection) => void;
  readonly replay?: ReplayState;
}

function Harness({
  selection = { kind: "event", seq: 42 },
  onOpenEvidence = () => undefined,
  replay = { mode: "compare", a: 4, b: 42 },
}: HarnessProps): JSX.Element {
  const [step, setStep] = useState<IncidentStep>("scope");
  return (
    <IncidentWorkspace
      step={step}
      onStepChange={setStep}
      selection={selection}
      replay={replay}
      storageKey={STORAGE_KEY}
      hubVersion="0.99.12"
      configEpoch="epoch-7"
      onOpenEvidence={onOpenEvidence}
    />
  );
}

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("IncidentWorkspace", () => {
  it("guides a local draft from observable scope through explicit evidence to export", async () => {
    const createObjectURL = vi.fn().mockReturnValue("blob:incident");
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("URL", { ...URL, createObjectURL, revokeObjectURL });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    render(<Harness />);

    expect(screen.getByText(/does not write to the hub/u)).toBeTruthy();
    expect((screen.getByRole("button", { name: /continue to evidence/u }) as HTMLButtonElement).disabled).toBe(true);
    await userEvent.type(screen.getByLabelText("Incident title"), "Waiter delivery gap");
    await userEvent.type(screen.getByLabelText("Working hypothesis"), "Provisional routing mismatch");
    await userEvent.click(screen.getByRole("button", { name: /continue to evidence/u }));
    await userEvent.click(screen.getByRole("button", { name: "back to scope" }));
    await userEvent.click(screen.getByRole("button", { name: /continue to evidence/u }));

    expect(screen.getByText("sequence 42")).toBeTruthy();
    expect(screen.getByText("compare 4 to 42")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "add current selection" }));
    expect(screen.getByText("1 explicit reference")).toBeTruthy();
    expect((screen.getByRole("button", { name: "already in cart" }) as HTMLButtonElement).disabled).toBe(true);

    await userEvent.click(screen.getByRole("button", { name: /continue to notes and export/u }));
    await userEvent.click(screen.getByRole("button", { name: "back to evidence" }));
    await userEvent.click(screen.getByRole("button", { name: /continue to notes and export/u }));
    await userEvent.type(screen.getByLabelText("Operator notes"), "Observed at the exact event sequence.");
    await userEvent.click(screen.getByRole("button", { name: "export incident JSON" }));
    expect(createObjectURL).toHaveBeenCalledTimes(1);
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:incident");
    const blob = createObjectURL.mock.calls[0]?.[0];
    expect(blob).toBeInstanceOf(Blob);
    const exported = JSON.parse(await (blob as Blob).text()) as Record<string, unknown>;
    expect(exported["provenance"]).toBe("local-operator-draft");
    expect(exported["authority"]).toBe("not-a-hub-receipt-or-signed-audit-bundle");
    expect(localStorage.getItem(STORAGE_KEY)).toContain("Waiter delivery gap");
  });

  it("opens and removes only the exact cart reference", async () => {
    const onOpenEvidence = vi.fn();
    render(<Harness onOpenEvidence={onOpenEvidence} />);
    await userEvent.type(screen.getByLabelText("Incident title"), "Exact sequence review");
    await userEvent.click(screen.getByRole("button", { name: /continue to evidence/u }));
    await userEvent.click(screen.getByRole("button", { name: "add current selection" }));
    await userEvent.click(screen.getByRole("button", { name: "open" }));
    expect(onOpenEvidence).toHaveBeenCalledWith({ kind: "event", seq: 42 });
    await userEvent.click(screen.getByRole("button", { name: /Remove event sequence 42/u }));
    expect(screen.getByText(/never adds related rows automatically/u)).toBeTruthy();
  });

  it("states an empty selection and restores a principal-scoped draft", async () => {
    const first = render(<Harness selection={null} />);
    await userEvent.type(screen.getByLabelText("Incident title"), "Persisted local draft");
    await userEvent.click(screen.getByRole("button", { name: /continue to evidence/u }));
    expect(screen.getByText("No current selection")).toBeTruthy();
    expect((screen.getByRole("button", { name: "add current selection" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/Select an event, task, route, agent, or project/u)).toBeTruthy();
    first.unmount();

    render(<Harness selection={null} />);
    await userEvent.click(screen.getByRole("button", { name: "scope" }));
    expect((screen.getByLabelText("Incident title") as HTMLInputElement).value).toBe(
      "Persisted local draft",
    );
  });

  it("requires explicit confirmation before replacing the saved draft", async () => {
    render(<Harness />);
    await userEvent.type(screen.getByLabelText("Incident title"), "Draft to replace");
    await userEvent.click(screen.getByRole("button", { name: /continue to evidence/u }));
    await userEvent.click(screen.getByRole("button", { name: "add current selection" }));
    await userEvent.click(screen.getByRole("button", { name: /continue to notes and export/u }));
    await userEvent.click(screen.getByRole("button", { name: "start new incident" }));
    expect(screen.getByText("Replace this local draft?")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "cancel" }));
    expect(screen.queryByText("Replace this local draft?")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "start new incident" }));
    await userEvent.click(screen.getByRole("button", { name: "confirm new incident" }));
    await waitFor(() => expect(screen.getByText("Untitled incident")).toBeTruthy());
    expect((screen.getByLabelText("Incident title") as HTMLInputElement).value).toBe("");
  });

  it("announces unavailable browser storage without breaking the workflow", async () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("quota");
    });
    render(<Harness />);
    await waitFor(() => expect(screen.getByText("browser storage unavailable")).toBeTruthy());
    expect(setItem).toHaveBeenCalled();
    await userEvent.type(screen.getByLabelText("Incident title"), "Unsaved but usable");
    expect((screen.getByLabelText("Incident title") as HTMLInputElement).value).toBe(
      "Unsaved but usable",
    );
  });

  it("shows a live replay context and keeps export gated without scope or evidence", async () => {
    render(<Harness replay={{ mode: "live" }} />);
    await userEvent.click(screen.getByRole("button", { name: "evidence" }));
    expect(screen.getByText("live view")).toBeTruthy();
    expect(
      (screen.getByRole("button", { name: /continue to notes and export/u }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    await userEvent.click(screen.getByRole("button", { name: "notes" }));
    expect(screen.getByText("missing title")).toBeTruthy();
    expect(
      (screen.getByRole("button", { name: "export incident JSON" }) as HTMLButtonElement).disabled,
    ).toBe(true);
  });

  it("labels historical evidence and falls back to a local incident id", async () => {
    vi.stubGlobal("crypto", {});
    render(<Harness replay={{ mode: "history", at: 42 }} />);
    await userEvent.type(screen.getByLabelText("Incident title"), "Historical review");
    await userEvent.click(screen.getByRole("button", { name: /continue to evidence/u }));
    expect(screen.getByText("history at sequence 42")).toBeTruthy();
    await waitFor(() => {
      const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "{}") as { id?: string };
      expect(saved.id).toMatch(/^local-/u);
    });
  });
});
