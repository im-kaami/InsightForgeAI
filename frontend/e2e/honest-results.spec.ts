import type { Route } from "@playwright/test";
import { expect, hrCsv, test } from "./fixtures";

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
    provenance: {
      privacy_mode: "local",
      model: "local: qwen3:4b",
      assumptions: ["orders: reads orders; uses every row (no filter)."],
      number_check: { checked: 2, matched: 1, unmatched: ["4,500,000"] },
      evidence: [
        {
          id: "N1",
          text: "20",
          value: 20,
          artifact: "orders",
          row: 2,
          column: "amount",
          kind: "cell",
        },
      ],
      checks: [
        {
          code: "summary_numbers",
          passed: false,
          message: "1 of 2 numbers in the summary were found in the results",
        },
      ],
    },
    verification_status: "needs_review",
    warnings: [],
    fallback_reason: null,
    error: null,
    created_at: now,
    finished_at: now,
  };
}

test("charts switch type in the browser and the health check lists findings", async ({ page }) => {
  await page.goto("/register");
  await page.locator("#email").fill(`switch-${Date.now()}@example.com`);
  await page.locator("#password").fill("password123");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/datasets$/, { timeout: 15_000 });
  await page.getByRole("button", { name: "Add data" }).click();
  await page.locator('input[type="file"]').setInputFiles(await hrCsv());
  await page.getByRole("button", { name: "Preview import" }).click();
  await expect(page.getByTestId("quality-checks")).toBeVisible();
  await expect(page.getByText("Imported rows: 60")).toBeVisible();
  await page.getByLabel("I reviewed the import preview").check();
  await page.getByRole("button", { name: "Confirm import" }).click();
  const datasetRow = page.getByRole("row").filter({
    has: page.getByRole("button", { name: "Ask" }),
  });
  await expect(datasetRow).toBeVisible({ timeout: 15_000 });
  await datasetRow.getByRole("button", { name: "Ask" }).click();
  await expect(page).toHaveURL(/\/sessions\/[a-f0-9]+$/, { timeout: 15_000 });
  const composer = page.getByPlaceholder("Ask a question about this dataset...");
  await composer.fill("Profile departments");
  await composer.press("Enter");
  await expect(page.getByText("completed", { exact: true })).toBeVisible({ timeout: 60_000 });

  const chartType = page.getByLabel("Chart type").first();
  await expect(chartType).toHaveValue("bar");
  const plot = page.locator(".js-plotly-plot").first();
  await expect(plot.locator(".barlayer .trace")).toHaveCount(1, { timeout: 60_000 });
  await chartType.selectOption("line");
  await expect(plot.locator(".scatterlayer .trace")).toHaveCount(1);
  await expect(plot.locator(".barlayer .trace")).toHaveCount(0);

  await page.getByRole("link", { name: "Change data sharing" }).click();
  await expect(page).toHaveURL(/\/datasets\/[a-f0-9]+$/, { timeout: 15_000 });
  await expect(page.getByTestId("quality-checks")).toBeVisible();
});

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
  await page.locator('input[type="file"]').setInputFiles(await hrCsv());
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
  await expect(page.getByTestId("number-check")).toContainText(
    "1 of 2 numbers in the summary were found in the results; not found: 4,500,000.",
  );
  await expect(page.getByTestId("assumptions")).toContainText(
    "orders: reads orders; uses every row (no filter).",
  );
  await page.getByText("Evidence and definitions", { exact: true }).click();
  await expect(page.getByTestId("evidence-N1")).toContainText(
    "\u201c20\u201d = 20orders, row 2, column amount",
  );
  await expect(page.getByText("Local only: qwen3:4b on this computer")).toBeVisible();
  await expect(page.getByText(/Local only: chat uses qwen3:4b on this computer/)).toBeVisible();
});
