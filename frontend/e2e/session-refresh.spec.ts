import { expect, test } from "./fixtures";

async function signUp(page: import("@playwright/test").Page, prefix: string) {
  await page.goto("/register");
  await page.locator("#email").fill(`${prefix}-${Date.now()}@example.com`);
  await page.locator("#password").fill("password123");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/datasets$/, { timeout: 15_000 });
}

test("a signed-in browser renews its session from the refresh cookie", async ({
  page,
  context,
}) => {
  await signUp(page, "refresh");
  const cookies = await context.cookies();
  const refresh = cookies.find((item) => item.name === "if_refresh");
  expect(refresh?.httpOnly).toBe(true);
  expect(refresh?.sameSite).toBe("Strict");
  expect(refresh?.path).toBe("/api/auth");
  expect(await page.evaluate(() => document.cookie)).not.toContain("if_refresh");

  // The access token is gone (as after closing the browser): the cookie signs the user back in.
  await page.evaluate(() => localStorage.removeItem("if_token"));
  await page.goto("/history");
  await expect(page).toHaveURL(/\/history$/);
  await expect(page.getByRole("link", { name: "Account" })).toBeVisible();
  expect(await page.evaluate(() => localStorage.getItem("if_token"))).toBeTruthy();
  const rotated = (await context.cookies()).find((item) => item.name === "if_refresh");
  expect(rotated?.value).not.toBe(refresh?.value);

  // An expired or invalid access token is renewed too, and the original request still succeeds.
  await page.evaluate(() => localStorage.setItem("if_token", "not.a.valid.token"));
  await page.goto("/datasets");
  await expect(page).toHaveURL(/\/datasets$/);
  await expect(page.getByRole("link", { name: "Account" })).toBeVisible();
  expect(await page.evaluate(() => localStorage.getItem("if_token"))).not.toBe("not.a.valid.token");
});

test("logging out ends the session even though the cookie existed", async ({ page }) => {
  await signUp(page, "logout");
  await page.getByRole("button", { name: "Log out" }).click();
  await expect(page).toHaveURL(/\/login$/);
  await page.goto("/datasets");
  await expect(page).toHaveURL(/\/login$/, { timeout: 15_000 });
  expect(await page.evaluate(() => localStorage.getItem("if_token"))).toBeNull();
});

test("signing out everywhere also ends the refresh cookie", async ({ page }) => {
  await signUp(page, "everywhere");
  await page.goto("/account");
  await page.getByRole("button", { name: "Sign out everywhere" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Sign out everywhere" }).click();
  await expect(page).toHaveURL(/\/login$/, { timeout: 15_000 });
  await page.goto("/datasets");
  await expect(page).toHaveURL(/\/login$/, { timeout: 15_000 });
});
