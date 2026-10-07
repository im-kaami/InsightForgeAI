import { expect, test } from "./fixtures";

test("change the password, sign out and sign in again, then sign out everywhere", async ({
  page,
}) => {
  const email = `account-${Date.now()}@example.com`;
  await page.goto("/register");
  await page.locator("#email").fill(email);
  await page.locator("#password").fill("password123");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/datasets$/, { timeout: 15_000 });

  await page.getByRole("link", { name: "Account" }).click();
  await expect(page).toHaveURL(/\/account$/);
  await expect(page.getByTestId("account-email")).toHaveText(email);

  await page.locator("#current").fill("password123");
  await page.locator("#new").fill("a-new-password-1");
  await page.locator("#confirm").fill("different-password");
  await page.getByRole("button", { name: "Change password" }).click();
  await expect(page.getByTestId("account-error")).toContainText("do not match");

  await page.locator("#current").fill("wrong-password");
  await page.locator("#confirm").fill("a-new-password-1");
  await page.getByRole("button", { name: "Change password" }).click();
  await expect(page.getByTestId("account-error")).toContainText("Current password is incorrect");

  await page.locator("#current").fill("password123");
  await page.getByRole("button", { name: "Change password" }).click();
  await expect(page.getByText("Password changed; other sessions were signed out")).toBeVisible();
  await page.getByRole("link", { name: "Datasets" }).click();
  await expect(page).toHaveURL(/\/datasets$/);

  await page.evaluate(() => localStorage.removeItem("if_token"));
  await page.goto("/login");
  await page.locator("#email").fill(email);
  await page.locator("#password").fill("password123");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByText("Invalid email or password")).toBeVisible();
  await page.locator("#password").fill("a-new-password-1");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/datasets$/, { timeout: 15_000 });

  await page.goto("/account");
  await page.getByRole("button", { name: "Sign out everywhere" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Sign out everywhere" }).click();
  await expect(page).toHaveURL(/\/login$/, { timeout: 15_000 });
  expect(await page.evaluate(() => localStorage.getItem("if_token"))).toBeNull();
});

test("the reset page removes the secret from the address bar and rejects a bad link", async ({
  page,
}) => {
  await page.goto("/reset#ifr_not-a-real-secret");
  await expect(page.getByText("Choose a new password")).toBeVisible();
  await expect.poll(() => page.evaluate(() => window.location.hash)).toBe("");
  await page.locator("#password").fill("a-new-password-1");
  await page.locator("#confirm").fill("a-new-password-1");
  await page.getByRole("button", { name: "Set new password" }).click();
  await expect(page.getByTestId("reset-error")).toHaveText(
    "This reset link is invalid or has expired",
  );

  await page.locator("#confirm").fill("something-else-123");
  await page.getByRole("button", { name: "Set new password" }).click();
  await expect(page.getByTestId("reset-error")).toContainText("do not match");
});

test("email is off by default: the account card explains it and login has no forgot link", async ({
  page,
}) => {
  await page.goto("/login");
  await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Forgot password?" })).toHaveCount(0);

  await page.goto("/register");
  await page.locator("#email").fill(`email-off-${Date.now()}@example.com`);
  await page.locator("#password").fill("password123");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/datasets$/, { timeout: 15_000 });
  await page.getByRole("link", { name: "Account" }).click();
  const card = page.getByTestId("email-alerts");
  await expect(card.getByTestId("email-off")).toContainText("Email is not set up on this server");
  await expect(card.getByRole("checkbox")).toBeDisabled();
  await expect(card.getByRole("button", { name: "Send test email" })).toBeDisabled();
});

test("the forgot page says the same thing whatever happens", async ({ page }) => {
  await page.goto("/forgot");
  await page.locator("#email").fill("nobody@example.com");
  await page.getByRole("button", { name: "Send reset link" }).click();
  await expect(page.getByTestId("forgot-error")).toContainText("Email is not set up");
});
