import { expect, test } from "./fixtures";

// Runs against the isolated fake API. A checked metric report never uses an AI model, so the
// numbers, checks and summary here are the real calculation.
const ordersCsv = `order_id,order_date,region,amount,status
1,2025-01-10,West,40,completed
2,2025-02-10,East,20,completed
3,2025-04-05,West,100,completed
4,2025-05-06,East,30,completed
5,2025-05-07,East,500,refunded
`;

test("a checked report compares an approved metric with the previous period", async ({ page }) => {
  await page.goto("/register");
  await page.locator("#email").fill(`metric-report-${Date.now()}@example.com`);
  await page.locator("#password").fill("password123");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/datasets$/, { timeout: 15_000 });
  await page.getByRole("button", { name: "Add data" }).click();
  await page
    .locator('input[type="file"]')
    .setInputFiles([{ name: "orders.csv", mimeType: "text/csv", buffer: Buffer.from(ordersCsv) }]);
  await page.getByRole("button", { name: "Preview import" }).click();
  await page.getByLabel("I reviewed the import preview").check();
  await page.getByRole("button", { name: "Confirm import" }).click();
  const row = page.getByRole("row").filter({ has: page.getByRole("button", { name: "Ask" }) });
  await expect(row).toBeVisible({ timeout: 15_000 });
  await row.getByRole("button").first().click();
  await expect(page).toHaveURL(/\/datasets\/[a-f0-9]+$/, { timeout: 15_000 });

  const card = page.getByTestId("dataset-metrics");
  await card.getByText("Add a metric").click();
  await card.locator("#metric-name").fill("revenue");
  await card.locator("#metric-label").fill("Revenue");
  await card.locator("#metric-column").selectOption("amount");
  await card.locator("#metric-date").selectOption("order_date");
  await card.getByRole("checkbox", { name: "region", exact: true }).check();
  await card.getByLabel("Filter column").selectOption("status");
  await card.getByLabel("Filter value").fill("completed");
  await card.getByRole("button", { name: "Add condition" }).click();
  await card.getByLabel("I checked this definition; the AI may use it").check();
  await card.getByRole("button", { name: "Add to list" }).click();
  await card.getByRole("button", { name: "Save metrics" }).click();
  await expect(page.getByText("Metrics saved")).toBeVisible();

  await card.getByRole("button", { name: "Checked report" }).click();
  const form = card.getByTestId("metric-report-form");
  await form.getByLabel("First day").fill("2025-04-01");
  await form.getByLabel("Last day").fill("2025-06-30");
  await form.getByLabel("Split by").selectOption("region");
  await form.getByRole("button", { name: "Run checked report" }).click();
  await expect(page).toHaveURL(/\/sessions\/[a-f0-9]+$/, { timeout: 15_000 });

  const runCard = page.getByTestId("run-card");
  await expect(runCard).toContainText("Checked metric report", { timeout: 60_000 });
  await expect(runCard).toContainText("Calculation checks passed", { timeout: 60_000 });
  await expect(runCard).toContainText("Revenue: checked report");
  const table = page
    .locator("section[id^='artifact-']")
    .filter({ has: page.getByTestId("metric-result") });
  // Current period 130 (100 + 30, the refund excluded) against 60 in January to March.
  await expect(table.getByRole("cell", { name: "130" }).first()).toBeVisible();
  await expect(table.getByRole("cell", { name: "60" }).first()).toBeVisible();
  await expect(table.getByRole("cell", { name: "116.67" })).toBeVisible();
});
