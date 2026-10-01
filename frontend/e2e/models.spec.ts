import type { Route } from "@playwright/test";
import { expect, hrCsv, test } from "./fixtures";

// The isolated test backend uses the offline planner, which never trains a prediction
// model, so these tests stub the model endpoints. Saving, scoring, drift and deletion
// against the real API are covered by backend/tests/api/test_saved_models.py.

const target = process.env.E2E_API_TARGET;

function upstream(route: Route) {
  if (!target) return undefined;
  const requested = new URL(route.request().url());
  return { url: new URL(requested.pathname + requested.search, target).toString() };
}

const MODEL_ID = "b".repeat(32);

function savedModel(datasetId: string, versionId: string) {
  return {
    id: MODEL_ID,
    dataset_id: datasetId,
    dataset_version_id: versionId,
    run_id: "c".repeat(32),
    name: "salary model",
    task: "regression",
    target: "salary",
    features: ["department", "years"],
    date_column: null,
    model_type: "random forest",
    metrics: { holdout_score: 8123, baseline_score: 15000, mae: 8123, r_squared: 0.61 },
    created_at: new Date().toISOString(),
    warning: null,
  };
}

function score(versionId: string) {
  return {
    model_id: MODEL_ID,
    version_id: versionId,
    rows_scored: 2,
    preview_columns: ["department", "years", "prediction"],
    preview_rows: [
      { department: "Engineering", years: 4, prediction: 101000 },
      { department: "Sales", years: 2, prediction: 64000 },
    ],
    drift: {
      features: [
        {
          feature: "years",
          kind: "numeric",
          psi: 0.41,
          band: "major shift",
          unseen_share: 0,
          missing_change: 0,
        },
        {
          feature: "department",
          kind: "categorical",
          psi: 0.02,
          band: "stable",
          unseen_share: 0.05,
          missing_change: 0,
        },
      ],
      rows: 2,
      unseen_row_share: 0.05,
      max_psi: 0.41,
    },
    new_data_metrics: { mae: 12000, r_squared: 0.4 },
    holdout_metrics: { holdout_score: 8123, baseline_score: 15000, mae: 8123, r_squared: 0.61 },
    recommendation: {
      verdict: "retrain recommended",
      detail: "The new data differs from the training data enough that retraining is advised.",
      reasons: ["years shifted a lot (PSI 0.41)"],
    },
  };
}

async function openSession(page: import("@playwright/test").Page) {
  await page.goto("/register");
  await page.locator("#email").fill(`models-${Date.now()}@example.com`);
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
}

