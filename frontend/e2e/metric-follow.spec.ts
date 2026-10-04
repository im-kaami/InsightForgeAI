import { expect, test } from "./fixtures";

// Runs against the isolated fake API. Following a metric and checking it never uses an AI model,
// so the window, the change and the alert here are the real calculation.
const ordersCsv = `order_id,order_date,region,amount,status
1,2025-03-05,West,40,completed
2,2025-03-20,East,20,completed
3,2025-04-10,West,100,completed
4,2025-04-25,East,30,completed
5,2025-04-28,East,500,refunded
`;

test("a followed metric is checked and a large change raises an alert", async ({ page }) => {
  await page.goto("/register");
  await page.locator("#email").fill(`metric-follow-${Date.now()}@example.com`);
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
  await card.getByLabel("Filter column").selectOption("status");
  await card.getByLabel("Filter value").fill("completed");
  await card.getByRole("button", { name: "Add condition" }).click();
  await card.getByLabel("I checked this definition; the AI may use it").check();
  await card.getByRole("button", { name: "Add to list" }).click();
  await card.getByRole("button", { name: "Save metrics" }).click();
  await expect(page.getByText("Metrics saved")).toBeVisible();

  const follow = card.getByTestId("metric-follow");
  await follow.getByRole("button", { name: "Follow" }).click();
  const form = follow.getByTestId("metric-follow-form");
  await form.getByLabel("Window (days)").fill("30");
  await form.getByLabel("Alert at change of (%)").fill("50");
  await form.getByRole("button", { name: "Start following" }).click();
  await expect(follow).toContainText("Following", { timeout: 15_000 });
  await expect(follow).toContainText("Not checked yet.");

  await follow.getByRole("button", { name: "Check now" }).click();
  const last = follow.getByTestId("follow-last-check");
  // 30 days to 2025-04-25 (the latest completed order): 130 against 60 in the 30 days before.
  await expect(last).toContainText("Revenue rose 116.67% in the 30 days to 2025-04-25", {
    timeout: 60_000,
  });
  await expect(last).toContainText("at or above the 50% alert threshold");
  await expect(page.getByTestId("metric-alerts-nav")).toContainText("Revenue: up 116.67%", {
    timeout: 70_000,
  });
  await last.getByRole("button", { name: "Dismiss" }).click();
  await expect(page.getByText("Alert dismissed")).toBeVisible();
  await expect(page.getByTestId("metric-alerts-nav")).toHaveCount(0, { timeout: 15_000 });
});
