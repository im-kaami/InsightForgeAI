# InsightForge AI

<p align="center">
  <img src="https://img.shields.io/badge/Category-Enterprise%20AI-blue?style=for-the-badge" alt="Enterprise AI">
  <img src="https://img.shields.io/badge/Models-Any%20OpenAI--compatible%20LLM-brightgreen?style=for-the-badge" alt="Any OpenAI-compatible LLM">
  <img src="https://img.shields.io/badge/Tools-SQL%20%7C%20Plots%20%7C%20Memory-orange?style=for-the-badge" alt="SQL, plots, and memory">
  <img src="https://img.shields.io/badge/Status-Active%20development-success?style=for-the-badge" alt="Active development">
</p>
<p align="center">
  <img src="docs/images/InsightForge Logo.png" width="175" height="175" alt="InsightForge logo">
</p>

## Overview

InsightForge turns a natural-language business question into a validated analysis plan, safe DuckDB SQL, interactive charts, and an executive summary. Version 0.2 runs this workflow on **your data** instead of a fixed demonstration schema. It discovers tables and columns, plans against the discovered schema, executes guarded read-only queries, and keeps conversational context for follow-up questions.

The same engine powers the web application, CLI, Python package, and submission notebook.

## What's new in 0.2

- **Schema-agnostic analysis:** introspection and planner prompts adapt to any supported table layout.
- **Multi-source ingestion:** combine local files, workbooks, public URLs, Google Sheets, and databases.
- **Full web application:** authenticated datasets, chat-style analysis, live run events, reports, and schedules.
- **Reusable core:** typed Pydantic artifacts, an OpenAI-compatible LLM client, offline fake mode, and a SELECT-only SQL guard.

## Supported data sources

| Source | Support |
| --- | --- |
| CSV / TSV / Parquet / JSON | Local files and multipart uploads |
| Excel | Multi-sheet `.xlsx`, `.xlsm`, and `.xls` workbooks |
| HTTP URLs | Direct CSV, Excel, Parquet, and JSON downloads |
| Google Sheets | Public share links, including individual tabs |
| PostgreSQL / MySQL / SQLite | Read-only DuckDB attachments |
| Other databases | SQLAlchemy URIs with installed drivers |

## Architecture

![Agent architecture](docs/images/Agent%20Architecture.png)

```text
Next.js frontend
       |
       v
FastAPI API -- auth / datasets / sessions / runs / schedules
       |
       +--> ingestion layer --> files / URLs / sheets / databases
       |
       +--> core agent --> planner --> SQL guard --> executor --> plots / summary
                                      |
                                      v
                                   DuckDB
```

The API persists users, datasets, runs, artifacts, and schedules through SQLAlchemy. Dataset files and DuckDB catalogs live under the configured storage directory.

## Quick start

### Backend

From the repository root:

```bash
python -m venv backend/.venv
backend/.venv/Scripts/python.exe -m pip install -e "backend[dev]"
cp .env.example backend/.env
cd backend
.venv/Scripts/uvicorn.exe insightforge.api.main:app --reload --port 8000
```

On Linux or macOS, use `backend/.venv/bin/python`, activate with `source backend/.venv/bin/activate`, and run `uvicorn` normally.

### Frontend

```bash
cd frontend
npm install
cp .env.local.example .env.local
npm run dev
```