test("a prediction result can be saved, scored for drift and deleted", async ({ page }) => {
  await openSession(page);
  const now = new Date().toISOString();
  await page.route(/\/api\/sessions\/[a-f0-9]+$/, async (route) => {
    const response = await route.fetch(upstream(route));
    const session = await response.json();
    const run = {
      id: "c".repeat(32),
      session_id: session.id,
      goal: "What predicts salary?",
      status: "completed",
      summary: "Department matters most.",
      artifacts: [
        {
          type: "stat",
          name: "salary_model",
          trust: "tested method",
          method: "predict",
          test: "Prediction model (random forest)",
          n: 2,
          data_source: "rows",
          x: "",
          y: "salary",
          statistic: 8123,
          p_value: null,
          importance: [{ feature: "department", importance: 0.3, spread: 0.02 }],
          checks: [],
          cautions: [],
          interpretation: "The random forest model predicts salary on held-out rows.",
        },
      ],
      timings: {},
      token_usage: {},
      used_fallback_plan: false,
      dataset_version_id: null,
      definition_id: null,
      provenance: { privacy_mode: "local" },
      verification_status: "exploratory",
      warnings: [],
      fallback_reason: null,
      error: null,
      created_at: now,
      finished_at: now,
    };
    await route.fulfill({ response, json: { ...session, runs: [run] } });
  });
  let savedName = "";
  await page.route(/\/api\/runs\/c+\/artifacts\/0\/model$/, async (route) => {
    savedName = (route.request().postDataJSON() as { name: string }).name;
    await route.fulfill({ status: 201, json: savedModel("d".repeat(32), "e".repeat(32)) });
  });
  await page.reload();

  const save = page.getByTestId("save-model");
  await expect(save.getByLabel("Model name")).toHaveValue("salary model");
  await save.getByLabel("Model name").fill("pay model");
  await save.getByRole("button", { name: "Save model" }).click();
  await expect(page.getByTestId("model-saved")).toContainText("Saved as “pay model”");
  expect(savedName).toBe("pay model");

  await page.unroute(/\/api\/sessions\/[a-f0-9]+$/);
  await page.getByRole("link", { name: "Change data sharing" }).click();
  await expect(page).toHaveURL(/\/datasets\/[a-f0-9]+$/, { timeout: 15_000 });
  const datasetId = page.url().split("/").pop()!;
  const versionId = await page.evaluate(async (id) => {
    const response = await fetch(`/api/datasets/${id}`, {
      headers: { Authorization: `Bearer ${localStorage.getItem("if_token")}` },
    });
    return ((await response.json()) as { current_version_id: string }).current_version_id;
  }, datasetId);
  expect(versionId).toBeTruthy();

  let deleted = false;
  await page.route(/\/api\/datasets\/[a-f0-9]+\/models$/, (route) =>
    route.fulfill({ json: deleted ? [] : [savedModel(datasetId, versionId)] }),
  );
  let scoredVersion: string | null = null;
  await page.route(/\/api\/models\/b+\/score$/, async (route) => {
    scoredVersion = (route.request().postDataJSON() as { version_id: string | null }).version_id;
    await route.fulfill({ json: score(versionId) });
  });
  await page.route(/\/api\/models\/b+$/, async (route) => {
    if (route.request().method() !== "DELETE") return route.fallback();
    deleted = true;
    await route.fulfill({ status: 204, body: "" });
  });
  await page.reload();

  const card = page.getByTestId("saved-models");
  const item = card.getByTestId("saved-model");
  await expect(item).toContainText("salary model");
  await expect(item).toContainText("Predicts salary from department, years");
  await item.getByRole("button", { name: "Score and check drift" }).click();

  const result = item.getByTestId("score-result");
  await expect(result).toContainText("Retraining recommended");
  await expect(result).toContainText("years shifted a lot (PSI 0.41)");
  await expect(result.getByTestId("drift-table")).toContainText("major shift");
  await expect(result.getByTestId("accuracy-comparison")).toContainText("Typical error (MAE)");
  await expect(result).toContainText("it does not prove the model is right or wrong");
  await expect(result.getByRole("cell", { name: "101000" })).toBeVisible();
  expect(scoredVersion).toBe(versionId);

  let explainedValues: Record<string, unknown> | null = null;
  await page.route(/\/api\/models\/b+\/explain$/, async (route) => {
    explainedValues = (route.request().postDataJSON() as { values: Record<string, unknown> })
      .values;
    await route.fulfill({
      json: {
        model_id: MODEL_ID,
        explained: "predicted value",
        reference: 80000,
        output: 64000,
        prediction: 64000,
        contributions: [
          { feature: "department", value: "Sales", contribution: -12000 },
          { feature: "years", value: 2, contribution: -4000 },
        ],
        additivity_gap: 0,
        algorithm: "exact Shapley values",
        background_rows: 50,
        interpretation:
          "The model's predicted value is 64,000 for this row, against 80,000 on average.",
        cautions: ["Contributions do not show that a feature causes the outcome."],
      },
    });
  });
  await result.getByLabel("Row in the preview (1 to 2)").fill("2");
  await result.getByRole("button", { name: "Explain this row" }).click();
  const explanation = result.getByTestId("explanation");
  await expect(explanation).toContainText("Tested method");
  await expect(explanation).toContainText("against 80,000 on average");
  await expect(explanation.getByTestId("contribution-table")).toContainText("-12,000");
  await expect(explanation).toContainText("do not show that a feature causes the outcome");
  expect(explainedValues).toEqual({ department: "Sales", years: 2, prediction: 64000 });

  await item.getByRole("button", { name: "Delete model" }).click();
  await item.getByRole("button", { name: "Confirm delete" }).click();
  await expect(page.getByText("Model deleted")).toBeVisible();
  await expect(card).toContainText("No saved models yet");
});

