import { expect, hrCsv, test } from "./fixtures";
import { mkdir } from "node:fs/promises";

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
    await page.locator('input[type="file"]').setInputFiles(await hrCsv());
    await page.getByRole("button", { name: "Preview import" }).click();
    await expect(page.getByRole("heading", { name: "Review import" })).toBeVisible();
    await page.getByLabel("I reviewed the import preview").check();
    await page.getByRole("button", { name: "Confirm import" }).click();

    const datasetRow = page.getByRole("row").filter({
      has: page.getByRole("button", { name: "Ask" }),
    });
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
    await expect(page.getByRole("button", { name: "Download CSV" }).first()).toBeVisible();
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

test("double-clicking Ask creates one session", async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  await page.goto("/register");
  await page.locator("#email").fill(`ask-guard-${Date.now()}@example.com`);
  await page.locator("#password").fill("password123");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/datasets$/, { timeout: 15_000 });
  await page.getByRole("button", { name: "Add data" }).click();
  await page.locator('input[type="file"]').setInputFiles(await hrCsv());
  await page.getByRole("button", { name: "Preview import" }).click();
  await page.getByLabel("I reviewed the import preview").check();
  await page.getByRole("button", { name: "Confirm import" }).click();
  let creates = 0;
  await page.route("**/api/sessions", async (route) => {
    if (route.request().method() === "POST") {
      creates += 1;
      await new Promise((resolveDelay) => setTimeout(resolveDelay, 250));
    }
    await route.fallback();
  });
  const ask = page.getByRole("button", { name: "Ask" });
  await expect(ask).toBeVisible({ timeout: 15_000 });
  const navigationStarted = Date.now();
  await ask.dblclick();
  await expect(page).toHaveURL(/\/sessions\/[a-f0-9]+$/, { timeout: 15_000 });
  await expect(page.getByPlaceholder("Ask a question about this dataset...")).toBeVisible();
  console.log(JSON.stringify({ phase: "ask", elapsed_ms: Date.now() - navigationStarted }));
  expect(creates).toBe(1);
  await expect(page.getByText(/Jest worker encountered/i)).toHaveCount(0);
  expect(consoleErrors).toEqual([]);
});

test("draft Review and detail Start analysis create one session", async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  await page.goto("/register");
  await page.locator("#email").fill(`review-guard-${Date.now()}@example.com`);
  await page.locator("#password").fill("password123");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/datasets$/, { timeout: 15_000 });
  await page.getByRole("button", { name: "Add data" }).click();
  await page.locator('input[type="file"]').setInputFiles(await hrCsv());
  await page.getByRole("button", { name: "Preview import" }).click();
  await expect(page.getByRole("heading", { name: "Review import" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Confirm import" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  const navigationStarted = Date.now();
  await page.getByRole("button", { name: "Review" }).click();
  await expect(page).toHaveURL(/\/datasets\/[a-f0-9]+$/, { timeout: 15_000 });
  await expect(page.getByText("Version history")).toBeVisible();
  console.log(JSON.stringify({ phase: "review", elapsed_ms: Date.now() - navigationStarted }));
  await page.getByRole("button", { name: "Review" }).click();
  await expect(page.getByRole("heading", { name: "Review import" })).toBeVisible();
  await page.getByLabel("I reviewed the import preview").check();
  await page.getByRole("button", { name: "Confirm import" }).click();
  let creates = 0;
  await page.route("**/api/sessions", async (route) => {
    if (route.request().method() === "POST") {
      creates += 1;
      await new Promise((resolveDelay) => setTimeout(resolveDelay, 250));
    }
    await route.fallback();
  });
  const askStarted = Date.now();
  await page.getByRole("button", { name: "Start analysis" }).dblclick();
  await expect(page).toHaveURL(/\/sessions\/[a-f0-9]+$/, { timeout: 15_000 });
  await expect(page.getByPlaceholder("Ask a question about this dataset...")).toBeVisible();
  console.log(JSON.stringify({ phase: "ask", elapsed_ms: Date.now() - askStarted }));
  expect(creates).toBe(1);
  await expect(page.getByText(/Jest worker encountered/i)).toHaveCount(0);
  expect(consoleErrors).toEqual([]);
});

test("Ask unlocks after a failed session request", async ({ page }) => {
  await page.goto("/register");
  await page.locator("#email").fill(`retry-guard-${Date.now()}@example.com`);
  await page.locator("#password").fill("password123");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/datasets$/, { timeout: 15_000 });
  await page.getByRole("button", { name: "Add data" }).click();
  await page.locator('input[type="file"]').setInputFiles(await hrCsv());
  await page.getByRole("button", { name: "Preview import" }).click();
  await page.getByLabel("I reviewed the import preview").check();
  await page.getByRole("button", { name: "Confirm import" }).click();
  let failed = false;
  await page.route("**/api/sessions", async (route) => {
    if (route.request().method() === "POST" && !failed) {
      failed = true;
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: '{"detail":"Temporary session failure"}',
      });
      return;
    }
    await route.fallback();
  });
  const ask = page.getByRole("button", { name: "Ask" });
  await expect(ask).toBeVisible({ timeout: 15_000 });
  await ask.click();
  await expect(page.getByText("Temporary session failure")).toBeVisible();
  await expect(ask).toBeEnabled();
  await page.unroute("**/api/sessions");
  await ask.click();
  await expect(page).toHaveURL(/\/sessions\/[a-f0-9]+$/, { timeout: 15_000 });
  await expect(page.getByPlaceholder("Ask a question about this dataset...")).toBeVisible();
});

test("polling recovers when the run event stream fails", async ({ page }) => {
  await page.goto("/register");
  await page.locator("#email").fill(`stream-${Date.now()}@example.com`);
  await page.locator("#password").fill("password123");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/datasets$/, { timeout: 15_000 });
  await page.getByRole("button", { name: "Add data" }).click();
  await page.locator('input[type="file"]').setInputFiles(await hrCsv());
  await page.getByRole("button", { name: "Preview import" }).click();
  await page.getByLabel("I reviewed the import preview").check();
  await page.getByRole("button", { name: "Confirm import" }).click();
  const datasetRow = page.getByRole("row").filter({
    has: page.getByRole("button", { name: "Ask" }),
  });
  await expect(datasetRow).toBeVisible({ timeout: 15_000 });
  await datasetRow.getByRole("button", { name: "Ask" }).click();
  await expect(page).toHaveURL(/\/sessions\/[a-f0-9]+$/, { timeout: 15_000 });
  await page.route("**/api/runs/*/events", (route) => route.abort());
  const composer = page.getByPlaceholder("Ask a question about this dataset...");
  await composer.fill("Profile departments");
  await composer.press("Enter");
  await expect(page.getByText("completed", { exact: true })).toBeVisible({ timeout: 60_000 });
  await expect(page.getByTestId("run-card")).toHaveCount(1);
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
