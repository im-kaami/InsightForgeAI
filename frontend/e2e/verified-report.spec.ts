import type { Page } from "@playwright/test";
import { expect, test } from "./fixtures";
import { mkdir } from "node:fs/promises";

const salesCsv = `sale_id,order_id,sold_on,revenue,refund,cost,currency,status
p1,o1,2026-08-25,100,0,60,USD,paid
p2,o2,2026-08-27,200,20,100,USD,paid
c1,o3,2026-09-01,120,0,70,USD,paid
c2,o3,2026-09-02,80,10,40,USD,paid
c3,o4,2026-09-03,300,30,180,USD,paid
`;

async function register(page: Page, prefix: string) {
  await page.goto("/register");
  await page.locator("#email").fill(`${prefix}-${Date.now()}@example.com`);
  await page.locator("#password").fill("password123");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/datasets$/, { timeout: 15_000 });
}

async function uploadAndConfirm(page: Page, content = salesCsv) {
  await page.getByRole("button", { name: "Add data" }).click();
  await page.locator('input[type="file"]').setInputFiles({
    name: "sales.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(content),
  });
  await page.getByRole("button", { name: "Preview import" }).click();
  await expect(page.getByRole("heading", { name: "Review import" })).toBeVisible();
  await page.getByLabel("I reviewed the import preview").check();
  await page.getByRole("button", { name: "Confirm import" }).click();
  const row = page.getByRole("row").filter({
    has: page.getByRole("button", { name: "Ask" }),
  });
  await expect(row).toBeVisible({ timeout: 15_000 });
  await row.getByRole("button", { name: "sales" }).click();
  await expect(page).toHaveURL(/\/datasets\/[a-f0-9]+$/, { timeout: 15_000 });
}

async function saveDefinition(page: Page) {
  const form = page.getByRole("form", { name: "Approved report setup" });
  await form.getByLabel("Report name").fill("Weekly sales");
  await form.getByLabel("Sales table").selectOption("sales");
  await form.getByLabel("Unique row ID").selectOption("sale_id");
  await form.getByLabel("Order ID (optional)").selectOption("order_id");
  await form.getByLabel("Reporting date").selectOption("sold_on");
  await form.getByLabel("Gross sales amount").selectOption("revenue");
  await form.getByLabel("Refund amount").selectOption({ label: "sales.refund" });
  await form.getByLabel("Total cost amount").selectOption({ label: "sales.cost" });
  await form.getByRole("textbox", { name: "Currency", exact: true }).fill("USD");
  await form.getByLabel("Currency column").selectOption("currency");
  const approval = form.getByLabel("I approve these definitions and assumptions");
  const save = form.getByRole("button", { name: "Save approved report" });
  await approval.check();
  await form.getByLabel("Report name").fill("Weekly sales revised");
  await expect(approval).not.toBeChecked();
  await expect(save).toBeDisabled();
  await form.getByText("Advanced joins and filters").click();
  await form.getByRole("button", { name: "Add approved join" }).click();
  await expect(save).toBeDisabled();
  await form.getByRole("button", { name: "Remove join" }).click();
  await form.getByRole("button", { name: "Add filter" }).click();
  await expect(save).toBeDisabled();
  await form.getByRole("button", { name: "Remove filter" }).click();
  await approval.check();
  await expect(save).toBeEnabled();
  await save.click();
  await expect(page.getByText(/Saved definition version/)).toBeVisible();
}

async function runReport(page: Page) {
  await page.getByLabel("Saved report").selectOption({ index: 1 });
  await page.getByLabel("Dataset version").selectOption({ index: 1 });
  await page.getByLabel("Period start").fill("2026-09-01");
  await page.getByLabel("Period end").fill("2026-09-07");
  await page.getByRole("button", { name: "Run saved report" }).click();
  await expect(page).toHaveURL(/\/sessions\/[a-f0-9]+$/, { timeout: 15_000 });
}

