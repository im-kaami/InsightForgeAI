import type { Browser, Page } from "@playwright/test";
import { expect, test } from "./fixtures";

const ordersCsv = `order_id,order_date,region,amount,status
1,2025-03-05,West,40,completed
2,2025-03-20,East,20,completed
`;

async function register(page: Page, email: string) {
  await page.goto("/register");
  await page.locator("#email").fill(email);
  await page.locator("#password").fill("password123");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/datasets$/, { timeout: 15_000 });
}

async function isolatedContext(browser: Browser) {
  // A second person in their own browser, routed to the isolated API like the main fixture.
  const context = await browser.newContext();
  const target = process.env.E2E_API_TARGET;
  if (target) {
    await context.route("**/api/**", async (route) => {
      const requested = new URL(route.request().url());
      const response = await route.fetch({
        url: new URL(requested.pathname + requested.search, target).toString(),
      });
      await route.fulfill({ response });
    });
  }
  return context;
}

test("an owner shares a dataset with a workspace and an invited viewer sees it", async ({
  page,
  browser,
}) => {
  const stamp = Date.now();
  const viewerEmail = `viewer-${stamp}@example.com`;
  await register(page, `owner-${stamp}@example.com`);
  await page.getByRole("button", { name: "Add data" }).click();
  await page
    .locator('input[type="file"]')
    .setInputFiles([{ name: "orders.csv", mimeType: "text/csv", buffer: Buffer.from(ordersCsv) }]);
  await page.getByRole("button", { name: "Preview import" }).click();
  await page.getByLabel("I reviewed the import preview").check();
  await page.getByRole("button", { name: "Confirm import" }).click();
  await expect(page.getByRole("row").filter({ hasText: "orders" })).toBeVisible({
    timeout: 15_000,
  });

  await page.getByRole("link", { name: "Workspaces" }).click();
  await page.getByLabel("New workspace name").fill(`Finance ${stamp}`);
  await page.getByRole("button", { name: "New workspace" }).click();
  const card = page.getByTestId("workspace-card");
  await expect(card).toContainText("Your role: owner");
  await card.getByLabel("Invite email").fill(viewerEmail);
  await card.getByRole("button", { name: "Create invitation" }).click();
  const inviteUrl = (await card.getByTestId("invite-url").textContent())!.trim();
  expect(inviteUrl).toMatch(/\/invite#ifi_/);

  await page.getByRole("link", { name: "Datasets" }).click();
  const row = page.getByRole("row").filter({ has: page.getByRole("button", { name: "Ask" }) });
  await row.getByRole("button").first().click();
  await expect(page).toHaveURL(/\/datasets\/[a-f0-9]+$/, { timeout: 15_000 });
  const datasetUrl = page.url();
  await page.getByLabel("Share with workspace").selectOption({ label: `Finance ${stamp}` });
  await expect(page.getByText("Shared with the workspace")).toBeVisible();

  const context = await isolatedContext(browser);
  const viewer = await context.newPage();
  await register(viewer, viewerEmail);
  await expect(viewer.getByRole("row").filter({ hasText: "orders" })).toHaveCount(0);
  await viewer.goto(inviteUrl);
  await viewer.getByRole("button", { name: "Accept invitation" }).click();
  await expect(viewer).toHaveURL(/\/workspaces/, { timeout: 15_000 });
  await expect(viewer.getByTestId("workspace-card")).toContainText("Your role: viewer");
  await expect(viewer.getByTestId("workspace-card")).toContainText("Dataset: orders");
  await viewer.goto(datasetUrl);
  await expect(viewer.getByTestId("dataset-access")).toHaveText("Shared with you: view only", {
    timeout: 15_000,
  });
  await expect(viewer.getByLabel("Share with workspace")).toHaveCount(0);
  await context.close();
});
