// @vitest-environment jsdom
// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — read-only setup assistant behaviour tests

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SetupAssistant } from "../../src/components/SetupAssistant";
import { CockpitI18nProvider } from "../../src/context/CockpitI18n";
import type { SetupEvidence } from "../../src/lib/setupAssistant";

const EVIDENCE: SetupEvidence = {
  access: "ready",
  snapshot: "live",
  transport: "live",
  optionalFeeds: ["live", "absent"],
  loopbackOrigin: true,
};

let writeText: ReturnType<typeof vi.fn>;

beforeEach(() => {
  history.replaceState(null, "", "/cockpit/?lang=en#panel=attention");
  writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText },
  });
});

afterEach(() => {
  cleanup();
  localStorage.clear();
  vi.restoreAllMocks();
});

function renderAssistant(
  evidence: SetupEvidence = EVIDENCE,
  onClose = vi.fn(),
): ReturnType<typeof vi.fn> {
  render(
    <CockpitI18nProvider>
      <SetupAssistant open evidence={evidence} onClose={onClose} />
    </CockpitI18nProvider>,
  );
  return onClose;
}

describe("SetupAssistant", () => {
  it("renders nothing while closed", () => {
    render(
      <CockpitI18nProvider>
        <SetupAssistant open={false} evidence={EVIDENCE} onClose={() => {}} />
      </CockpitI18nProvider>,
    );
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("starts with honest preflight evidence and no side effect", () => {
    const storageWrite = vi.spyOn(Storage.prototype, "setItem");
    const startingUrl = location.href;
    renderAssistant();
    expect(screen.getByRole("heading", { name: "Setup assistant" })).toBeTruthy();
    expect(screen.getByText("Cockpit bundle")).toBeTruthy();
    expect(screen.getAllByText("configured")).toHaveLength(4);
    expect(screen.getByText("installed")).toBeTruthy();
    expect(document.body.textContent).not.toContain("Bearer ");
    expect(document.body.textContent).not.toContain("--token");
    expect(writeText).not.toHaveBeenCalled();
    expect(storageWrite).not.toHaveBeenCalled();
    expect(location.href).toBe(startingUrl);
    expect(document.activeElement).toBe(screen.getByLabelText("Close setup assistant"));
  });

  it("validates the profile and never offers a broader bind", async () => {
    renderAssistant();
    await userEvent.click(screen.getByRole("button", { name: /Profile/u }));
    expect(screen.getByText("127.0.0.1 · locked")).toBeTruthy();
    expect(screen.queryByText(/0\.0\.0\.0/u)).toBeNull();
    const hubPort = screen.getByLabelText("Hub port");
    await userEvent.clear(hubPort);
    await userEvent.type(hubPort, "1023");
    expect(screen.getByRole("alert").textContent).toContain("1024 to 65535");
    expect((screen.getByRole("button", { name: "Commands" }) as HTMLButtonElement).disabled).toBe(true);
    await userEvent.clear(hubPort);
    await userEvent.type(hubPort, "8876");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("adds inert placeholders and copies only after an explicit click", async () => {
    renderAssistant();
    await userEvent.click(screen.getByRole("button", { name: /Profile/u }));
    await userEvent.click(screen.getByLabelText("Add durable evidence placeholders"));
    await userEvent.click(screen.getByLabelText("Add protected dashboard policy placeholder"));
    await userEvent.click(screen.getByRole("button", { name: "Commands" }));
    expect(writeText).not.toHaveBeenCalled();
    const previews = screen.getAllByText(/^synapse /u);
    expect(previews[0]?.textContent).toContain("--db <HUB_DB_PATH>");
    expect(previews[1]?.textContent).toContain("--dashboard-access-file <OWNER_ONLY_ACCESS_POLICY_PATH>");
    expect(document.body.textContent).not.toContain("--dashboard-token");
    const copyButtons = screen.getAllByRole("button", { name: "Copy command" });
    await userEvent.click(copyButtons[0] as HTMLButtonElement);
    expect(writeText).toHaveBeenCalledOnce();
    expect(writeText).toHaveBeenCalledWith(previews[0]?.textContent);
    expect(screen.getByText("Command copied")).toBeTruthy();
  });

  it("reports clipboard failure without introducing a hidden fallback", async () => {
    writeText.mockRejectedValueOnce(new Error("denied"));
    renderAssistant();
    await userEvent.click(screen.getByRole("button", { name: "Commands" }));
    await userEvent.click(screen.getAllByRole("button", { name: "Copy command" })[0] as HTMLButtonElement);
    expect(screen.getByText(/Clipboard unavailable/u)).toBeTruthy();
    expect(document.querySelector("textarea")).toBeNull();
  });

  it("shows proof, closes predictably, and traps keyboard focus", async () => {
    const onClose = renderAssistant({
      ...EVIDENCE,
      access: "unavailable",
      snapshot: "stale",
      transport: "fallback",
      optionalFeeds: ["absent"],
      loopbackOrigin: false,
    });
    await userEvent.click(screen.getByRole("button", { name: "Verify" }));
    expect(screen.getAllByText("check required")).toHaveLength(3);
    expect(screen.getAllByText("optional surface absent")).toHaveLength(2);
    const done = screen.getByRole("button", { name: "Review complete" });
    done.focus();
    fireEvent.keyDown(window, { key: "Tab" });
    expect(document.activeElement).toBe(screen.getByLabelText("Close setup assistant"));
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledOnce();
    const veil = document.querySelector(".setup-veil");
    fireEvent.mouseDown(veil as Element);
    expect(onClose).toHaveBeenCalledTimes(2);
  });
});
