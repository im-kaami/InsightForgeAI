import type { Page } from "@playwright/test";
import { expect, test } from "./fixtures";

const staffCsv = `employee_id,work_mode,salary
E1, remote,100
E2,Remote,200
E3,office,300
E3,office,300
`;

async function openConfirmedDataset(page: Page) {
  await page.goto("/register");
  await page.locator("#email").fill(`cleaning-${Date.now()}@example.com`);
  await page.locator("#password").fill("password123");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/datasets$/, { timeout: 15_000 });
  await page.getByRole("button", { name: "Add data" }).click();
  await page.locator('input[type="file"]').setInputFiles({
    name: "staff.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(staffCsv),
  });
  await page.getByRole("button", { name: "Preview import" }).click();
  await page.getByLabel("I reviewed the import preview").check();
  await page.getByRole("button", { name: "Confirm import" }).click();
  const row = page.getByRole("row").filter({ has: page.getByRole("button", { name: "Ask" }) });
  await expect(row).toBeVisible({ timeout: 15_000 });
  await row.getByRole("button", { name: "staff" }).click();
  await expect(page).toHaveURL(/\/datasets\/[a-f0-9]+$/, { timeout: 15_000 });
}

test("a saved cleaning recipe becomes a reviewed draft and rules report failures", async ({
  page,
}) => {
  await openConfirmedDataset(page);
  const recipe = page.getByTestId("cleaning-recipe");
  await recipe.getByRole("button", { name: "Suggest from health check" }).click();
  const suggestion = page
    .getByTestId("suggestions")
    .locator("div", { hasText: "Remove rows of staff that repeat exactly" })
    .last();
  await suggestion.getByRole("button", { name: "Add step" }).click();

  await recipe.getByText("Add a step").click();
  await recipe.locator("#step-kind").selectOption("clean_text");
  await recipe.locator("#step-column").selectOption("work_mode");
  await recipe.locator("#step-case").selectOption("lower");
  await recipe.getByRole("button", { name: "Add to recipe" }).click();
  await expect(recipe.getByTestId("recipe-steps").getByRole("listitem")).toHaveCount(2);
  await expect(recipe.getByRole("button", { name: "Apply to current data as a draft" })).toBeDisabled();
  await recipe.getByRole("button", { name: "Save recipe" }).click();
  await expect(page.getByText("Recipe saved")).toBeVisible();
  await recipe.getByRole("button", { name: "Apply to current data as a draft" }).click();

  await expect(page.getByRole("heading", { name: "Review import" })).toBeVisible();
  const summary = page.getByTestId("recipe-summary");
  await expect(summary).toContainText("rows 4 to 3");
  await expect(summary).toContainText("make lower case");
  await expect(page.getByRole("cell", { name: "remote", exact: true }).first()).toBeVisible();
  await page.getByLabel("I reviewed the import preview").check();
  await page.getByRole("button", { name: "Confirm import" }).click();
  await expect(page.getByRole("heading", { name: "Review import" })).toBeHidden();

  const rules = page.getByTestId("validation-rules");
  await rules.getByText("Add a rule").click();
  await rules.locator("#rule-kind").selectOption("range");
  await rules.locator("#rule-column").selectOption("salary");
  await rules.locator("#rule-max").fill("250");
  await rules.getByLabel("Blocking (stops verified reports when it fails)").check();
  await rules.getByRole("button", { name: "Add to rules" }).click();
  await expect(rules.getByTestId("rule-list")).toContainText("staff.salary is at most 250");
  await rules.getByRole("button", { name: "Save and check rules" }).click();
  const report = rules.getByTestId("validation-summary");
  await expect(report).toContainText("1 blocking", { timeout: 15_000 });
  await expect(report).toContainText("1 of 3 rows breaks this rule");
  await expect(report).toContainText("Verified reports on this version are blocked");
});
