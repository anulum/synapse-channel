// @vitest-environment jsdom
// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the apply step: preview, confirm, apply once, and say only what the read-back confirms

import { act, cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SetupApplyStep } from "../../src/components/SetupApplyStep";
import { CockpitI18nProvider } from "../../src/context/CockpitI18n";
import type {
  IssuedSetupPlan,
  SetupAnswer,
  SetupApplySource,
  SetupReceipt,
} from "../../src/lib/setupApply";

const PLAN: IssuedSetupPlan = {
  planId: "plan_0123456789abcdefghijkl",
  planDigest: "0123456789ab".padEnd(64, "f"),
  confirmationNonce: "nonce_0123456789abcdefghijk",
  requestId: "7f1d3c3e-8a51-4c43-9a55-3f1f8a2a4b10",
  profile: "local-single-user",
  expiresAt: 1_800_000_300,
  effects: [
    { id: "establish_local_loopback_hub", disposition: "planned", disruption: "service_start", reversible: true, processId: 4242 },
    { id: "establish_identity_waiter", disposition: "planned", disruption: "configuration_change", reversible: false, processId: null },
  ],
};

const APPLIED: SetupReceipt = {
  sequence: 4,
  requestId: PLAN.requestId,
  planId: PLAN.planId,
  capability: "setup_apply",
  outcome: "applied",
  reason: "none",
  effects: [
    { kind: "establish_local_loopback_hub", target: "synapse-hub.service", before: "unsatisfied", after: "satisfied" },
    { kind: "establish_identity_waiter", target: "synapse-arm@.service", before: "unsatisfied", after: "satisfied" },
  ],
  receiptDigest: "b".repeat(64),
};

const REPLAYED: SetupReceipt = { ...APPLIED, sequence: 6, outcome: "refused", reason: "plan_replayed", effects: [] };

function source(overrides: Partial<SetupApplySource> = {}): SetupApplySource {
  return {
    plan: vi.fn(async (): Promise<SetupAnswer<IssuedSetupPlan>> => ({ kind: "ok", value: PLAN })),
    apply: vi.fn(async (): Promise<SetupAnswer<SetupReceipt>> => ({ kind: "ok", value: APPLIED })),
    readBack: vi.fn(async (_plan: IssuedSetupPlan, answered: SetupReceipt | null) => answered),
    ...overrides,
  };
}

function renderStep(given: SetupApplySource): ReturnType<typeof render> {
  return render(
    <CockpitI18nProvider>
      <SetupApplyStep source={given} />
    </CockpitI18nProvider>,
  );
}

async function previewAndConfirm(user: ReturnType<typeof userEvent.setup>): Promise<void> {
  await user.click(screen.getByRole("button", { name: "Preview the plan" }));
  await user.click(await screen.findByLabelText("I confirm this plan. Apply it once on this host."));
}

beforeEach(() => {
  history.replaceState(null, "", "/cockpit/?lang=en#panel=attention");
});

afterEach(() => {
  cleanup();
  localStorage.clear();
  vi.restoreAllMocks();
});

