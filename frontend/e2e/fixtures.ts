import { expect, test as base } from "@playwright/test";

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
          const response = await route.fetch({ url: url.toString() });
          await route.fulfill({ response });
        });
      }
      await use();
    },
    { auto: true },
  ],
});

export { expect };
