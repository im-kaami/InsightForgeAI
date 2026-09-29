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
      mode: "deep",
      rounds: 2,
      reviews: [
        {
          round: 1,
          verdict: "revise",
          reason: "The filter used the wrong capitalisation",
          steps: ["orders"],
        },
        { round: 2, verdict: "answer", reason: "The count answers the question", steps: [] },
      ],
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
      trace: [
        {
          step: "plan",
          kind: "model",
          started_ms: 0,
          duration_ms: 4200,
          ok: true,
          details: {
            model: "qwen3:4b",
            shared: "everything, to a model on this computer",
            prompt_tokens: 812,
          },
        },
        {
          step: "orders",
          kind: "sql",
          started_ms: 4300,
          duration_ms: 35,
          ok: true,
          details: { rows: 10000, truncated: true, full_row_count: 2341556 },
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

test("dataset notes for the AI are saved and reloaded", async ({ page }) => {
  await page.goto("/register");
  await page.locator("#email").fill(`notes-${Date.now()}@example.com`);
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
  await page.getByRole("link", { name: "Change data sharing" }).click();
  await expect(page).toHaveURL(/\/datasets\/[a-f0-9]+$/, { timeout: 15_000 });

  const notes = page.getByTestId("dataset-notes");
  await notes.getByLabel("General notes and business rules").fill("Salary is annual base pay.");
  await notes.getByLabel("Meaning of hr.salary").fill("Annual base pay");
  await notes.getByLabel("Unit of hr.salary").fill("USD");
  await notes.getByLabel("Other names for hr.salary").fill("pay, compensation");
  await notes.getByRole("button", { name: "Save notes" }).click();
  await expect(page.getByText("Notes saved")).toBeVisible();

  await page.reload();
  const reloaded = page.getByTestId("dataset-notes");
  await expect(reloaded.getByLabel("General notes and business rules")).toHaveValue(
    "Salary is annual base pay.",
  );
  await expect(reloaded.getByLabel("Unit of hr.salary")).toHaveValue("USD");
  await expect(reloaded.getByLabel("Other names for hr.salary")).toHaveValue("pay, compensation");
});

test("a clarifying question offers choices and sends the answer as a new run", async ({ page }) => {
  await page.goto("/register");
  await page.locator("#email").fill(`clarify-${Date.now()}@example.com`);
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
    const now = new Date().toISOString();
    const asked = {
      ...cutOffRun(session.id),
      goal: "Show me the top performers.",
      summary: "Top by average salary or by performance score?",
      artifacts: [],
      warnings: [],
      verification_status: "exploratory",
      provenance: {
        privacy_mode: "local",
        clarification: {
          question: "Top by average salary or by performance score?",
          options: ["Average salary", "Performance score"],
        },
      },
      created_at: now,
    };
    await route.fulfill({ response, json: { ...session, runs: [asked] } });
  });
  await page.reload();

  const prompt = page.getByTestId("clarification");
  await expect(prompt).toContainText(
    "Before analyzing, the AI needs to know: Top by average salary or by performance score?",
  );
  const sent = page.waitForRequest(
    (request) =>
      request.method() === "POST" && /\/api\/sessions\/[a-f0-9]+\/runs$/.test(request.url()),
  );
  await prompt.getByRole("button", { name: "Performance score" }).click();
  const body = (await sent).postDataJSON();
  expect(body.clarified).toBe(true);
  expect(body.goal).toBe(
    "Show me the top performers.\n\nClarification: Top by average salary or by performance score? Performance score",
  );
});