test.describe("verified spreadsheet report", () => {
  test.setTimeout(120_000);

  test("approved report shows frozen evidence and downloadable CSV", async ({ page }) => {
    const consoleErrors: string[] = [];
    page.on("console", (message) => {
      if (message.type() === "error") consoleErrors.push(message.text());
    });
    await register(page, "verified");
    await uploadAndConfirm(page);
    await saveDefinition(page);
    await runReport(page);
    const card = page.getByTestId("run-card").last();
    await expect(card.getByText("completed", { exact: true })).toBeVisible({ timeout: 60_000 });
    await expect(card.getByText("Calculation checks passed")).toBeVisible();
    await card.getByText("Evidence and definitions").click();
    await expect(card.getByTestId("evidence-current.net_sales")).toBeVisible();
    await expect(card.getByTestId("evidence-current.margin_percent")).toBeVisible();
    await expect(card.getByTestId("evidence-current.order_count")).toBeVisible();
    await expect(card.getByTestId("evidence-current.net_sales")).toContainText("460.000000");
    await expect(card.getByTestId("evidence-current.margin_percent")).toContainText("36.96");
    await expect(card.getByTestId("evidence-current.order_count")).toContainText("2");
    await expect(card.locator(".js-plotly-plot svg.main-svg").first()).toBeVisible({
      timeout: 60_000,
    });
    await expect(card.getByText(/Source version:/)).not.toContainText("Not available");
    const downloadPromise = page.waitForEvent("download");
    await card.getByRole("button", { name: "Download CSV" }).click();
    const download = await downloadPromise;
    const content = await (
      await import("node:fs/promises")
    ).readFile(await download.path(), "utf8");
    expect(content).toContain("net_sales");
    expect(content).toContain("460.000000");
    await mkdir("e2e-artifacts", { recursive: true });
    await page.screenshot({ path: "e2e-artifacts/verified-report.png", fullPage: true });
    expect(consoleErrors).toEqual([]);
  });

  test("privacy changes use PATCH and update the current policy", async ({ page }) => {
    const privacyMethods: string[] = [];
    page.on("request", (request) => {
      if (request.url().includes("/privacy")) privacyMethods.push(request.method());
    });
    await register(page, "privacy");
    await uploadAndConfirm(page);
    await page.getByLabel("Data sharing").selectOption("schema_only");
    await page.getByLabel("I understand what this mode shares").check();
    await page.getByRole("button", { name: "Save privacy setting" }).click();
    await expect(page.getByText("Current policy:").locator("..")).toContainText("schema_only");
    expect(privacyMethods).toEqual(["PATCH"]);
  });

  test("activating a replacement refreshes preview and current report version", async ({
    page,
  }) => {
    await register(page, "current-version");
    await uploadAndConfirm(page);
    await page.getByRole("tab", { name: "Preview" }).click();
    await expect(
      page
        .locator("table")
        .filter({ hasText: "c3" })
        .last()
        .getByRole("row")
        .filter({ hasText: "300" }),
    ).toBeVisible();
    await page.getByRole("button", { name: "Upload new version" }).click();
    await page.locator('input[type="file"]').setInputFiles({
      name: "sales.csv",
      mimeType: "text/csv",
      buffer: Buffer.from(
        salesCsv.replace("c3,o4,2026-09-03,300,30,180", "c3,o4,2026-09-03,600,30,180"),
      ),
    });
    await page.getByRole("button", { name: "Preview import" }).click();
    await page.getByLabel("I reviewed the import preview").check();
    await page.getByRole("button", { name: "Confirm import" }).click();
    await page.getByRole("tab", { name: "Preview" }).click();
    await expect(
      page
        .locator("table")
        .filter({ hasText: "c3" })
        .last()
        .getByRole("row")
        .filter({ hasText: "600" }),
    ).toBeVisible();
    await expect(page.getByLabel("Dataset version").locator("option:checked")).toContainText(
      "current",
    );
  });

  test("closing a confirmed import resets the next Add data workflow", async ({ page }) => {
    await register(page, "dialog-reset");
    await page.getByRole("button", { name: "Add data" }).click();
    await page.locator('input[type="file"]').setInputFiles({
      name: "sales.csv",
      mimeType: "text/csv",
      buffer: Buffer.from(salesCsv),
    });
    await page.getByLabel("Dataset name (optional)").fill("First sales");
    await page.getByRole("button", { name: "Preview import" }).click();
    await page.getByLabel("I reviewed the import preview").check();
    await page.getByRole("button", { name: "Confirm import" }).click();
    await expect(page.getByRole("button", { name: "First sales" })).toBeVisible();
    const before = await page.evaluate(async () => {
      const token = localStorage.getItem("if_token");
      return fetch("/api/datasets", {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      }).then((response) => response.json());
    });
    await page.getByRole("button", { name: "Add data" }).click();
    await expect(page.getByRole("heading", { name: "Review import" })).toHaveCount(0);
    await expect(page.locator('input[type="file"]')).toBeVisible();
    await expect(page.getByRole("button", { name: "Preview import" })).toBeVisible();
    await page.locator('input[type="file"]').setInputFiles({
      name: "second.csv",
      mimeType: "text/csv",
      buffer: Buffer.from("id,value\n1,second\n"),
    });
    await page.getByLabel("Dataset name (optional)").fill("Second sales");
    await page.getByRole("button", { name: "Preview import" }).click();
    await page.getByLabel("I reviewed the import preview").check();
    await page.getByRole("button", { name: "Confirm import" }).click();
    await expect(page.getByRole("button", { name: "Second sales" })).toBeVisible();
    const after = await page.evaluate(async () => {
      const token = localStorage.getItem("if_token");
      return fetch("/api/datasets", {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      }).then((response) => response.json());
    });
    expect(after).toHaveLength(2);
    expect(new Set(after.map((dataset: { id: string }) => dataset.id)).size).toBe(2);
    expect(
      after.find((dataset: { id: string }) => dataset.id === before[0].id).current_version_id,
    ).toBe(before[0].current_version_id);
  });

  test("failed preview blocks confirmation", async ({ page }) => {
    await register(page, "preview-error");
    const previewRoute = /\/api\/datasets\/[^/]+\/preview/;
    await page.route(previewRoute, (route) =>
      route.fulfill({
        status: 500,
        contentType: "application/json",
        body: '{"detail":"Preview failed"}',
      }),
    );
    await page.getByRole("button", { name: "Add data" }).click();
    await page.locator('input[type="file"]').setInputFiles({
      name: "sales.csv",
      mimeType: "text/csv",
      buffer: Buffer.from(salesCsv),
    });
    await page.getByRole("button", { name: "Preview import" }).click();
    await expect(page.getByRole("alert")).toContainText("Preview failed");
    await page.getByLabel("I reviewed the import preview").check();
    await expect(page.getByRole("button", { name: "Confirm import" })).toBeDisabled();
    await page.unroute(previewRoute);
  });

  test("currency mismatch blocks a replacement-version report", async ({ page }) => {
    await register(page, "blocked");
    await uploadAndConfirm(page);
    await saveDefinition(page);
    await page.getByRole("button", { name: "Upload new version" }).click();
    await page.locator('input[type="file"]').setInputFiles({
      name: "sales.csv",
      mimeType: "text/csv",
      buffer: Buffer.from(
        salesCsv.replace("c1,o3,2026-09-01,120,0,70,USD", "c1,o3,2026-09-01,120,0,70,EUR"),
      ),
    });
    const uploaded = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        /\/api\/datasets\/[a-f0-9]+\/versions$/.test(response.url()),
    );
    await page.getByRole("button", { name: "Preview import" }).click();
    const replacement = await (await uploaded).json();
    await page.getByLabel("I reviewed the import preview").check();
    await page.getByRole("button", { name: "Confirm import" }).click();
    await expect(page.getByText("Current policy:")).toBeVisible();
    await expect(page.getByLabel("Dataset version").locator("option:checked")).toHaveText(
      `${replacement.id.slice(0, 8)} · current`,
    );
    await runReport(page);
    const card = page.getByTestId("run-card").last();
    await expect(card.getByText("Blocked by validation")).toBeVisible({ timeout: 60_000 });
    await expect(card.getByRole("alert")).toContainText(/currency/i);
    await expect(card.locator("table")).toHaveCount(0);
    await expect(page.getByText(/Local only:/)).toBeVisible();
  });
});
