import { expect, test } from "./fixtures";

test("the usage page shows an empty state when no AI model answered", async ({ page }) => {
  await page.goto("/register");
  await page.locator("#email").fill(`usage-${Date.now()}@example.com`);
  await page.locator("#password").fill("password123");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/datasets$/, { timeout: 15_000 });

  await page.getByRole("link", { name: "Usage" }).click();
  await expect(page).toHaveURL(/\/usage$/);
  await expect(page.getByRole("heading", { name: "Usage" })).toBeVisible();
  await expect(page.getByTestId("usage-empty")).toContainText(
    "No AI-answered runs in this period.",
  );
  await page.getByRole("button", { name: "7 days" }).click();
  await expect(page.getByRole("button", { name: "7 days" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await expect(page.getByTestId("usage-empty")).toBeVisible();
  await expect(page.getByText("your provider's bill is authoritative")).toBeVisible();
});
