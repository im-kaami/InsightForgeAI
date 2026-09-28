import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { expect, test as base } from "@playwright/test";

export async function hrCsv() {
  return {
    name: "hr.csv",
    mimeType: "text/csv",
    buffer: await readFile(resolve("../backend/tests/fixtures/hr.csv")),
  };
}

export const test = base.extend<{ isolatedApi: void }>({
  isolatedApi: [
    async ({ context }, use) => {
      const target = process.env.E2E_API_TARGET;
      if (target) {
        const upstream = new URL(target);
        if (
          !["http:", "https:"].includes(upstream.protocol) ||
          !["localhost", "127.0.0.1"].includes(upstream.hostname)
        )
          throw new Error("E2E_API_TARGET must be a loopback HTTP(S) service");
        await context.route("**/api/**", async (route) => {
          const requested = new URL(route.request().url());
          if (!["localhost", "127.0.0.1"].includes(requested.hostname)) {
            await route.continue();
            return;
          }
          const url = new URL(requested.pathname + requested.search, upstream);
          try {
            const response = await route.fetch({ url: url.toString() });
            await route.fulfill({ response });
          } catch (error) {
            if (!/disposed|has been closed/i.test(String(error))) throw error;
          }
        });
      }
      await use();
      await context.unrouteAll({ behavior: "ignoreErrors" });
    },
    { auto: true },
  ],
});

export { expect };
