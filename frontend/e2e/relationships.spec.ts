import { expect, test } from "./fixtures";

const customersCsv = `customer_id,segment
1,retail
2,business
3,retail
`;

const ordersCsv = `order_id,customer_id,amount
10,1,25
11,1,40
12,2,15
13,3,60
14,2,35
`;

test("join suggestions come from the data and approved relationships are saved", async ({
  page,
}) => {
  await page.goto("/register");
  await page.locator("#email").fill(`joins-${Date.now()}@example.com`);
  await page.locator("#password").fill("password123");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/datasets$/, { timeout: 15_000 });
  await page.getByRole("button", { name: "Add data" }).click();
  await page.locator('input[type="file"]').setInputFiles([
    { name: "customers.csv", mimeType: "text/csv", buffer: Buffer.from(customersCsv) },
    { name: "orders.csv", mimeType: "text/csv", buffer: Buffer.from(ordersCsv) },
  ]);
  await page.getByRole("button", { name: "Preview import" }).click();
  await page.getByLabel("I reviewed the import preview").check();
  await page.getByRole("button", { name: "Confirm import" }).click();
  const row = page.getByRole("row").filter({ has: page.getByRole("button", { name: "Ask" }) });
  await expect(row).toBeVisible({ timeout: 15_000 });
  await row.getByRole("button").first().click();
  await expect(page).toHaveURL(/\/datasets\/[a-f0-9]+$/, { timeout: 15_000 });

  const card = page.getByTestId("table-relationships");
  await expect(card).toContainText("No relationships yet.");
  await card.getByRole("button", { name: "Suggest joins" }).click();
  const suggestion = card.getByTestId("join-suggestions");
  await expect(suggestion).toContainText("orders.customer_id → customers.customer_id", {
    timeout: 15_000,
  });
  await expect(suggestion).toContainText("100% match");
  await expect(suggestion).toContainText("5 of 5 orders rows");
  await suggestion.getByRole("button", { name: "Add relationship" }).click();
  await expect(suggestion).toContainText("No further suggestions.");
  await card.getByRole("button", { name: "Save relationships" }).click();
  await expect(page.getByText("Relationships saved")).toBeVisible();

  await page.reload();
  const reloaded = page.getByTestId("table-relationships");
  await expect(reloaded.getByTestId("relationship-list")).toContainText(
    "orders.customer_id → customers.customer_id (many orders rows to one customers row)",
  );
});