Open [http://localhost:3000](http://localhost:3000). The API documentation is available at [http://localhost:8000/docs](http://localhost:8000/docs).

### CLI

```bash
insightforge schema data.xlsx
insightforge ask data.xlsx "Compare revenue by region and recommend actions"
insightforge ask data.xlsx "Profile this dataset" --fake --png --out out
```

`--fake` provides deterministic offline planning and summaries for demos and tests.

### Notebook

```bash
cd notebooks
../backend/.venv/Scripts/jupyter.exe nbconvert --execute --to notebook --inplace submission.ipynb
```

The notebook uses the package directly, defaults to the offline fake LLM when no API key is present, and includes both sales and HR examples.

## Configuration

Copy `.env.example` to `.env` and adjust these values:

| Variable | Purpose | Default |
| --- | --- | --- |
| `LLM_PROVIDER` | Provider preset or `fake` for offline mode | `openai` |
| `LLM_API_KEY` | API key for the configured provider | empty |
| `LLM_BASE_URL` | Optional endpoint override; required for `openai-compatible` | provider default |
| `LLM_MODEL` | Optional model override | provider default |
| `DATABASE_URL` | SQLAlchemy application database | `sqlite:///./insightforge.db` |
| `STORAGE_DIR` | Dataset catalogs, uploads, downloads, and run artifacts | `./storage` |
| `JWT_SECRET` | Access-token signing secret | `change-me` |
| `APP_SECRET` | Fernet key derivation secret for connection URIs | `change-me` |
| `ACCESS_TOKEN_MINUTES` | Access-token lifetime | `10080` |
| `CORS_ORIGINS` | JSON list of allowed frontend origins | `["http://localhost:3000"]` |
| `SCHEDULER_ENABLED` | Start APScheduler with the API | `true` |
| `AUTO_CREATE_TABLES` | Apply database migrations during application startup | `true` |
| `ENVIRONMENT` | Validation mode: `development` or `production` | `development` |
| `ALLOW_PRIVATE_URLS` | Permit private/internal URL ingestion for trusted development | `false` |
| `QUERY_TIMEOUT_SECONDS` | Maximum execution time for analysis SQL | `60` |
| `DUCKDB_MEMORY_LIMIT` | Memory available to each opened dataset catalog | `2GB` |
| `DUCKDB_THREADS` | DuckDB worker threads per catalog | `4` |
| `MAX_CONCURRENT_RUNS_PER_USER` | Pending/running analyses allowed per user | `2` |
| `MAX_UPLOAD_BYTES` | Maximum bytes accepted for each uploaded file | `200000000` |
| `LLM_SEND_SAMPLE_VALUES` | Include non-sensitive schema samples in planner prompts | `true` |
| `LLM_SUMMARY_MAX_ROWS` | Result rows included per table in summary prompts | `20` |

Provider presets:

| Provider | Default model | Default endpoint |
| --- | --- | --- |
| `openai` | `gpt-4o-mini` | OpenAI |
| `gemini` | `gemini-3.6-flash` | Google OpenAI-compatible API |
| `groq` | `llama-3.3-70b-versatile` | Groq OpenAI-compatible API |
| `ollama` | `llama3.1` | `http://localhost:11434/v1` |
| `openai-compatible` | `gpt-4o-mini` | Must be supplied with `LLM_BASE_URL` |
| `fake` | deterministic offline responses | none |

## What is sent to the LLM

| Mode | Outbound data |
| --- | --- |
| `local` | No LLM requests. Exploratory chat uses local generic planning and summaries. |
| `schema_only` | Sends table/column identifiers, types, and current/prior questions. Result values, statistics, previous answers, and schema samples stay local. Questions can still contain data typed by the user. |
| `full` | Explicitly allows schema samples, result rows, statistics, and conversation history. These can contain sensitive data. |

Sensitive-column classification is a name-based heuristic, not a complete data-loss-prevention system. Saved verified reports never call the LLM regardless of mode.

UI file uploads are staged and require preview and confirmation. The legacy upload API auto-confirms unless `review=true`; initial public URL imports and Add Source retain auto-confirm behavior, while manual URL refresh creates a draft. Structural profiling is bounded by function limits and does not certify accuracy or comprehensively detect outliers. A live database query is not a guarantee of source freshness.

## Verified spreadsheet reports

The web file-upload workflow stages immutable drafts. Review inferred columns, source fingerprints, previews, and quality counts before confirmation. Replacements are complete source sets; earlier confirmed versions remain available for reproducible reruns. Public URL and Google Sheets sources can be refreshed manually one source at a time, producing another draft that must be reviewed.

Approved sales reports use saved definitions and deterministic DuckDB calculations rather than a paid LLM. Supported assumptions are explicit row/order/date mappings, ISO/day-first/month-first text dates, decimal amounts, optional total refund and total cost mappings, one currency or a mapped currency column, up to two approved one-to-one joins, and up to five literal AND filters. Net sales, gross profit, margin, comparisons, checks, SQL, and evidence come from the backend report engine; the frontend never recalculates them.

Privacy defaults to local-only. Schema-only planning sends names, types, and questions while values remain local. Full cloud analysis must be explicitly acknowledged and may send questions, result rows, statistics, and summaries containing sensitive data. Saved verified reports stay local regardless of this setting.

Source freshness is recorded as unknown unless the source is a live connection. Import timestamps do not prove freshness, and live queries are unsnapshotted and do not certify freshness. Quality profiles are exact structural counts within bounded profiling limits, not a complete DLP system, anomaly model, or certification of source accuracy. Draft files consume storage until the dataset is deleted; automatic retention cleanup is not implemented yet.

The in-process scheduler continues to run exploratory goals. It is not durable verified-report automation and does not provide delivery guarantees or automatic approval/promotion.

## Safety

- SQL is parsed with SQLGlot and restricted to one SELECT or UNION statement.
- External file readers, unsafe scans, mutations, ATTACH, PRAGMA, and environment functions are rejected.
- Database connections are attached read-only where DuckDB supports it.
- Saved connection URIs are encrypted with Fernet and redacted in API responses.
- Downloads, uploads, Excel files, result rows, and SQL output have explicit size or row caps.
- Every API resource is owner-scoped.

URL and Google Sheets ingestion refuses non-HTTP(S) schemes and private, loopback, link-local, shared, or metadata addresses. Every redirect hop is validated, with a maximum of five redirects. DNS is checked before connecting, so DNS rebinding between validation and connection remains a known residual risk; production deployments should also enforce network egress rules.

## Analysis output example

```json
{
  "steps": [
    {
      "name": "revenue_by_date",
      "action": "sql",
      "query": "SELECT date, SUM(revenue) AS revenue FROM sales GROUP BY date ORDER BY date"
    },
    {
      "name": "revenue_chart",
      "action": "plot",
      "kind": "line",
      "data_source": "revenue_by_date",
      "x": "date",
      "y": "revenue",
      "title": "Revenue over time"
    },
    {
      "name": "summary",
      "action": "summary",
      "focus": "growth drivers"
    }
  ]
}
```

![Revenue over time](docs/images/Revenue%20Trends%20Over%20Time.png)

![Revenue by region](docs/images/Revenue%20Trends%20by%20Regions.png)

![Revenue by channel](docs/images/Revenue%20Trends%20by%20Channels.png)

## Development

Backend checks:

```bash
backend/.venv/Scripts/ruff.exe check backend
backend/.venv/Scripts/python.exe -m pytest backend -q
backend/.venv/Scripts/python.exe -m pytest backend -m integration
```

Set `INSIGHTFORGE_TEST_PG_URI` before running the PostgreSQL integration test.

Frontend checks:

```bash
cd frontend
npm run lint
npm run format:check
npm run build
npm run gen:api
```

Run `npm run gen:api` while the backend is available on port 8000 whenever API schemas change.

## Repository structure

```text
InsightForgeAI/
├── backend/
│   ├── insightforge/
│   │   ├── api/          # FastAPI app and routers
│   │   ├── core/         # planner, guard, executor, plotting, memory
│   │   ├── db/           # SQLAlchemy models and Alembic migrations
│   │   ├── ingest/       # file, URL, Sheets, and database loaders
│   │   └── services/     # auth, datasets, runs, reports, schedules
│   ├── tests/
│   └── pyproject.toml
├── frontend/
│   ├── src/app/          # Next.js App Router pages
│   ├── src/components/   # application and shadcn components
│   ├── src/lib/          # API client, generated types, auth, SSE
│   └── scripts/
├── notebooks/submission.ipynb
├── docs/images/
├── .github/workflows/ci.yml
└── README.md
```

## Roadmap

- Docker Compose for one-command local deployment
- Celery workers for durable distributed runs
- Native MSSQL, BigQuery, and Snowflake connectors
- Private Google Sheets through service-account authentication
- A governed semantic layer for reusable metrics
- Per-provider token and cost tracking
