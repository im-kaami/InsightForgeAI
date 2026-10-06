import { expect, test } from "./fixtures";

const ordersCsv = `order_id,region,amount,status
1,West,100,completed
2,West,50,refunded
3,East,30,completed
4,East,20,completed
`;

test("run read-only SQL in the editor and see refusals as errors", async ({ page }) => {
  await page.goto("/register");
  await page.locator("#email").fill(`sql-editor-${Date.now()}@example.com`);
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

  await page.getByRole("link", { name: "SQL editor" }).click();
  await expect(page).toHaveURL(/\/datasets\/[a-f0-9]+\/sql$/);
  const editor = page.getByLabel("SQL query");
  await expect(editor).toHaveValue('SELECT * FROM "orders" LIMIT 100');

  await editor.fill("SELECT region, SUM(amount) AS revenue FROM ");
  await page.getByTestId("sql-tables").getByRole("button", { name: "orders" }).click();
  await expect(editor).toHaveValue(/FROM "orders"$/);
  await editor.pressSequentially(" GROUP BY region ORDER BY region");
  await page.getByRole("button", { name: "Run", exact: true }).click();
  await expect(page.getByTestId("sql-summary")).toContainText("Showing 2 of 2 rows");
  await expect(page.getByTestId("sql-summary")).toContainText("ms");
  const result = page.getByTestId("sql-result");
  await expect(result.getByRole("cell", { name: "West" })).toBeVisible();
  await expect(result.getByRole("cell", { name: "150" })).toBeVisible();

  await editor.fill("DELETE FROM orders");
  await editor.press("Control+Enter");
  await expect(page.getByRole("alert").filter({ hasText: "Only SELECT" })).toBeVisible();
  await expect(page.getByTestId("sql-result")).toHaveCount(0);
});
