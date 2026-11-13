// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — real Chromium Studio board-column acceptance

import { expect, test, type APIRequestContext } from "@playwright/test";

const bearer = process.env["SYNAPSE_COCKPIT_E2E_TOKEN"];
if (bearer === undefined || bearer === "") {
  throw new Error("SYNAPSE_COCKPIT_E2E_TOKEN is required by the browser gate");
}

const headers = { Authorization: `Bearer ${bearer}` };

async function declareTask(
  request: APIRequestContext,
  id: string,
  title: string,
  dependsOn: readonly string[] = [],
): Promise<void> {
  const response = await request.post("/task", {
    headers,
    data: { id, title, depends_on: dependsOn },
  });
  expect(response.status()).toBe(200);
  expect(await response.json()).toEqual(expect.objectContaining({ ok: true, status: "accepted" }));
}

async function updateTask(
  request: APIRequestContext,
  id: string,
  status: string,
): Promise<void> {
  const response = await request.post("/task/update", {
    headers,
    data: { id, status, note: "Studio board browser acceptance" },
  });
  expect(response.status()).toBe(200);
  expect(await response.json()).toEqual(expect.objectContaining({ ok: true, status: "accepted" }));
}

test("the protected Studio renders exact read-only columns on desktop and mobile", async ({
  page,
  request,
}) => {
  const suffix = Date.now().toString(36);
  const openId = `studio-open-${suffix}`;
  const workingId = `studio-working-${suffix}`;
  const blockedId = `studio-blocked-${suffix}`;
  const doneId = `studio-done-${suffix}`;
  const payload = `<img id="studio-pwn" onerror="window.__studioPwned=1">\"'`;

  await declareTask(request, openId, payload);
  await declareTask(request, workingId, "Working in Chromium");
  await updateTask(request, workingId, "in_progress");
  await declareTask(request, blockedId, "Blocked in Chromium", [workingId]);
  await updateTask(request, blockedId, "blocked");
  await declareTask(request, doneId, "Closed in Chromium");
  await updateTask(request, doneId, "done");

  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  await page.setExtraHTTPHeaders(headers);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.setViewportSize({ width: 1440, height: 1000 });
  const snapshot = page.waitForResponse(
    (response) => new URL(response.url()).pathname === "/studio.json" && response.status() === 200,
  );
  await page.goto("/studio/command");
  expect((await snapshot).request().headers()["authorization"]).toBe(`Bearer ${bearer}`);

  const board = page.locator("#cc-board-columns");
  await expect(board.locator('.syn-board-column[data-column="open"]')).toContainText(openId);
  await expect(board.locator('.syn-board-column[data-column="working"]')).toContainText(workingId);
  await expect(board.locator('.syn-board-column[data-column="blocked"]')).toContainText(blockedId);
  // The bounded snapshot counts closed tasks without loading them: the Closed
  // column states how many it did not receive and draws no card for them.
  const closedColumn = board.locator('.syn-board-column[data-column="closed"]');
  await expect(closedColumn.locator(".syn-board-column__omitted")).toHaveText(/^[1-9]\d* not loaded$/u);
  const notLoaded = Number.parseInt(
    (await closedColumn.locator(".syn-board-column__omitted").textContent()) ?? "",
    10,
  );
  await expect(closedColumn.locator(".syn-board-column__count")).toHaveText(String(notLoaded));
  await expect(closedColumn.locator(".syn-board-card")).toHaveCount(0);
  await expect(closedColumn.locator(".syn-board-column__empty")).toHaveCount(0);
  await expect(board).not.toContainText(doneId);
  await expect(board).toContainText(payload);
  await expect(page.locator("#studio-pwn, img[onerror], [onclick]")).toHaveCount(0);
  expect(await page.evaluate(() => (window as typeof window & { __studioPwned?: number }).__studioPwned))
    .toBeUndefined();
  await expect(page.getByText("Read-only projection; actions remain hub-enforced.")).toBeVisible();
  await expect(page.locator(".cc-shell button, .cc-shell input, .cc-shell form")).toHaveCount(0);
  await expect(page.locator(".cc-sweep")).toHaveCount(0);
  expect(await board.evaluate((element) => getComputedStyle(element).gridAutoFlow)).toBe("column");

  await page.setViewportSize({ width: 390, height: 844 });
  expect(await board.evaluate((element) => getComputedStyle(element).gridAutoFlow)).toBe("row");
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1),
  ).toBe(true);
  expect(pageErrors).toEqual([]);
});

function scaleTasks(count: number, prefix: string, column: string, boardStatus: string) {
  return Array.from({ length: count }, (_unused, index) => ({
    task_id: `${prefix}-${String(index + 1).padStart(4, "0")}`,
    title: `${prefix} task ${index + 1} with a title long enough to wrap in a column`,
    column,
    board_status: boardStatus,
    claim_status: "",
    declared: true,
    ready: column === "open",
    owner: `scale/seat-${(index % 40) + 1}`,
  }));
}

