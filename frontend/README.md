# InsightForge frontend

The InsightForge frontend is a Next.js 16 App Router application for managing datasets, running conversational analyses, viewing tables and charts, downloading reports, and scheduling recurring work.

## Prerequisites

- Node.js 20 or newer
- The InsightForge backend running on `http://localhost:8000`

## Development

```bash
npm install
cp .env.local.example .env.local
npm run dev
```

Open `http://localhost:3000`. Requests under `/api` are proxied to the backend by the rewrite in `next.config.ts`; configure the upstream with `API_PROXY_TARGET`.

## Scripts

| Script                 | Purpose                                                       |
| ---------------------- | ------------------------------------------------------------- |
| `npm run dev`          | Start the development server                                  |
| `npm run build`        | Create a production build                                     |
| `npm run lint`         | Run ESLint                                                    |
| `npm run format`       | Format source and scripts with Prettier                       |
| `npm run format:check` | Check committed formatting                                    |
| `npm run gen:api`      | Regenerate TypeScript types from the backend OpenAPI document |
| `npm run e2e`          | Run Playwright browser tests against running services         |

See the [root README](../README.md) for backend setup, supported data sources, configuration, and architecture.
