// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the operations tab over a hub that journalled an operation in every state

import { expect, test, type APIRequestContext, type Locator, type Page } from "@playwright/test";
import axe from "axe-core";

import { startHarness, stop, type Started } from "./fleetHarness";

const bearer = process.env["SYNAPSE_COCKPIT_E2E_TOKEN"];
if (!bearer) throw new Error("SYNAPSE_COCKPIT_E2E_TOKEN is required");

/** The state the operation report gives each operation the harness's hub journalled. */
const EXPECTED: Readonly<Record<string, string>> = {
  "op-verified": "verified",
  "op-sealed": "sealed",
  "op-ended": "ended, unreconciled",
  "op-unknown": "outcome reported, unknown",
  "op-open": "attempt open",
  "op-reconciled": "reconciled",
  "op-refused": "refused: not_approved",
  "op-refused-then-approved": "approved, no attempt",
  "op-awaiting": "awaiting approval",
  "op-approved": "approved, no attempt",
  "op-rejected": "approval rejected",
  "op-declared": "declared",
};

interface ServedLink {
  readonly seq: number;
  readonly kind: string;
  readonly actor: string;
  readonly detail: Readonly<Record<string, unknown>>;
}

interface ServedChain {
  readonly state: string;
  readonly custody: string;
  readonly chain: readonly ServedLink[];
  readonly intent_sha256: string;
  readonly recomputed_sha256: string;
  readonly intent_fields: readonly { readonly field: string; readonly canonical: string }[];
}

interface AxeWindow extends Window {
  readonly axe: {
    run(context: { readonly include: readonly (readonly string[])[] }): Promise<{
      readonly violations: readonly { readonly id: string }[];
    }>;
  };
}

async function served<T>(request: APIRequestContext, origin: string, path: string): Promise<T> {
  const response = await request.get(`${origin}${path}`, { headers: { Authorization: `Bearer ${bearer}` } });
  expect(response.status(), path).toBe(200);
  return (await response.json()) as T;
}

/** Check that the chain the tab shows is the served chain, link by link, and claims nothing more. */
async function expectChainAsServed(section: Locator, chain: ServedChain): Promise<void> {
  await expect(section.locator(".op-chain__head .op-state__text")).toHaveText(chain.state);
  const links = section.locator(".op-link");
  await expect(links).toHaveCount(chain.chain.length);
  for (const [index, link] of chain.chain.entries()) {
    const shown = links.nth(index);
    await expect(shown.locator(".op-link__seq")).toHaveText(`seq ${link.seq}`);
    await expect(shown.locator(".op-link__actor")).toHaveText(`by ${link.actor || "not recorded"}`);
    await expect(shown.locator("dt")).toHaveText(Object.keys(link.detail));
  }
  await expect(section).toContainText(`custody: ${chain.custody}`);
  await expect(section).not.toContainText(/success/iu);
}

/** Check the approval view against the served chain: the fields, both digests, the words and the commands. */
async function expectApprovalAsServed(section: Locator, chain: ServedChain): Promise<void> {
  const approval = section.getByRole("region", { name: "Approval" });
  await expect(approval.locator("dt")).toHaveText(chain.intent_fields.map(item => item.field));
  await expect(approval.locator(".op-intent code")).toHaveText(chain.intent_fields.map(item => item.canonical));
  expect(chain.recomputed_sha256).toBe(chain.intent_sha256);
  await expect(approval).toContainText(`recorded digest ${chain.intent_sha256}`);
  await expect(approval).toContainText(`recomputed digest ${chain.recomputed_sha256}`);
  await expect(approval.locator(".op-words__text")).toHaveText("The request gave no reason.");
  const commands = approval.locator(".op-command__text");
  await expect(commands).toHaveCount(2);
  for (const command of await commands.allTextContents()) {
    expect(command).toContain(`--subject sha256:${chain.intent_sha256}`);
    expect(command).not.toMatch(/token/iu);
  }
}

async function useTheme(page: Page, theme: "dark" | "light"): Promise<void> {
  const current = await page.locator("html").getAttribute("data-theme");
  if ((current === "light" ? "light" : "dark") !== theme) {
    await page.getByRole("button", { name: /Switch to (?:dark|light) theme/u }).click();
  }
  await expect
    .poll(() =>
      page.locator(".op-link").first().evaluate(link => {
        const probe = document.createElement("span");
        probe.style.color = "var(--panel-2)";
        document.body.append(probe);
        const expected = getComputedStyle(probe).color;
        probe.remove();
        return getComputedStyle(link).backgroundColor === expected;
      }),
    )
    .toBe(true);
}