test("the command centre keeps its instruments in the first screen and bounds a thousand-task plan", async ({
  page,
}) => {
  // The page, its scripts and its styles are the shipped ones. Only the size of the
  // plan is supplied at the feed boundary, because a thousand tasks cannot be
  // declared through the rate-limited operator route within one journey.
  const open = scaleTasks(150, "SCALE-OPEN", "open", "open");
  const working = scaleTasks(40, "SCALE-WORK", "working", "in_progress");
  const blocked = scaleTasks(30, "SCALE-BLOCK", "blocked", "blocked");
  const closed = scaleTasks(780, "SCALE-DONE", "closed", "done");
  const snapshot = {
    verdict: "amber",
    hub: { id: "scale-fixture", version: "fixture" },
    headline: {
      agents_live: 40,
      claims_active: 3,
      claims_stale: 0,
      tasks_ready: open.length,
      tasks_blocked: blocked.length,
      branch_conflicts: 0,
      risk_signals: blocked.length,
    },
    agents: {
      live: Array.from({ length: 40 }, (_unused, index) => `scale/seat-${index + 1}`),
      missing_waiters: [],
    },
    claims: {
      active: [1, 2, 3].map((index) => ({ owner: `scale/seat-${index}`, paths: [`src/part-${index}`] })),
      stale: [],
    },
    tasks: {
      ready: open.map((task) => task.task_id),
      blocked: blocked.map((task) => ({ task_id: task.task_id })),
      columns: {
        columns: [
          { id: "open", label: "Open", tasks: open },
          { id: "claimed", label: "Claimed", tasks: [] },
          { id: "working", label: "Working", tasks: working },
          { id: "input_required", label: "Input required", tasks: [] },
          { id: "blocked", label: "Blocked", tasks: blocked },
          { id: "closed", label: "Closed", tasks: closed },
          { id: "other", label: "Other", tasks: [] },
        ],
      },
    },
    conflicts: [],
    risk: {
      signals: blocked.map((task) => ({ level: "amber", subject: task.task_id, detail: "blocked on unmet dependencies" })),
    },
    security_posture: { level: "green", rows: [{ level: "green", surface: "receipts", state: "evidenced" }] },
    observed_fleet: {},
  };
  let served = 0;
  await page.route("**/studio.json", async (route) => {
    served += 1;
    await route.fulfill({ json: snapshot });
  });

  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  await page.setExtraHTTPHeaders(headers);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/studio/command");

  const board = page.locator("#cc-board-columns");
  await expect(board.locator(".syn-board-column")).toHaveCount(7);

  const clock = await page.locator("#cc-clock").boundingBox();
  expect(clock).not.toBeNull();
  expect(clock!.y + clock!.height).toBeLessThanOrEqual(900);
  const boardTop = (await board.boundingBox())!.y;
  expect(boardTop).toBeGreaterThan(clock!.y + clock!.height);
  const grid = (await page.locator(".cc-grid").boundingBox())!;
  const boardPanel = (await page.locator(".cc-board-panel").boundingBox())!;
  expect(boardPanel.y - (grid.y + grid.height)).toBeGreaterThanOrEqual(16);

  const pageHeight = await page.evaluate(() => document.documentElement.scrollHeight);
  // Four screens. With this thousand-task plan the page measured 3,119 pixels
  // when the budget was set; unbounded lists would make it tens of screens long.
  expect(pageHeight).toBeLessThanOrEqual(900 * 4);

  const openColumn = board.locator('.syn-board-column[data-column="open"]');
  await expect(openColumn.locator(".syn-board-column__count")).toHaveText("150");
  await expect(openColumn.locator(".syn-board-column__body > .syn-board-card")).toHaveCount(6);
  await expect(openColumn.locator("details.syn-board-more > summary")).toHaveText("144 more of 150");
  const closedColumn = board.locator('.syn-board-column[data-column="closed"]');
  await expect(closedColumn.locator(".syn-board-column__count")).toHaveText("780");
  await expect(closedColumn.locator("details.syn-board-more > summary")).toHaveText("780 closed");
  await expect(closedColumn.locator(".syn-board-card").first()).toBeHidden();

  const tasksPanel = page.locator("#cc-tasks-list");
  await expect(tasksPanel.locator(":scope > .syn-row")).toHaveCount(8);
  const more = tasksPanel.locator("details.cc-more > summary");
  await expect(more).toHaveText("172 more of 180");
  await more.focus();
  await page.keyboard.press("Enter");
  await expect(tasksPanel.locator("details.cc-more")).toHaveAttribute("open", "");
  const servedBefore = served;
  await expect.poll(() => served, { timeout: 12_000 }).toBeGreaterThan(servedBefore);
  await expect(tasksPanel.locator("details.cc-more")).toHaveAttribute("open", "");
  await expect(tasksPanel.locator(".syn-row").last()).toBeVisible();

  await expect(page.locator(".cc-shell button, .cc-shell input, .cc-shell form")).toHaveCount(0);

  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1),
  ).toBe(true);
  expect(pageErrors).toEqual([]);
});