describe("SetupApplyStep", () => {
  it("shows the plan before anything is applied: each change, its disruption, and the hub it restarts", async () => {
    const user = userEvent.setup();
    const given = source();
    renderStep(given);

    expect(screen.queryByRole("button", { name: "Apply the plan" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Preview the plan" }));

    const plan = await screen.findByRole("region", { name: "Planned changes" });
    expect(plan.textContent).toContain("Profile local-single-user · plan 0123456789ab · valid until");
    const rows = Array.from(plan.querySelectorAll("li"), (row) => row.textContent);
    expect(rows).toEqual([
      "Run the local loopback hub as a user serviceservice start · can be undone",
      "Run the identity waiter as a user serviceconfiguration change · cannot be undone",
    ]);
    expect(plan.textContent).toContain("The hub (process 4242) is restarted.");
    expect((screen.getByRole("button", { name: "Apply the plan" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByRole("button", { name: "Preview a new plan" })).toBeTruthy();
    expect(given.apply).not.toHaveBeenCalled();
  });

  it("applies the confirmed plan once and says applied only after the read-back confirms it", async () => {
    const user = userEvent.setup();
    const given = source();
    renderStep(given);
    await previewAndConfirm(user);

    await user.click(screen.getByRole("button", { name: "Apply the plan" }));

    const answer = await screen.findByRole("region", { name: "What the dashboard did" });
    expect(given.apply).toHaveBeenCalledWith(PLAN);
    expect(given.readBack).toHaveBeenCalledWith(PLAN, APPLIED);
    expect(answer.textContent).toContain("Receipt 4 · outcome applied · reason none");
    expect(answer.textContent).toContain("unsatisfied → satisfied");
    expect(screen.getByRole("status").textContent).toBe(
      "Read back: applied. The receipt chain holds this apply, and every change is in place.",
    );
    const confirm = screen.getByLabelText("I confirm this plan. Apply it once on this host.") as HTMLInputElement;
    expect(confirm.checked).toBe(false);
    expect((screen.getByRole("button", { name: "Apply the plan" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("shows a replay of the same plan as refused, with the chain's own receipt", async () => {
    const user = userEvent.setup();
    const apply = vi
      .fn<SetupApplySource["apply"]>()
      .mockResolvedValueOnce({ kind: "ok", value: APPLIED })
      .mockResolvedValueOnce({ kind: "refused", status: 409, detail: "setup apply refused: plan_replayed" });
    const readBack = vi.fn<SetupApplySource["readBack"]>(async (_plan, answered) => answered ?? REPLAYED);
    renderStep(source({ apply, readBack }));
    await previewAndConfirm(user);
    await user.click(screen.getByRole("button", { name: "Apply the plan" }));
    await screen.findByText(/Read back: applied/u);

    await user.click(screen.getByLabelText("I confirm this plan. Apply it once on this host."));
    await user.click(screen.getByRole("button", { name: "Apply the plan" }));

    const answer = await screen.findByText("The dashboard refused: setup apply refused: plan_replayed");
    expect(answer).toBeTruthy();
    expect(apply).toHaveBeenNthCalledWith(2, PLAN);
    expect(readBack).toHaveBeenLastCalledWith(PLAN, null);
    expect(screen.getByRole("status").textContent).toBe(
      "Read back: refused. The receipt chain records why; no change from this plan was kept.",
    );
    const region = screen.getByRole("region", { name: "What the dashboard did" });
    expect(region.textContent).toContain("Receipt 6 · outcome refused · reason plan_replayed");
  });

  it("says unknown when the chain does not hold what the apply answered", async () => {
    const user = userEvent.setup();
    renderStep(source({ readBack: vi.fn(async () => null) }));
    await previewAndConfirm(user);

    await user.click(screen.getByRole("button", { name: "Apply the plan" }));

    expect((await screen.findByRole("status")).textContent).toBe(
      "Read back: unknown. The receipt chain does not confirm the change; check the host before relying on it.",
    );
  });

  it("reads the chain back even when the apply got no answer", async () => {
    const user = userEvent.setup();
    const readBack = vi.fn(async () => APPLIED);
    renderStep(source({ apply: vi.fn(async () => ({ kind: "failed", detail: "offline" }) as const), readBack }));
    await previewAndConfirm(user);

    await user.click(screen.getByRole("button", { name: "Apply the plan" }));

    expect(await screen.findByText("The dashboard could not be asked: offline")).toBeTruthy();
    expect(readBack).toHaveBeenCalledWith(PLAN, null);
    expect(screen.getByRole("status").textContent).toContain("Read back: applied.");
  });

  it("says why no plan was issued, and offers nothing to apply", async () => {
    const user = userEvent.setup();
    renderStep(source({ plan: vi.fn(async () => ({ kind: "refused", status: 404, detail: "setup not served: unarmed" }) as const) }));

    await user.click(screen.getByRole("button", { name: "Preview the plan" }));

    expect((await screen.findByRole("alert")).textContent).toBe("The dashboard refused: setup not served: unarmed");
    expect(screen.queryByRole("region", { name: "Planned changes" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Apply the plan" })).toBeNull();
  });

  it("says when the dashboard could not be asked for a plan", async () => {
    const user = userEvent.setup();
    renderStep(source({ plan: vi.fn(async () => ({ kind: "failed", detail: "the answer was not a plan" }) as const) }));

    await user.click(screen.getByRole("button", { name: "Preview the plan" }));

    expect((await screen.findByRole("alert")).textContent).toBe(
      "The dashboard could not be asked: the answer was not a plan",
    );
  });

  it("says nothing needs changing, and that the host was left as it was", async () => {
    const user = userEvent.setup();
    const satisfied: SetupReceipt = { ...APPLIED, outcome: "already_satisfied", effects: [] };
    renderStep(
      source({
        plan: vi.fn(async () => ({ kind: "ok", value: { ...PLAN, effects: [] } }) as const),
        apply: vi.fn(async () => ({ kind: "ok", value: satisfied }) as const),
      }),
    );
    await previewAndConfirm(user);
    expect(screen.getByText("Nothing to change: this host already holds the profile.")).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "Apply the plan" }));

    expect((await screen.findByRole("status")).textContent).toBe(
      "Read back: nothing changed. The host already held the profile.",
    );
  });

  it("offers no confirmation for a plan with a change the dashboard cannot make", async () => {
    const user = userEvent.setup();
    const blocked = {
      ...PLAN,
      effects: [{ id: "install_synapse_package", disposition: "blocked", disruption: "host_migration", reversible: false, processId: null }],
    };
    renderStep(source({ plan: vi.fn(async () => ({ kind: "ok", value: blocked }) as const) }));

    await user.click(screen.getByRole("button", { name: "Preview the plan" }));

    expect((await screen.findByRole("alert")).textContent).toContain("so it cannot be applied");
    expect(screen.getByText("cannot be made here")).toBeTruthy();
    expect(screen.queryByLabelText("I confirm this plan. Apply it once on this host.")).toBeNull();
    expect(screen.queryByRole("button", { name: "Apply the plan" })).toBeNull();
  });

  it("names a change or a disruption this cockpit does not know by its own word", async () => {
    const user = userEvent.setup();
    const novel = {
      ...PLAN,
      effects: [{ id: "establish_new_thing", disposition: "planned", disruption: "new_kind", reversible: true, processId: null }],
    };
    renderStep(source({ plan: vi.fn(async () => ({ kind: "ok", value: novel }) as const) }));

    await user.click(screen.getByRole("button", { name: "Preview the plan" }));

    const plan = await screen.findByRole("region", { name: "Planned changes" });
    expect(plan.querySelector("li")?.textContent).toBe("establish_new_thingnew_kind · can be undone");
  });

  it("forgets the last answer when a new plan is previewed", async () => {
    const user = userEvent.setup();
    renderStep(source());
    await previewAndConfirm(user);
    await user.click(screen.getByRole("button", { name: "Apply the plan" }));
    await screen.findByRole("region", { name: "What the dashboard did" });

    await user.click(screen.getByRole("button", { name: "Preview a new plan" }));

    await screen.findByRole("region", { name: "Planned changes" });
    expect(screen.queryByRole("region", { name: "What the dashboard did" })).toBeNull();
  });

  it("says it is waiting while the dashboard is asked, and offers no second request meanwhile", async () => {
    const user = userEvent.setup();
    let release: (answer: SetupAnswer<IssuedSetupPlan>) => void = () => {};
    const pending = new Promise<SetupAnswer<IssuedSetupPlan>>((resolve) => {
      release = resolve;
    });
    renderStep(source({ plan: vi.fn(() => pending) }));

    await user.click(screen.getByRole("button", { name: "Preview the plan" }));

    expect(screen.getByRole("status").textContent).toBe("Waiting for the dashboard…");
    expect((screen.getByRole("button", { name: "Preview the plan" }) as HTMLButtonElement).disabled).toBe(true);
    await act(async () => {
      release({ kind: "ok", value: PLAN });
      await pending;
    });
    expect(screen.queryByText("Waiting for the dashboard…")).toBeNull();
    expect(screen.getByRole("region", { name: "Planned changes" })).toBeTruthy();
  });
});
