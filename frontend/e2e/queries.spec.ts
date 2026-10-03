import { expect, test } from "./fixtures";

// Runs against the isolated fake API: an approved question is matched by code (no AI model), and
// "Approved data only" refuses everything else, so the whole flow is real.
const ordersCsv = `order_id,region,amount,status
1,West,100,completed
2,West,50,refunded
3,East,30,completed
4,East,20,completed
`;

test("approved questions answer with saved SQL and approved-data-only refuses the rest", async ({
  page,
}) => {
  await page.goto("/register");
  await page.locator("#email").fill(`queries-${Date.now()}@example.com`);
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

  const card = page.getByTestId("dataset-queries");
  await expect(card).toContainText("No approved questions yet.");
  await card.getByText("Add a question").click();
  await card.locator("#query-question").fill("What is completed revenue by region?");
  await card
    .locator("#query-sql")
    .fill(
      "SELECT region, SUM(amount) AS revenue FROM orders WHERE status = 'completed' GROUP BY region",
    );
  await card.getByRole("button", { name: "Add question" }).click();
  await card.getByRole("checkbox", { name: /Approved data only/ }).check();
  await card.getByRole("button", { name: "Save approved questions" }).click();
  await expect(page.getByText("Approved questions saved")).toBeVisible();
  await expect(card.getByTestId("query-list")).toContainText("Approved");

  await page.getByRole("button", { name: "Start analysis" }).click();
  await expect(page).toHaveURL(/\/sessions\/[a-f0-9]+$/, { timeout: 15_000 });
  const composer = page.getByPlaceholder("Ask a question about this dataset...");
  await composer.fill("Completed revenue for each region");
  await composer.press("Enter");
  const result = page.getByTestId("approved-query-result");
  await expect(result).toContainText("Approved query", { timeout: 60_000 });
  await expect(result).toContainText("matched by code");
  const table = page.locator("section[id^='artifact-']").filter({ has: result });
  await expect(table.getByRole("cell", { name: "West" })).toBeVisible();
  await expect(table.getByRole("cell", { name: "100" })).toBeVisible();

  await composer.fill("How many orders are there?");
  await composer.press("Enter");
  const refusal = page.getByTestId("refusal");
  await expect(refusal).toContainText("Approved data only", { timeout: 60_000 });
  await expect(refusal).toContainText("What is completed revenue by region?");
});
