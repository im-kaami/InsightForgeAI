import { resolve } from "node:path";
import type { Route } from "@playwright/test";
import { expect, test } from "./fixtures";

const target = process.env.E2E_API_TARGET;

function upstream(route: Route) {
  if (!target) return undefined;
  const requested = new URL(route.request().url());
  return { url: new URL(requested.pathname + requested.search, target).toString() };
}

function cutOffRun(sessionId: string) {
  const now = new Date().toISOString();
  return {
    id: "a".repeat(32),
    session_id: sessionId,
    goal: "Show every order",
    status: "completed",
    summary: "Orders summary",
    artifacts: [
      {
        type: "table",
        name: "orders",
        sql: "SELECT * FROM orders LIMIT 10000",
        columns: ["id", "amount"],
        rows: [
          { id: 1, amount: 10 },
          { id: 2, amount: 20 },
        ],
        total_rows: 10000,
        truncated: true,
        full_row_count: 2341556,
      },
      {
        type: "plot",
        name: "amounts",
        kind: "scatter",
        title: "Order amounts",
        figure: { data: [], layout: {} },
        note: "Showing a random sample of 10,000 of 2,341,556 points.",
      },
      { type: "text", name: "summary", text: "Orders summary" },
    ],
    timings: {},
    token_usage: {},
    used_fallback_plan: false,
    dataset_version_id: null,
    definition_id: null,
    provenance: { privacy_mode: "local", model: "local: qwen3:4b" },
    verification_status: "exploratory",
    warnings: [],
    fallback_reason: null,
    error: null,
    created_at: now,
    finished_at: now,
  };
}

test("cut-off results, chart notes and the local model are explained", async ({ page }) => {
  await page.route("**/api/health", async (route) => {
    const response = await route.fetch(upstream(route));
    await route.fulfill({
      response,
      json: { ...(await response.json()), local_model: "qwen3:4b" },
    });
  });
  await page.goto("/register");
  await page.locator("#email").fill(`honest-${Date.now()}@example.com`);
  await page.locator("#password").fill("password123");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/datasets$/, { timeout: 15_000 });
  await page.getByRole("button", { name: "Add data" }).click();
  await page
    .locator('input[type="file"]')
    .setInputFiles(resolve("../backend/tests/fixtures/hr.csv"));
  await page.getByRole("button", { name: "Preview import" }).click();
  await page.getByLabel("I reviewed the import preview").check();
  await page.getByRole("button", { name: "Confirm import" }).click();
  const datasetRow = page.getByRole("row").filter({
    has: page.getByRole("button", { name: "Ask" }),
  });
  await expect(datasetRow).toBeVisible({ timeout: 15_000 });
  await datasetRow.getByRole("button", { name: "Ask" }).click();
  await expect(page).toHaveURL(/\/sessions\/[a-f0-9]+$/, { timeout: 15_000 });

  await page.route(/\/api\/sessions\/[a-f0-9]+$/, async (route) => {
    const response = await route.fetch(upstream(route));
    const session = await response.json();
    await route.fulfill({ response, json: { ...session, runs: [cutOffRun(session.id)] } });
  });
  await page.reload();

  await expect(page.getByTestId("truncation-note")).toHaveText(
    "Result cut off: the query produced 2,341,556 rows; only the first 10,000 were kept.",
  );
  await expect(page.getByText("10,000 rows kept · showing 2")).toBeVisible();
  await expect(page.getByTestId("chart-note")).toHaveText(
    "Showing a random sample of 10,000 of 2,341,556 points.",
  );
  await expect(page.getByText("AI model: local: qwen3:4b")).toBeVisible();
  await expect(page.getByText("Local only: qwen3:4b on this computer")).toBeVisible();
  await expect(page.getByText(/Local only: chat uses qwen3:4b on this computer/)).toBeVisible();
});