test("statistical test results show the method, verdict, effect size and cautions", async ({
  page,
}) => {
  await page.goto("/register");
  await page.locator("#email").fill(`stats-${Date.now()}@example.com`);
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
    const tested = {
      ...cutOffRun(session.id),
      goal: "Is the salary difference between Remote and New York significant?",
      summary: "Salaries differ.",
      warnings: [],
      verification_status: "exploratory",
      provenance: { privacy_mode: "local" },
      artifacts: [
        {
          type: "stat",
          name: "significance_test",
          trust: "tested method",
          method: "compare_groups",
          test: "Mann-Whitney U test",
          data_source: "rows",
          x: "location",
          y: "salary",
          n: 26,
          statistic: 128.5,
          p_value: 0.0159,
          p_adjusted: null,
          effect_size: { name: "rank_biserial", value: 0.562, magnitude: "large" },
          interval: null,
          groups: [
            { location: "New York", n: 13, mean: 94923, median: 85000, sd: 21000 },
            { location: "Remote", n: 13, mean: 77923, median: 72000, sd: 9000 },
          ],
          pairwise: [],
          robustness: [
            { analysis: "Welch's t-test on means", p_value: 0.03, holds: true },
            { analysis: "without the top and bottom 1% of values", p_value: 0.08, holds: false },
          ],
          checks: ["Normality: not met for Remote, so a rank-based test compares typical values."],
          cautions: ["Small groups (New York, Remote have fewer than 10 values)."],
          interpretation: "Median salary is 85,000 for New York and 72,000 for Remote.",
          note: null,
        },
      ],
    };
    await route.fulfill({ response, json: { ...session, runs: [tested] } });
  });
  await page.reload();

  const card = page.getByTestId("stat-result");
  await expect(card).toContainText("Tested method: Mann-Whitney U test");
  await expect(card).toContainText("Unlikely to be chance");
  await expect(card).toContainText("p = 0.016");
  await expect(card).toContainText("0.562 · large");
  await expect(card).toContainText("Median salary is 85,000 for New York and 72,000 for Remote.");
  await expect(card.getByRole("row")).toHaveCount(3);
  await expect(card).toContainText("Small groups (New York, Remote have fewer than 10 values).");
  await expect(card.getByTestId("robustness")).toContainText(
    "Holds: Welch's t-test on means (p = 0.030)",
  );
  await expect(card.getByTestId("robustness")).toContainText(
    "Changes: without the top and bottom 1% of values (p = 0.080)",
  );

  await page.unroute(/\/api\/sessions\/[a-f0-9]+$/);
  await page.route(/\/api\/sessions\/[a-f0-9]+$/, async (route) => {
    const response = await route.fetch(upstream(route));
    const session = await response.json();
    const change = {
      ...cutOffRun(session.id),
      goal: "Why did total amount drop from May to June?",
      summary: "Amount fell.",
      warnings: [],
      verification_status: "exploratory",
      provenance: { privacy_mode: "local" },
      artifacts: [
        {
          type: "stat",
          name: "change_breakdown",
          trust: "tested method",
          method: "explain_change",
          test: "Change breakdown (mix and rate)",
          data_source: "rows",
          x: "period",
          y: "amount",
          by: ["region"],
          n: 69,
          statistic: -2083.14,
          p_value: null,
          groups: [
            {
              dimension: "region",
              segment: "West",
              before: 1223.95,
              after: 455.43,
              change: -768.52,
            },
          ],
          checks: ["43 rows in before and 26 rows in after."],
          cautions: ["This shows where the change happened, not why it happened."],
          interpretation: "Total amount fell from 4,418 (before) to 2,335 (after).",
        },
      ],
    };
    await route.fulfill({ response, json: { ...session, runs: [change] } });
  });
  await page.reload();
  const breakdown = page.getByTestId("stat-result");
  await expect(breakdown).toContainText("Tested method: Change breakdown (mix and rate)");
  await expect(breakdown).toContainText("Total change");
  await expect(breakdown).toContainText("-2,083");
  await expect(breakdown).not.toContainText("chance");
  await expect(breakdown).toContainText(
    "This shows where the change happened, not why it happened.",
  );

  await page.unroute(/\/api\/sessions\/[a-f0-9]+$/);
  await page.route(/\/api\/sessions\/[a-f0-9]+$/, async (route) => {
    const response = await route.fetch(upstream(route));
    const session = await response.json();
    const coded = {
      ...cutOffRun(session.id),
      goal: "Using Python, compute the median salary per department",
      summary: "Engineering is highest.",
      warnings: [],
      verification_status: "exploratory",
      provenance: { privacy_mode: "local" },
      artifacts: [
        {
          type: "code",
          name: "python_analysis",
          trust: "free-form code",
          code: "result = df.groupby('department')['salary'].median()",
          data_source: "rows",
          ok: true,
          stdout: "",
          error: null,
          columns: ["department", "salary"],
          rows: [{ department: "Engineering", salary: 107000 }],
          total_rows: 1,
          value: null,
          limits: { network: "none", memory: "512m", timeout_seconds: "30" },
        },
      ],
    };
    await route.fulfill({ response, json: { ...session, runs: [coded] } });
  });
  await page.reload();
  const code = page.getByTestId("code-result");
  await expect(code).toContainText("Free-form code (sandboxed)");
  await expect(code).toContainText("network none, 512m memory, 30 s limit");
  await expect(code).toContainText("result = df.groupby('department')['salary'].median()");
  await expect(code.getByRole("row")).toHaveCount(2);
});

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
  await page.getByLabel("Analysis mode").selectOption("deep");
  await composer.fill("Profile departments");
  await composer.press("Enter");
  await expect(page.getByText("completed", { exact: true })).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText("Deep · 1 round", { exact: true })).toBeVisible();
  await expect(
    page.getByText("Deep mode needs an AI model, so the analysis ran once without a review."),
  ).toBeVisible();

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
  await expect(page.getByText("Deep · 2 rounds", { exact: true })).toBeVisible();
  await expect(page.getByTestId("deep-reviews")).toContainText(
    "Revised orders: The filter used the wrong capitalisation",
  );
  await expect(page.getByTestId("deep-reviews")).toContainText(
    "Accepted: The count answers the question",
  );
  await expect(page.getByTestId("assumptions")).toContainText(
    "orders: reads orders; uses every row (no filter).",
  );
  await page.getByText("Run trace", { exact: true }).click();
  await expect(page.getByTestId("run-trace")).toContainText(
    "model: qwen3:4b · shared: everything, to a model on this computer · prompt tokens: 812",
  );
  await expect(page.getByTestId("run-trace")).toContainText(
    "rows: 10000 · truncated: true · full row count: 2341556",
  );
  await page.getByText("Evidence and definitions", { exact: true }).click();
  await expect(page.getByTestId("evidence-N1")).toContainText(
    "\u201c20\u201d = 20orders, row 2, column amount",
  );
  await expect(page.getByText("Local only: qwen3:4b on this computer")).toBeVisible();
  await expect(page.getByText(/Local only: chat uses qwen3:4b on this computer/)).toBeVisible();
});
