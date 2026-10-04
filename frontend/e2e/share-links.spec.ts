import { expect, test } from "./fixtures";

// A link created in the app opens without signing in, in a fresh browser context, and stops
// working once revoked.
test("a shared dashboard opens without signing in and can be revoked", async ({
  page,
  browser,
}) => {
  await page.goto("/register");
  await page.locator("#email").fill(`sharing-${Date.now()}@example.com`);
  await page.locator("#password").fill("password123");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/datasets$/, { timeout: 15_000 });

  await page.getByRole("link", { name: "Dashboards" }).click();
  await page.getByLabel("New dashboard name").fill("Board for the team");
  await page.getByRole("button", { name: "New dashboard" }).click();
  await expect(page).toHaveURL(/\/dashboards\/[a-f0-9]+$/, { timeout: 15_000 });

  await page.getByRole("button", { name: "Share" }).click();
  const panel = page.getByTestId("share-panel");
  await expect(panel.getByRole("button", { name: "Create link" })).toBeDisabled();
  await panel.getByRole("checkbox").check();
  await panel.getByRole("button", { name: "Create link" }).click();
  const url = (await panel.getByTestId("share-url").textContent())!.trim();
  expect(url).toMatch(/\/share#ifs_/);

  // A visitor with no account and no sign-in.
  const visitorContext = await browser.newContext();
  const target = process.env.E2E_API_TARGET;
  if (target) {
    await visitorContext.route("**/api/**", async (route) => {
      const requested = new URL(route.request().url());
      const response = await route.fetch({ url: new URL(requested.pathname, target).toString() });
      await route.fulfill({ response });
    });
  }
  const visitor = await visitorContext.newPage();
  await visitor.goto(url);
  await expect(visitor.getByTestId("shared-view")).toContainText("Board for the team");
  await expect(visitor.getByTestId("shared-view")).toContainText("read only");

  await page.getByRole("link", { name: "Sharing" }).click();
  const list = page.getByTestId("share-list");
  await expect(list).toContainText("Board for the team");
  await expect(list).toContainText("Active");
  await page.getByRole("button", { name: "Revoke link to Board for the team" }).click();
  await expect(list).toContainText("Revoked");

  await visitor.reload();
  await expect(visitor.getByTestId("share-error")).toContainText("expired or was revoked");
  await visitorContext.close();
});
