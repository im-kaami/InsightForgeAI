import { expect, test } from "./fixtures";

// Runs against the isolated fake API. Metric and question tiles are calculated by code, so the
// numbers below are the real results.
const ordersCsv = `order_id,order_date,region,amount,status
1,2025-03-05,West,40,completed
2,2025-03-20,East,20,completed
3,2025-04-10,West,100,completed
4,2025-04-25,East,30,completed
5,2025-04-28,East,500,refunded
`;

test("a dashboard holds live metric and question tiles and pinned results", async ({ page }) => {
  await page.goto("/register");
  await page.locator("#email").fill(`dashboards-${Date.now()}@example.com`);
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

  const metrics = page.getByTestId("dataset-metrics");
  await metrics.getByText("Add a metric").click();
  await metrics.locator("#metric-name").fill("revenue");
  await metrics.locator("#metric-label").fill("Revenue");
  await metrics.locator("#metric-column").selectOption("amount");
  await metrics.locator("#metric-date").selectOption("order_date");
  await metrics.getByRole("checkbox", { name: "region", exact: true }).check();
  await metrics.getByLabel("Filter column").selectOption("status");
  await metrics.getByLabel("Filter value").fill("completed");
  await metrics.getByRole("button", { name: "Add condition" }).click();
  await metrics.getByLabel("I checked this definition; the AI may use it").check();
  await metrics.getByRole("button", { name: "Add to list" }).click();
  await metrics.getByRole("button", { name: "Save metrics" }).click();
  await expect(page.getByText("Metrics saved")).toBeVisible();

  const queries = page.getByTestId("dataset-queries");
  await queries.getByText("Add a question").click();
  await queries.locator("#query-question").fill("What is completed revenue by region?");
  await queries
    .locator("#query-sql")
    .fill(
      "SELECT region, SUM(amount) AS revenue FROM orders WHERE status = 'completed' GROUP BY region ORDER BY region",
    );
  await queries.getByRole("button", { name: "Add question" }).click();
  await queries.getByRole("button", { name: "Save approved questions" }).click();
  await expect(page.getByText("Approved questions saved")).toBeVisible();

  // Pin an answer from a session.
  await page.getByRole("button", { name: "Start analysis" }).click();
  await expect(page).toHaveURL(/\/sessions\/[a-f0-9]+$/, { timeout: 15_000 });
  const composer = page.getByPlaceholder("Ask a question about this dataset...");
  await composer.fill("What are revenue by region?");
  await composer.press("Enter");
  // Code matches this wording to the approved question, so the answer is that approved query.
  await expect(page.getByTestId("approved-query-result")).toBeVisible({ timeout: 60_000 });
  const table = page
    .locator("section[id^='artifact-']")
    .filter({ has: page.getByTestId("approved-query-result") });
  await table.getByRole("button", { name: "Pin to dashboard" }).click();
  page.once("dialog", (dialog) => dialog.accept("Weekly numbers"));
  await table.getByLabel("Dashboard to pin to").selectOption("__new__");
  await table.getByRole("button", { name: "Pin", exact: true }).click();
  await expect(page.getByText("Pinned to the dashboard")).toBeVisible();

  await page.getByRole("link", { name: "Dashboards" }).click();
  await page.getByTestId("dashboard-list").getByText("Weekly numbers").click();
  await expect(page).toHaveURL(/\/dashboards\/[a-f0-9]+$/);
  const tiles = page.getByTestId("dashboard-tile");
  await expect(tiles).toHaveCount(1);
  await expect(tiles.first()).toContainText("Pinned result");
  await expect(tiles.first()).toContainText("approved query");

  const add = page.getByTestId("add-tile");
  await add.getByLabel("Tile dataset").selectOption({ label: "orders" });
  await add.getByLabel("Tile content").selectOption({ label: "Metric report: Revenue" });
  await add.getByLabel("Tile days").fill("30");
  await add.getByRole("button", { name: "Add tile" }).click();
  await expect(tiles).toHaveCount(2, { timeout: 60_000 });
  // 30 days to 2025-04-25: 130 against 60.
  await expect(tiles.nth(1)).toContainText("Revenue, last 30 days");
  await expect(tiles.nth(1).getByRole("cell", { name: "130" })).toBeVisible();
  await expect(tiles.nth(1).getByRole("cell", { name: "116.67" })).toBeVisible();

  await add.getByLabel("Tile content").selectOption({
    label: "Question: What is completed revenue by region?",
  });
  await add.getByRole("button", { name: "Add tile" }).click();
  await expect(tiles).toHaveCount(3, { timeout: 60_000 });
  await expect(tiles.nth(2)).toContainText("approved query");
  await expect(tiles.nth(2).getByRole("cell", { name: "140" })).toBeVisible();

  await page.getByRole("button", { name: "Refresh all" }).click();
  await expect(page.getByText("Dashboard refreshed")).toBeVisible({ timeout: 60_000 });
  await page.getByRole("button", { name: "Move What is completed revenue by region? up" }).click();
  await expect(tiles.nth(1)).toContainText("What is completed revenue by region?");
});