async function expectAccessible(page: Page, width: number, theme: "dark" | "light"): Promise<void> {
  await page.setViewportSize({ width, height: width < 600 ? 844 : 900 });
  await useTheme(page, theme);
  await expect(page.getByRole("region", { name: "Governed operations" })).toBeVisible();
  const result = await page.evaluate(() => (window as unknown as AxeWindow).axe.run({ include: [[".ops"]] }));
  expect(result.violations, `${width}/${theme}: ${JSON.stringify(result.violations)}`).toEqual([]);
  const shot = await page.getByRole("region", { name: "Governed operations" }).screenshot();
  await test.info().attach(`operations-${width}-${theme}`, { body: shot, contentType: "image/png" });
  await test.info().attach(`page-${width}-${theme}`, { body: await page.screenshot(), contentType: "image/png" });
}

test("the operations tab shows each journalled operation's state and chain, and no link claims more than its row", async ({
  page,
}) => {
  test.setTimeout(150_000);
  let harness: Started | undefined;
  try {
    const started = await startHarness({ operations: true });
    harness = started.harness;
    const origin = new URL(started.url).origin;
    await page.addInitScript({ content: axe.source });
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(started.url);
    await page.getByLabel("Dashboard bearer token").fill(bearer);
    await page.getByRole("button", { name: "unlock cockpit" }).click();
    await expect(page.getByRole("banner").getByText("live", { exact: true })).toBeVisible();

    await page.getByRole("tab", { name: "operations" }).click();
    const tab = page.getByRole("region", { name: "Governed operations" });
    await expect(tab).toContainText("12 of 12 declared operations, newest first");
    const list = await served<{ operations: { operation_id: string; state: string }[] }>(page.request, origin, "/operations.json");
    expect(Object.fromEntries(list.operations.map(row => [row.operation_id, row.state]))).toEqual(EXPECTED);

    for (const [operation, state] of Object.entries(EXPECTED)) {
      await test.step(operation, async () => {
        const row = tab.getByRole("button", { name: new RegExp(`^${operation} `, "u") });
        await expect(tab.getByRole("list", { name: new RegExp(`^${state}`, "u") }).getByRole("button", { name: new RegExp(`^${operation} `, "u") })).toBeVisible();
        await row.click();
        await expect(row).toHaveAttribute("aria-pressed", "true");
        const section = page.getByRole("region", { name: `Chain of operation ${operation}` });
        await expect(section.locator(".op-link").first()).toBeVisible();
        const chain = await served<ServedChain>(page.request, origin, `/operations.json?operation=${operation}`);
        expect(chain.state).toBe(state);
        await expectChainAsServed(section, chain);
        if (state === "awaiting approval") await expectApprovalAsServed(section, chain);
        else await expect(section.getByRole("region", { name: "Approval" })).toHaveCount(0);
      });
    }

    await tab.getByRole("button", { name: /^op-verified / }).click();
    const verified = page.getByRole("region", { name: "Chain of operation op-verified" });
    const first = await served<ServedChain>(page.request, origin, "/operations.json?operation=op-verified");
    const seq = first.chain[0]?.seq ?? -1;
    await verified.getByRole("button", { name: `open evidence (seq ${seq})` }).click();
    await expect(verified.getByRole("status")).toHaveText(new RegExp(`^seq ${seq} is committed to root [0-9a-f]{64}$`, "u"));

    await test.step("a refusal opens its chain from the attention queue", async () => {
      await page.getByRole("tab", { name: "attention" }).click();
      const queue = page.getByRole("region", { name: "Fleet attention queue" });
      const refusal = queue.getByRole("listitem").filter({ hasText: "op-refused" }).filter({ hasText: "refused: not_approved" });
      await expect(refusal).toHaveCount(1, { timeout: 45_000 });
      await expect(queue.getByRole("listitem").filter({ hasText: "op-verified" })).toHaveCount(0);
      await refusal.getByRole("button", { name: /open operation/u }).click();
      await expect(page.getByRole("tab", { name: "operations" })).toHaveAttribute("aria-selected", "true");
      await expect(page.getByRole("region", { name: "Chain of operation op-refused" })).toBeVisible();
      expect(new URL(page.url()).searchParams.get("operation")).toBe("op-refused");
      await page.goBack();
      await expect(page.getByRole("tab", { name: "attention" })).toHaveAttribute("aria-selected", "true");
    });
    await page.getByRole("tab", { name: "operations" }).click();
    await tab.getByRole("button", { name: /^op-verified / }).click();

    for (const width of [1440, 1024, 390]) {
      for (const theme of ["dark", "light"] as const) {
        await test.step(`${width} pixels, ${theme}`, () => expectAccessible(page, width, theme));
      }
    }

    await page.setViewportSize({ width: 1440, height: 900 });
    await tab.getByRole("button", { name: /^op-awaiting / }).click();
    await expect(page.getByRole("region", { name: "Approval" })).toBeVisible();
    for (const width of [1440, 1024, 390]) {
      for (const theme of ["dark", "light"] as const) {
        await test.step(`approval view, ${width} pixels, ${theme}`, () => expectAccessible(page, width, theme));
      }
    }
  } finally {
    await stop(harness);
  }
});
