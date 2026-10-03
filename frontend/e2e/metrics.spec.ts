import { expect, test } from "./fixtures";

// Runs against the isolated fake API: with no AI model, an approved metric named in the
// question is still answered by the code-only metric path, so the whole flow is real.
const ordersCsv = `order_id,region,amount,status
1,West,100,completed
2,West,50,refunded
3,East,30,completed
4,East,20,completed
5,North,70,cancelled
`;

test("an approved metric is previewed and answers a question with code-written SQL", async ({
  page,
}) => {
  await page.goto("/register");
  await page.locator("#email").fill(`metrics-${Date.now()}@example.com`);
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
  await expect(card).toContainText("No metrics yet.");
  await card.getByText("Add a metric").click();
  await card.locator("#metric-name").fill("revenue");
  await card.locator("#metric-label").fill("Revenue");
  await card.locator("#metric-column").selectOption("amount");
  await card.locator("#metric-synonyms").fill("sales");
  await card.getByRole("checkbox", { name: "region", exact: true }).check();
  await card.getByLabel("Filter column").selectOption("status");
  await card.getByLabel("Filter value").fill("completed");
  await card.getByRole("button", { name: "Add condition" }).click();
  await expect(card).toContainText("status equals completed");
  await card.getByLabel("I checked this definition; the AI may use it").check();
  await card.getByRole("button", { name: "Add to list" }).click();
  await card.getByRole("button", { name: "Save metrics" }).click();
  await expect(page.getByText("Metrics saved")).toBeVisible();

  const list = card.getByTestId("metric-list");
  await expect(list).toContainText("Approved");
  await list.getByRole("button", { name: "Preview" }).click();
  const preview = card.getByTestId("metric-preview");
  await expect(preview).toContainText(
    "Revenue = sum amount of orders where status equals completed",
  );
  await expect(preview.getByRole("cell", { name: "150" })).toBeVisible();

  await page.getByRole("button", { name: "Start analysis" }).click();
  await expect(page).toHaveURL(/\/sessions\/[a-f0-9]+$/, { timeout: 15_000 });
  const composer = page.getByPlaceholder("Ask a question about this dataset...");
  await composer.fill("What are sales by region?");
  await composer.press("Enter");
  const result = page.getByTestId("metric-result");
  await expect(result).toContainText("Approved metric: Revenue", { timeout: 60_000 });
  await expect(result).toContainText("by region");
  const table = page.locator("section[id^='artifact-']").filter({ has: result });
  await expect(table.getByRole("cell", { name: "West" })).toBeVisible();
  await expect(table.getByRole("cell", { name: "100" })).toBeVisible();
  await expect(table.getByRole("cell", { name: "North" })).toHaveCount(0);
});
