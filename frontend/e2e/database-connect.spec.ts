import type { Page } from "@playwright/test";
import { expect, test } from "./fixtures";

// The Database tab builds the connection address in the browser and lists tables through the test
// endpoint. Server answers are mocked, so no database is needed and the server's settings do not matter.
async function openDatabaseTab(page: Page) {
  await page.goto("/register");
  await page.locator("#email").fill(`db-connect-${Date.now()}@example.com`);
  await page.locator("#password").fill("password123");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/datasets$/, { timeout: 15_000 });
  await page.getByRole("button", { name: "Add data" }).click();
  await page.getByRole("tab", { name: "Database" }).click();
  return page.getByTestId("database-form");
}

test("SQLite files are only offered when the server allows them", async ({ page }) => {
  await page.route("**/api/connections/options", (route) =>
    route.fulfill({ json: { sqlite_files: false } }),
  );
  const form = await openDatabaseTab(page);
  await expect(form.getByLabel("Database type")).toBeVisible();
  await expect(form.getByLabel("Database type").locator("option")).toHaveCount(6);
  await expect(form.getByLabel("Database type").locator("option[value='sqlite']")).toHaveCount(0);

  await page.reload();
  await page.unroute("**/api/connections/options");
  await page.route("**/api/connections/options", (route) =>
    route.fulfill({ json: { sqlite_files: true } }),
  );
  await page.getByRole("button", { name: "Add data" }).click();
  await page.getByRole("tab", { name: "Database" }).click();
  await expect(
    page.getByTestId("database-form").getByLabel("Database type").locator("option"),
  ).toHaveCount(7);
});

test("the database tab builds the address, lists tables and connects with a table choice", async ({
  page,
}) => {
  await page.route("**/api/connections/options", (route) =>
    route.fulfill({ json: { sqlite_files: false } }),
  );
  const tests: { uri: string; schema: string | null }[] = [];
  await page.route("**/api/connections/test", async (route) => {
    tests.push(route.request().postDataJSON());
    await route.fulfill({
      json: { kind: "postgres", tables: ["orders", "customers"], redacted_uri: "postgresql://x" },
    });
  });
  let connected: Record<string, unknown> | null = null;
  await page.route("**/api/datasets/from-connection", async (route) => {
    connected = route.request().postDataJSON();
    await route.fulfill({
      status: 400,
      json: { detail: "Mocked: connection not really made" },
    });
  });

  const form = await openDatabaseTab(page);
  await form.getByLabel("Database type").selectOption("mssql");
  await expect(form.getByLabel("Port")).toHaveAttribute("placeholder", "1433");
  await expect(form.getByLabel("Schema (optional)")).toBeVisible();
  await form.getByLabel("Database type").selectOption("mysql");
  await expect(form.getByLabel("Port")).toHaveAttribute("placeholder", "3306");
  await expect(form.getByLabel("Schema (optional)")).toHaveCount(0);

  await form.getByLabel("Database type").selectOption("postgres-cloud");
  await form.getByLabel("Host").fill("db.example.com");
  await form.getByLabel("Database", { exact: true }).fill("app");
  await form.getByLabel("User").fill("user@corp");
  await form.getByLabel("Password").fill("p@ss:w/rd");
  await form.getByLabel("Schema (optional)").fill("sales");
  await form.getByRole("button", { name: "Test connection" }).click();

  const tables = page.getByTestId("database-tables");
  await expect(tables).toContainText("Tables to use (2 of 2)");
  expect(tests[0]).toEqual({
    uri: "postgresql://user%40corp:p%40ss%3Aw%2Frd@db.example.com:5432/app?sslmode=require",
    schema: "sales",
  });
  await tables.getByRole("checkbox", { name: "customers" }).uncheck();
  await expect(tables).toContainText("Tables to use (1 of 2)");
  await form.getByLabel("Dataset name").fill("Sales data");
  await form.getByRole("button", { name: "Connect", exact: true }).click();
  await expect(page.getByText("Mocked: connection not really made")).toBeVisible();
  expect(connected).toEqual({
    uri: tests[0].uri,
    name: "Sales data",
    tables: ["orders"],
    schema: "sales",
  });
});

test("Snowflake builds an encoded address and starts with no tables ticked", async ({ page }) => {
  await page.route("**/api/connections/options", (route) =>
    route.fulfill({ json: { sqlite_files: false } }),
  );
  const tests: { uri: string; schema: string | null }[] = [];
  await page.route("**/api/connections/test", async (route) => {
    tests.push(route.request().postDataJSON());
    await route.fulfill({
      json: {
        kind: "snowflake",
        tables: ["customer", "nation", "orders", "region"],
        redacted_uri: "snowflake://x",
      },
    });
  });
  let connected: Record<string, unknown> | null = null;
  await page.route("**/api/datasets/from-connection", async (route) => {
    connected = route.request().postDataJSON();
    await route.fulfill({ status: 400, json: { detail: "Mocked: not really connected" } });
  });

  const form = await openDatabaseTab(page);
  await form.getByLabel("Database type").selectOption("snowflake");
  await expect(form.getByLabel("Warehouse")).toHaveValue("COMPUTE_WH");
  await expect(form.getByText("locator.region, e.g. ft45233.eu-central-2.aws")).toBeVisible();
  await expect(form.getByRole("button", { name: "Test connection" })).toBeDisabled();
  await form.getByLabel("Account identifier").fill("ft45233.eu-central-2.aws");
  await form.getByLabel("User").fill("kaami");
  await form.getByLabel("Password").fill("p@ss:w/rd?#1");
  await form.getByLabel("Database", { exact: true }).fill("SNOWFLAKE_SAMPLE_DATA");
  await form.getByLabel("Schema", { exact: true }).fill("TPCH_SF1");
  await form.getByLabel("Role (optional)").fill("MY ROLE");
  await form.getByRole("button", { name: "Test connection" }).click();

  const tables = page.getByTestId("database-tables");
  await expect(tables).toContainText("Tables to use (0 of 4)");
  await expect(tables.getByRole("checkbox", { checked: true })).toHaveCount(0);
  await expect(page.getByTestId("snowflake-credits")).toContainText("uses Snowflake credits");
  const uri =
    "snowflake://kaami:p%40ss%3Aw%2Frd%3F%231@ft45233.eu-central-2.aws/SNOWFLAKE_SAMPLE_DATA/TPCH_SF1" +
    "?warehouse=COMPUTE_WH&role=MY%20ROLE";
  expect(tests[0]).toEqual({ uri, schema: null });

  const connect = form.getByRole("button", { name: "Connect", exact: true });
  await expect(connect).toBeDisabled();
  await tables.getByRole("checkbox", { name: "nation" }).check();
  await tables.getByRole("checkbox", { name: "region" }).check();
  await form.getByLabel("Dataset name").fill("Sample");
  await connect.click();
  await expect(page.getByText("Mocked: not really connected")).toBeVisible();
  expect(connected).toEqual({ uri, name: "Sample", tables: ["nation", "region"] });
});