test("scheduled checks show alerts that can be dismissed and schedules can be saved", async ({
  page,
}) => {
  await openSession(page);
  await page.getByRole("link", { name: "Change data sharing" }).click();
  await expect(page).toHaveURL(/\/datasets\/[a-f0-9]+$/, { timeout: 15_000 });
  const datasetId = page.url().split("/").pop()!;
  const now = new Date().toISOString();
  let acknowledged = false;
  const alert = {
    id: "f".repeat(32),
    model_id: MODEL_ID,
    model_name: "salary model",
    dataset_id: datasetId,
    trigger: "scheduled",
    version_id: "e".repeat(32),
    status: "completed",
    rows_scored: 60,
    max_psi: 0.41,
    verdict: "retrain recommended",
    reasons: ["feature 'years' shows a major distribution shift (PSI 0.41)"],
    error: null,
    alert: true,
    acknowledged_at: null as string | null,
    created_at: now,
  };
  const schedule = {
    id: "a".repeat(32),
    model_id: MODEL_ID,
    cron: "0 6 * * 1",
    timezone: "UTC",
    enabled: true,
    last_run_at: now,
    next_run_at: now,
  };
  await page.route(/\/api\/datasets\/[a-f0-9]+\/models$/, (route) =>
    route.fulfill({
      json: [
        {
          ...savedModel(datasetId, "e".repeat(32)),
          schedule,
          open_alerts: acknowledged ? 0 : 1,
        },
      ],
    }),
  );
  await page.route(/\/api\/model-alerts$/, (route) =>
    route.fulfill({ json: acknowledged ? [] : [alert] }),
  );
  await page.route(/\/api\/models\/b+\/scorings$/, (route) =>
    route.fulfill({ json: [{ ...alert, acknowledged_at: acknowledged ? now : null }] }),
  );
  await page.route(/\/api\/models\/b+\/scorings\/f+\/acknowledge$/, async (route) => {
    acknowledged = true;
    await route.fulfill({ json: { ...alert, acknowledged_at: now } });
  });
  let savedSchedule: Record<string, unknown> | null = null;
  await page.route(/\/api\/models\/b+\/schedule$/, async (route) => {
    savedSchedule = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill({ json: { ...schedule, ...savedSchedule } });
  });
  await page.reload();

  await expect(page.getByTestId("model-alerts-nav")).toContainText(
    "salary model: retraining recommended",
  );
  const monitoring = page.getByTestId("model-monitoring");
  await expect(monitoring).toContainText("1 alert");
  const banner = monitoring.getByTestId("model-alert");
  await expect(banner).toContainText("Retraining recommended (60 rows, largest drift PSI 0.410)");
  await expect(banner).toContainText("major distribution shift");
  await banner.getByRole("button", { name: "Dismiss" }).click();
  await expect(page.getByText("Alert dismissed")).toBeVisible();
  await expect(monitoring.getByTestId("model-alert")).toHaveCount(0);
  await expect(page.getByTestId("model-alerts-nav")).toHaveCount(0);
  await monitoring.getByText("Monitoring").click();
  await expect(monitoring.getByTestId("scoring-history")).toContainText("scheduled");

  await monitoring.getByRole("button", { name: "Daily 06:00" }).click();
  await monitoring.getByRole("button", { name: "Save schedule" }).click();
  await expect(page.getByText("Schedule saved")).toBeVisible();
  expect(savedSchedule).toMatchObject({ cron: "0 6 * * *", enabled: true });
});
