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
| `LOCAL_LLM_MODEL` | Ollama model used by `local` datasets, for example `qwen3:4b`; empty keeps the offline planner | empty |
| `LOCAL_LLM_BASE_URL` | Ollama address; must be on this computer (`localhost`) | `http://localhost:11434` |
| `LOCAL_LLM_CONTEXT_TOKENS` | Context window requested from Ollama | `8192` |
| `LOCAL_LLM_MAX_OUTPUT_TOKENS` | Maximum tokens per local-model reply | `1024` |
| `LOCAL_LLM_THINK` | Let thinking models reason before answering (much slower on small GPUs) | `false` |
| `LOCAL_LLM_TIMEOUT_SECONDS` | Timeout for each local-model request | `300` |

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
| `local` | No cloud LLM requests. With `LOCAL_LLM_MODEL` set, a model running on this computer through Ollama plans the analysis and writes a short summary with full access to the data. Otherwise exploratory chat uses local generic planning and summaries. |
| `schema_only` | Sends table/column identifiers, types, and current/prior questions. Result values, statistics, previous answers, and schema samples stay local. Questions can still contain data typed by the user. |
| `full` | Explicitly allows schema samples, result rows, statistics, and conversation history. These can contain sensitive data. |

Sensitive-column classification is a name-based heuristic, not a complete data-loss-prevention system. Saved verified reports never call the LLM regardless of mode.

Queries without their own `LIMIT` keep the first 10,000 rows. When a result is cut off, InsightForge counts the full result, marks the table as cut off, tells the summary which rows it covers, and adds a run warning. Charts use every row: bar and pie charts are summed in the database, histograms and heatmaps are binned or summed in the database, and large line, area, scatter, and box charts are evenly thinned or randomly sampled, with a note under the chart saying so. Supported chart kinds are line, bar, area, scatter, pie, histogram, box, and heatmap. In the web app, bar, line, area, and point charts can be switched between those four types without another AI call.

If the AI returns a plan with invalid steps, InsightForge lists the problems and asks the model once to correct the plan. It no longer drops invalid steps silently: any step that is still invalid appears as a run warning. Local models receive a JSON schema, so Ollama can only produce well-formed plans and summaries. When a plan has no chart, one is added from the shape of the first suitable result, and its note says it was added automatically.

Every number in an AI-written summary is checked against the results with fixed code. A number counts as matched when it equals, at the precision written, a table cell, a column total, or a row count; the match is recorded as evidence that links to the cell. Numbers that can't be found are listed in a run warning, and the run is marked **Needs review**. Numbers taken from the question or the SQL, small counts, and years are not checked. Each exploratory run also gets an **Assumptions** list, generated from the executed SQL: the tables read, joins, filters, groupings and calculations, row limits, cut-off results, and how many values were missing in the columns used. Reports and exports include both.

Each exploratory run stores a **run trace**: every model call (plan, plan repair, SQL repair, summary), query, chart, and number check, with timings. Model calls record the model used, what it was allowed to see (for example "table and column names, types and the question only"), the prompt size and token counts. Prompts and data values are not stored. The trace appears in the run card under **Run trace** and in report exports.

### Quick and Deep modes

Choose the mode under the chat box, or send `"mode": "deep"` to `POST /api/sessions/{id}/runs`.

- **Quick** (default) plans once, runs the plan, and summarizes.
- **Deep** runs the plan, then asks the model to review the results before summarizing. The review sees the question and a description of each result. It either accepts the results or supplies corrected or extra SQL steps; a step with the same name replaces the earlier result. Deep mode stops after `DEEP_MAX_ROUNDS` (default 3), `DEEP_MAX_SECONDS` (300), or `DEEP_MAX_TOKENS` (40,000), and records any stop reason as a warning. It needs an AI model; offline mode runs once. Reviews follow the privacy mode: schema-only reviews see row counts, columns, missing-value counts, and findings without data values.

**Clarifying questions.** When a chat question is ambiguous in a way that would change the answer, for example "Who are our best customers?" (by revenue or by number of orders?), a short ambiguity check before planning may ask first instead of guessing. The run finishes immediately with the question, 2 to 4 clickable options, and an "Other" box. Your answer starts a new run with the clarification appended, and that run may not ask again. Clarifying runs are left out of conversation memory. Schedules, the CLI, and Deep-mode revisions never ask.

In both modes, fixed code checks every query result:

- **Empty or zero results:** reported as a warning.
- **Filters that match no rows:** the filter's column is searched for similar values, so `location = 'remote'` suggests `'Remote'`.
- **Joins with repeated keys on both sides:** these multiply rows.

Filter and join problems mark the run **Needs review**, and appear in the warnings, the checks list, and the trace. Answers show a **Deep · N rounds** badge and the review decisions.

### Evaluation

`insightforge eval` scores the analyst on 32 reference questions over two synthetic datasets. Three are deliberately ambiguous and pass only if the analyst asks a clarifying question; asking on a clear question counts as a failure. The datasets are the HR fixture and a shop with orders and customers. The questions are in `backend/evals/suite.json`, and fixed SQL computes each correct answer, so the answers never come from an AI. A question passes when a result table contains the expected value, every expected row, or the expected winner as its first row.

```bash
cd backend
.venv/Scripts/insightforge.exe eval --model local                          # LOCAL_LLM_MODEL through Ollama
.venv/Scripts/insightforge.exe eval --model cloud                          # the configured provider; sends the synthetic data
.venv/Scripts/insightforge.exe eval --model local --case hr-headcount      # a single question
.venv/Scripts/insightforge.exe eval --model local --mode deep              # Deep mode (review and revise)
.venv/Scripts/insightforge.exe eval --model local --baseline evals/baselines/local-qwen3-4b.json
```

Reports are written to `backend/evals/results/` (not committed). The output shows accuracy, fallback rate, error rate, how many summary numbers were found in the results, average time, and tokens. With `--baseline`, it lists questions that now fail or now pass, and exits with code 1 when accuracy drops by more than `--max-drop` (default 0.05). Run it after changing prompts, models, the planner, or the executor.

The data health check reports, for each column:

- Numbers: range, median, quartiles, a 10-bin distribution, outliers (1.5 × interquartile range), and skew.
- Dates: range and days with no rows.
- Text: the most common values (hidden for sensitive columns) and values that look like numbers or dates but are stored as text.
- All columns: plain-language warnings for missing, constant, or identifier-like data.

Health checks made before this version show an **Update health check** button, which calls `POST /api/datasets/{id}/versions/{version_id}/profile`. Questions on saved versions reuse the schema saved at import instead of rescanning the data.

UI file uploads are staged and require preview and confirmation. The legacy upload API auto-confirms unless `review=true`; initial public URL imports and Add Source retain auto-confirm behavior, while manual URL refresh creates a draft. Profiling is bounded by time and column limits. Its outlier and skew checks are simple statistical rules, and it does not certify accuracy. A live database query is not a guarantee of source freshness.

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
