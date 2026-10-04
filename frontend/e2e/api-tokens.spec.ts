import { expect, test } from "./fixtures";

// The browser is routed to the isolated API by the fixture; direct calls must target it too.
const api = (path: string) => `${process.env.E2E_API_TARGET ?? "http://localhost:3000"}${path}`;

// A token created in the app works against the real (isolated) API, cannot write, and stops
// working once revoked.
test("an API token is shown once, can read, cannot write and can be revoked", async ({
  page,
  request,
}) => {
  await page.goto("/register");
  await page.locator("#email").fill(`tokens-${Date.now()}@example.com`);
  await page.locator("#password").fill("password123");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/datasets$/, { timeout: 15_000 });

  await page.getByRole("link", { name: "API tokens" }).click();
  await expect(page).toHaveURL(/\/api-tokens$/);
  await expect(page.getByText("No tokens yet.")).toBeVisible();
  await page.getByLabel("Token name").fill("Weekly export script");
  await page.getByRole("button", { name: "Create token" }).click();
  const value = page.getByTestId("new-token-value");
  await expect(value).toHaveText(/^ifk_/);
  const token = (await value.textContent())!.trim();
  await expect(page.getByTestId("token-list")).toContainText("Weekly export script");
  await expect(page.getByTestId("token-list")).toContainText("Active");

  const headers = { Authorization: `Bearer ${token}` };
  const datasets = await request.get(api("/api/datasets"), { headers });
  expect(datasets.status()).toBe(200);
  const write = await request.post(api("/api/sessions"), { headers, data: { dataset_id: "x" } });
  expect(write.status()).toBe(403);

  await page.getByRole("button", { name: "Done" }).click();
  await expect(page.getByTestId("new-token")).toHaveCount(0);
  await page.getByRole("button", { name: "Revoke Weekly export script" }).click();
  await expect(page.getByTestId("token-list")).toContainText("Revoked");
  expect((await request.get(api("/api/datasets"), { headers })).status()).toBe(401);
});
