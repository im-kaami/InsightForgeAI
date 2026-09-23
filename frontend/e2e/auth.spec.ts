import { expect, test } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";

test("register, ask a question, see results", async ({ page }, testInfo) => {
  const email = `e2e-${Date.now()}@example.com`;
  const consoleErrors: string[] = [];
  const apiRequests: string[] = [];

  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  page.on("request", (request) => {
    if (request.url().includes("/api/")) {
      apiRequests.push(`${request.method()} ${request.url()}`);
    }
  });

  try {
    await page.goto("/register");
    await page.locator("#email").fill(email);
    await page.locator("#password").fill("password123");
    await page.getByRole("button", { name: "Create account" }).click();

    await expect(page).toHaveURL(/\/datasets$/, { timeout: 15_000 });
    await expect(page.getByText(email)).toBeVisible();

    await page.getByRole("button", { name: "Add data" }).click();
    await page.getByRole("tab", { name: "Upload files" }).click();
    await page
      .locator('input[type="file"]')
      .setInputFiles(resolve("../backend/tests/fixtures/hr.csv"));
    await page.getByRole("button", { name: "Upload" }).click();

    const datasetRow = page.getByRole("row").filter({ hasText: "hr" });
    await expect(datasetRow).toBeVisible({ timeout: 15_000 });
    await datasetRow.getByRole("button", { name: "Ask" }).click();

    await expect(page).toHaveURL(/\/sessions\/[a-f0-9]+$/, { timeout: 15_000 });
    const composer = page.getByPlaceholder("Ask a question about this dataset...");
    await composer.fill("What is the average salary by department?");
    await composer.press("Enter");

    await expect(page.getByText("completed", { exact: true })).toBeVisible({
      timeout: 60_000,
    });
    await expect(page.locator("table").last()).toBeVisible();
    await expect(page.getByRole("heading", { name: /summary/i }).last()).toBeVisible();
    await expect(page.locator(".js-plotly-plot svg.main-svg").first()).toBeVisible({
      timeout: 60_000,
    });
    const bodyFont = await page
      .locator("body")
      .evaluate((element) => getComputedStyle(element).fontFamily);
    expect(bodyFont.toLowerCase()).not.toContain("times");
    expect(consoleErrors).toEqual([]);
    expect(
      apiRequests.some((request) => request.includes("POST") && request.includes("/auth/register")),
    ).toBe(true);
    expect(apiRequests.some((request) => request.includes("/events"))).toBe(true);

    await mkdir("e2e-artifacts", { recursive: true });
    await page.screenshot({ path: "e2e-artifacts/session.png", fullPage: true });
  } finally {
    await testInfo.attach("console-errors", {
      body: consoleErrors.join("\n") || "none",
      contentType: "text/plain",
    });
    await testInfo.attach("api-requests", {
      body: apiRequests.join("\n") || "none",
      contentType: "text/plain",
    });
  }
});

test("register rejects short password without submitting", async ({ page }) => {
  await page.goto("/register");
  await page.locator("#email").fill(`short-${Date.now()}@example.com`);
  const password = page.locator("#password");
  await password.fill("short");
  await page.getByRole("button", { name: "Create account" }).click();

  await expect(page).toHaveURL(/\/register$/);
  expect(await password.evaluate((element: HTMLInputElement) => element.matches(":invalid"))).toBe(
    true,
  );
  expect(await password.evaluate((element: HTMLInputElement) => element.validity.tooShort)).toBe(
    true,
  );
  await expect(page.getByText("Password must be at least 8 characters")).toBeVisible();
});

test("register shows error toast for duplicate email", async ({ context, page }) => {
  const email = `duplicate-${Date.now()}@example.com`;
  await page.goto("/register");
  await page.locator("#email").fill(email);
  await page.locator("#password").fill("password123");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/datasets$/, { timeout: 15_000 });

  const freshPage = await context.newPage();
  await freshPage.goto("/register");
  await freshPage.locator("#email").fill(email);
  await freshPage.locator("#password").fill("password123");
  await freshPage.getByRole("button", { name: "Create account" }).click();
  await expect(freshPage.getByText("Email is already registered", { exact: true })).toBeVisible({
    timeout: 10_000,
  });
  await freshPage.close();
});
