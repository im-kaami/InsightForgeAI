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
| `LLM_PRICES` | JSON map of exact model name to `{"input": ..., "output": ...}` in USD per million tokens, used for the Usage page cost estimates; models without a price show "price not set" | empty |
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
| `RUN_WORKERS` | Analyses that run at the same time (1-32); the rest wait in the queue | `4` |
| `RUN_MAX_ATTEMPTS` | Times a run is tried when the server restarts mid-run (1-5), then it is marked failed | `2` |
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

**Statistical tests (tested methods).** Ask whether a difference, association, or relationship is real ("Is the salary difference between Remote and New York significant?"), and the analyst runs a statistical test. The model only chooses the method and two columns; tested code (`scipy`, `statsmodels`) calculates the result from row-level data fetched through the guarded SQL. Tests on cut-off results use every row, or a random sample of 200,000.

- **Comparing groups** (`compare_groups`): Welch's t-test or Mann-Whitney for two groups; one-way ANOVA, Welch's ANOVA, or Kruskal-Wallis for more. A normality check (Shapiro-Wilk below 30 values per group) and an equal-spread check (Levene) pick the test. Effect sizes are reported (Hedges' g, rank-biserial, eta² or epsilon²), with Holm-adjusted pairwise follow-ups.
- **Comparing categories** (`compare_categories`): a chi-square test of independence, or Fisher's exact test for sparse 2×2 tables, with Cramér's V.
- **Correlation**: Pearson with a 95% confidence interval, or Spearman when the two disagree.
- **Regression** (`regression`): ordinary least squares for a numeric outcome, one numeric predictor, and optional controls held fixed (categories become indicator columns). It reports the coefficient with a 95% CI, R², and fit checks. Breusch-Pagan switches to robust HC3 errors when the spread is uneven; Jarque-Bera checks the residuals, and variance inflation flags overlapping predictors.
- **Why did it change?** (`explain_change`): questions such as "Why did total amount drop from May to June?" get a breakdown of the change between two periods by up to three category columns. Segment contributions add up to the total change. The change in the average per row is split symmetrically into a *mix* effect (the share of each segment changed) and a *rate* effect (values within segments changed), and the two add up exactly. Cautions cover thin segments, new or vanished segments, and "where, not why".

- **Forecasts** (`forecast`): "Forecast total order amount per month for the next 3 months" totals the measure per day, week or month. Incomplete first and last periods are dropped, and empty periods count as 0. Naive, seasonal-naive, and damped Holt-Winters models are backtested on the most recent periods, and the model with the smallest error forecasts up to 36 periods ahead. The approximate 95% range comes from its backtest errors and widens with each step. It warns when no model beats the naive forecast or when there is under two seasons of history, and a chart shows the history, forecast, and range.
- **Unusual values** (`anomalies`): "Were there any unusual weeks?" compares each period with an expected value, from a robust seasonal-trend decomposition (STL) when there are two or more seasons of history, otherwise a rolling median. A period is flagged when its robust score (distance in MAD units) exceeds 3.5, and a chart marks the flagged periods.

- **Customer groups** (`segments`): "Segment our customers by total order amount and number of orders" builds one row per customer, standardises 2–8 numeric columns, and runs k-means with a fixed seed. The number of groups (2–8) is chosen by silhouette score unless the question states it, and each group gets a size and a plain-language profile ("high spend, high orders").
- **A/B tests** (`ab_test`): a sample-ratio check flags splits far from 50/50. A 0/1 outcome gets a two-proportion z-test; a numeric outcome gets Welch's t-test, with CUPED variance reduction when a pre-experiment covariate is named. Results report the difference, a 95% CI, and relative lift.
- **Robustness check:** every group comparison, correlation, regression, and A/B test is re-run under reasonable alternatives: the other test family, the top and bottom 1% of values removed, no controls, no CUPED, or capped outcomes. The result says whether the conclusion holds in each, and a caution appears when it doesn't.

**Predict this column** (`predict`, Phase 3a). "Which factors predict whether a subscriber churns?" builds a prediction model with scikit-learn:

- **Problem type:** classification or regression, detected from the target.
- **Columns left out:** constants, ID-like columns, high-cardinality text, and dates.
- **Leakage:** any column that predicts the target almost perfectly (98% purity or |r| > 0.98) is dropped and named as possible leakage.
- **Holdout:** a time-ordered 80/20 split when a date column is given, otherwise a seeded random split (stratified for classes).
- **Leaderboard:** a baseline against linear or logistic regression, random forest, and gradient boosting, chosen by cross-validation on the training part only and scored on held-out rows (balanced accuracy or mean absolute error, plus ROC AUC or R²).
- **Explanation:** permutation importance for the best model.

Cautions cover small data, a model barely better than the baseline, and "importance is not cause".

**Python sandbox (free-form code).** With Docker running and `SANDBOX_ENABLED=true`, questions that explicitly ask for Python, a simulation, or a bootstrap ("Using Python, compute the median salary for each department") get a `python` step. The AI writes code that reads the SQL result as `df` and assigns `result`. The code runs in the `insightforge-sandbox:1` container:

- no network
- read-only root filesystem, with the input table mounted read-only
- all Linux capabilities dropped and no privilege escalation
- user 65534
- 512 MB memory with no swap, 1 CPU, 128 processes, and a 30-second limit

Build the image once with `docker build -t insightforge-sandbox:1 backend/sandbox`; it is pinned to `python:3.11.15-slim-bookworm` by digest and the backend's library versions. Results are labelled **Free-form code (sandboxed)**, the least trusted tier, and the code, output, and errors are shown in full. When the sandbox is off, the step reports that instead of running anything.

**Notebook export.** **Download report → Notebook (.ipynb)** (or `GET /api/runs/{id}/report?format=ipynb`) turns a run into a Jupyter notebook in the order the steps ran:

- the question and model
- a DuckDB setup cell
- each guarded SQL query with its saved preview
- each tested method as a `run_test(...)` call with its interpretation, checks, and cautions
- sandboxed code with its output
- the summary, assumptions, and warnings

Load the data in the first cell to rerun it. The file passes `nbformat` validation.

When a tested method rejects its data (for example only one period came back), the AI gets one chance to rewrite the data query. In schema-only mode it sees only a value-free description of the problem.

Every result carries a "tested method" label, a plain-language reading written by code, and the assumption checks. Cautions cover small groups, sparse tables, "not proof of cause", and effects too uncertain to call. Several tests in one answer are Holm-adjusted. Questions asking *why* a number changed get a separate change-choice call, and questions about forecasts or unusual periods a series-choice call. Questions containing words such as *significant*, *chance*, *really*, *correlated*, *associated*, *controlling for* or *for each additional* first get a short test-choice call, because `qwen3:4b` never added test steps from the general planning prompt. The call picks the method, the columns, and the row-level SQL. It follows the privacy mode like planning does, so schema-only sends no values.

**Clarifying questions.** When a chat question is ambiguous in a way that would change the answer, for example "Who are our best customers?" (by revenue or by number of orders?), a short ambiguity check before planning may ask first instead of guessing. The run finishes immediately with the question, 2 to 4 clickable options, and an "Other" box. Your answer starts a new run with the clarification appended, and that run may not ask again. Clarifying runs are left out of conversation memory. Schedules, the CLI, and Deep-mode revisions never ask.

**Notes for the AI.** On the dataset page, owners can write general notes and business rules, and for each column a meaning, a unit, and other names ("revenue", "sales"). The notes go to the AI with the schema when planning, reviewing, repairing SQL, checking for ambiguity, and summarizing. That includes schema-only mode, so notes must not contain confidential values. A definition sentence such as "Revenue and sales mean completed orders only" is sent only when the question uses one of the terms it defines, because small models otherwise applied it to every question. Sentences without a definition, such as "Exclude test accounts", are always sent. They are saved with `PUT /api/datasets/{id}/notes`. On the evaluation set, `qwen3:4b` answered two revenue questions correctly (completed orders only) with the notes and got both wrong without them.

**Conversation memory.** Follow-up questions see up to five earlier questions in the session. Each comes with its queries and its assumptions. When the privacy mode lets values reach the model (full, or a local model), each also comes with its key numbers, taken from the summary's evidence, such as `avg_salary for department=Engineering = 106,333.33`, and a short summary. Schema-only follow-ups get the questions, queries, and assumptions without values.

In both modes, fixed code checks every query result:

- **Empty or zero results:** reported as a warning.
- **Filters that match no rows:** the filter's column is searched for similar values, so `location = 'remote'` suggests `'Remote'`.
- **Joins with repeated keys on both sides:** these multiply rows.

Filter and join problems mark the run **Needs review**, and appear in the warnings, the checks list, and the trace. Answers show a **Deep · N rounds** badge and the review decisions.

### Evaluation

`insightforge eval` scores the analyst on 48 reference questions over three synthetic datasets (HR, a shop, and a checkout experiment). Fourteen need a tested method: tests, change breakdowns, regression, forecasts, unusual values, customer groups, or A/B tests. They pass only if the right method runs on the right columns and data: the p-value, total change, coefficient, forecast, count, or number of groups must match a reference. Three are deliberately ambiguous and pass only if the analyst asks a clarifying question; asking on a clear question counts as a failure. Two depend on the shop dataset's notes ("revenue means completed orders only"). The datasets are the HR fixture and a shop with orders and customers. The questions are in `backend/evals/suite.json`, and fixed SQL computes each correct answer, so the answers never come from an AI. A question passes when a result table contains the expected value, every expected row, or the expected winner as its first row.

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

## SQL editor

**SQL editor** on a dataset page runs your own read-only SQL (DuckDB dialect) against the dataset's current confirmed version, or against a live database connection. The same SQL guard as the AI's queries applies: one `SELECT` (or `UNION`) only, with no changes to data, no file readers, `ATTACH` or `PRAGMA`. A query without its own `LIMIT` retrieves at most 10,000 rows (the page says when a result was cut and how many rows the full result has); the page shows up to 1,000 of them and **Download CSV** saves all retrieved rows. No AI model is involved and nothing is stored. Workspace members with view access can use it. It needs a signed-in session; API tokens cannot use it.

## Cleaning recipes and validation rules

**Cleaning recipes.** On the dataset page, the **Cleaning recipe** card holds an ordered list of steps: rename a column, change a type (with an optional date format, and either stop or leave unconvertible values empty), trim or change the case of text, replace values, fill missing values (a value, the mean, median, or most common value), remove rows with a missing value, remove duplicate rows (all columns or chosen key columns, keeping the first), keep or remove rows by a condition, and add a calculated column. Calculated columns are single row-by-row SQL expressions checked by the SQL guard; subqueries, other tables, totals, and window functions are refused. **Apply to current data as a draft** runs the steps on a copy of the current version's imported data and opens the usual import review, which lists each step with rows before and after and the number of values changed. The original import is kept as `raw.duckdb` next to the cleaned catalog, so re-applying never cleans already cleaned rows. With **Apply automatically** on (the default), every new upload or refresh gets the recipe too. If a step no longer fits, for example a renamed source column, the draft keeps the imported data unchanged and the review explains which step failed.

**Validation rules.** The **Validation rules** card checks, on every row, that a column is never missing, has no repeated values, stays in a number range, uses allowed values, matches a regular expression, or has a recent latest date, and that a table's row count is in a range. Rules are checked on every new version, when they are saved, and before every verified report. Each rule is a warning (the default) or blocking. A failing warning marks a verified report as Needs review; a failing blocking rule, or a blocking rule that cannot be checked, stops it with the result Blocked. The freshness rule looks at the dates in the data, not at when the source was last updated. Example failing values are shown except for columns flagged as sensitive.

**Suggestions.** **Suggest from health check** proposes steps (drop exact duplicates, convert numbers or dates stored as text, merge values that differ only in case or spaces) and rules the current data already meets. Suggestions come from fixed checks on the health check; no AI model is involved, and nothing is saved until you add it and save. Recipes and rules are not available for live database connections.

## Table relationships (join suggestions)

When a dataset has two or more tables, the dataset page shows a **Table relationships** card. **Suggest joins** checks the data with fixed rules, not an AI model. A column pair is suggested when:

- the names match (`customer_id` in both tables, or `orders.customer_id` and `customers.id`);
- the types fit;
- the key on the "one" side has no repeated or empty values;
- at least 90% of the other side's values are found in it.

Each suggestion shows the match rate and how many rows find no match. Add the ones you agree with, or add a relationship by hand, then **Save relationships**.

Saved relationships are added to the AI's planning, review, and SQL-repair prompts as join hints, for example "orders.customer_id -> customers.customer_id (many orders rows to one customers row)". Only table and column names are shared, never values. Relationships whose columns are no longer in the data are left out of prompts. The hints help the AI choose the right join; they do not guarantee it, and the existing many-to-many join check still flags risky joins. Suggestions are not available for live database connections, but you can add relationships by hand.

## Metrics

**Define a metric once.** The dataset page's **Metrics** card lets you define the numbers your business uses, such as revenue: a name, a label, the table, the calculation (sum, number of rows, number of distinct values, average, smallest or largest), the column, conditions that always apply (for example, status equals completed), an optional date column, the columns it may be grouped or filtered by, other names (such as "sales"), a unit and a description. **Suggest metrics** drafts a row count and column totals from the column types; nothing is approved until you tick **I checked this definition; the AI may use it** and save. **Preview** calculates any metric, even an unsaved one, on the current data and shows the SQL.

**Ask with a metric.** When a question names an approved metric, its label or one of its other names ("What are sales by region?"), the AI only chooses the groupings, filters, time period (day, week, month or year) and date range; tested code writes the SQL. The result table carries an **Approved metric** badge with the definition, and the Assumptions list starts with the metric and its revision. The AI can never label its own SQL as an approved metric, and metric SQL is never rewritten by the AI. Groupings from another table need an approved table relationship from the metric's table to that table (many rows to one), so a join can never count a row twice. Without an AI model (local mode with no local model), questions that name exactly one metric are still answered: the groupings and period are read from words such as "by region" and "monthly".

Limits: questions that do not name a metric are planned as before; questions that ask for a test, a forecast, a prediction or "why" go to those methods first. In schema-only mode the AI sees metric names, descriptions and allowed columns, but not the values in fixed conditions. A period or date range is used only when the question mentions time (a year, a month, "monthly", "last", and so on).

**Checked report for a metric.** Every saved, approved metric with a date column has a **Checked report** button on the Metrics card. Choose the first and last day of a period (up to 366 days) and, optionally, one of the metric's groupings to split by. The report opens in its own session and compares the period with the preceding period of the same length: a table with the total and each group (current, previous, change and change in percent), a chart, and a summary that cites its evidence. No AI model is used: tested code writes the SQL, calculates every number and writes the summary. Before calculating, it checks the data:

- dates that cannot be read stop the report, as does a period with no rows, and so do keys in a joined table that are not unique (they would count rows twice);
- rows without a date, rows with no value to calculate, and rows with no matching row in a joined table are listed as review notes and mark the report **Needs review**;
- blocking validation rules stop the report, as they do for the sales report.

When the previous period has no rows, or its value is zero or negative, the change is shown as unavailable, not as zero. The report freezes the dataset version, the metric definition and its revision.

**Follow a metric.** Under each approved metric with a date column, **Follow** watches it for you. Choose a window in days (for example 30), the change in percent that should raise an alert, optionally a grouping to split by, and when to check: **Every day**, **Every Monday** or **On the 1st of each month** (all at 08:00 in your browser's time zone), and/or **when new data is confirmed**. **Check now** runs a check at once.

Each check runs the checked report for the last N days that have data (ending on the latest date in the metric's rows) against the N days before. It records the result in plain words, for example "Revenue rose 116.67% in the 30 days to 2025-04-25 (GBP 130 against GBP 60 in the previous 30 days); at or above the 50% alert threshold." When the total changes by at least the threshold, or the check cannot run (the metric was removed, a blocking check failed), an alert appears under **Metric alerts** in the sidebar until you **Dismiss** it. **Open report** shows the full checked report. No AI is used. The last 100 checks per follow are kept; alerts are in-app only for now.

## Value index

People rarely type values exactly as they are stored: "the west" for `West`, "electronic goods" for `Electronics`, "business customers" for `Business`. Before planning, code reads the distinct values of short, category-like text columns (at most 500 values each; identifiers, sensitive columns and long free text are skipped) and matches the question's words to them, allowing different case, plurals and small typos ("close spelling"). No AI is involved.

- With **Full** sharing, or **Local** with a local model, the matches go into the planning prompt, so the AI filters on the exact column and spelling.
- In **Schema only** mode no values are sent; the matches are used by code only.
- Without an AI model, a question that names an approved metric and a value ("What is revenue in the west?") is filtered on that value, if the metric may be grouped by its column.
- Every answer lists the matches in its Assumptions ("west" -> orders.region = 'West'), so you can see how a word was read.

The index is built for each question from the confirmed data version, so it is never stale. Live database connections are not scanned.

## Approved questions and "Approved data only"

**Save a question with its SQL.** The dataset page's **Approved questions** card stores questions together with the SQL that answers them. Add one by hand (one read-only query; it is run once on the current data before it can be saved), or click **Save as approved question** under any result table in a session to save that answer's SQL with the question you asked.

**Ask it again.** When a new question asks the same thing, the saved SQL runs exactly as approved and the result carries an **Approved query** badge with the saved question and how it was matched. Code matches first by comparing words, so rewording works but different numbers ("top 5" against "top 10") never match. If code finds nothing, the AI may pick a saved question by its id; it sees only the questions, never the SQL, and it can never change the SQL. Approved SQL is never rewritten by the AI after an error.

**Approved data only.** Tick **Approved data only** on the same card to answer only from approved metrics and approved questions. Any other question is refused with a list of what can be asked, and the AI never writes SQL of its own. Statistical tests, forecasts, predictions and Deep mode are switched off in this mode because they run AI-written SQL.

## Saved models, scoring and drift

**Save a model.** A "predict this column" result card has a **Save model** button with a name field. Saving re-runs the prediction's query on the exact dataset version the answer used, retrains the chosen model with the same fixed settings, and stores it with that version, the target, the features, and the held-out scores. If the retrained score differs from the one the answer reported, the model is still saved with a warning.

**Score new data.** The dataset page's **Saved models** card lists each model. Pick any confirmed version (for example, next month's upload) and choose **Score and check drift**. You get a prediction for every row (plus a probability for yes/no targets), a preview, and a CSV download of the latest scores. When the new data also has the outcome column, the card compares accuracy on it with the held-out accuracy from training.

**Drift.** For each input, the population stability index (PSI) compares the new values with the training values: below 0.1 is stable, 0.1 to 0.25 a moderate shift, and above 0.25 a major shift. The card also shows the share of category values never seen in training and the change in missing values. Retraining is recommended when any input shows a major shift, or when accuracy on the new data is clearly worse (balanced accuracy down more than 0.05, or the typical error up more than 20%) and at least a moderate shift is present. All of this is calculated by fixed code; no AI model is involved.

**Scheduled checks and alerts.** Under **Monitoring** on each saved model, set a cron schedule and time zone (presets for daily, weekly, and monthly), or choose **Check now**. Each check scores the dataset's current version, stores the result in the model's history (rows, largest drift, verdict, or the error), and replaces the latest scores CSV. A scheduled check raises an alert when retraining is recommended or the check fails, for example because a column is missing or a blocking rule fails. Alerts show on the model and in the sidebar until you dismiss them. Checks you start by scoring a version by hand are kept in the history but never raise alerts. The last 100 checks are kept per model. Schedules run inside the backend process, so nothing runs while it is stopped, and alerts are only shown in the app (no email yet).

**Why did the model predict this? (SHAP).** After scoring, choose a row in the preview and **Explain this row**. Each input gets a contribution, so that the model's average output over 50 training rows plus the contributions equals this row's output. The output is the predicted value for number targets, the probability of the second class for yes/no targets, and the probability of the predicted class otherwise. A text column counts as one input. With up to 8 inputs the contributions are exact Shapley values; with more they are a seeded permutation estimate that still adds up exactly. The calculation uses the `shap` library (0.51.0) in tested code, and no AI is involved. Contributions describe how the model reacts to the inputs; they do not show that an input causes the outcome.

Limits: drift compares the new data with the training data; it does not prove the model is right or wrong, and it says nothing about causes. Scoring stops when the chosen version is missing a feature column, or when it breaks a blocking validation rule. Models are stored as files under the storage directory and kept until you delete the model or its dataset. Model files are only ever written and read by the server; never replace them with files from elsewhere, because the format can run code when loaded.

## Dashboards

**Dashboards** in the sidebar collects results in one place. A dashboard holds up to 30 tiles of three kinds:

- **Pinned result:** under any result table or chart in a session, **Pin to dashboard** copies it to a dashboard (or a new one). The copy never changes and shows which question produced it and how (approved metric, approved query or AI-written SQL). It stays when the session is deleted.
- **Approved metric report:** the checked report for an approved metric (with a date column) over the last N days that have data, against the N days before.
- **Approved question:** the result of an approved question's SQL on the current data.

**Refresh** (one tile) and **Refresh all** re-run the metric and question tiles on the current data version with tested code; no AI is used. If a refresh fails (for example, the metric or question was removed), the tile shows why and keeps its last good result. **Up** and **Down** reorder tiles. Deleting a dataset deletes its tiles from every dashboard, pinned copies included, because they contain its data.

## Single sign-on (OpenID Connect)

People can sign in with your company's identity provider, such as Google Workspace, Microsoft Entra ID, Okta or Auth0, instead of a password. Register InsightForge with the provider as a web application, with the redirect URI `http://localhost:3000/api/auth/oidc/callback` (your frontend address plus `/api/auth/oidc/callback`), and set these in `backend/.env` (see `.env.example`):

```
OIDC_ISSUER=https://accounts.google.com
OIDC_CLIENT_ID=...
OIDC_CLIENT_SECRET=...
OIDC_REDIRECT_URI=http://localhost:3000/api/auth/oidc/callback
OIDC_PROVIDER_NAME=Google
OIDC_ALLOWED_DOMAINS=["company.com"]
```

After a restart, the sign-in page shows **Sign in with Google** (or the name you set). InsightForge uses the authorization-code flow with PKCE, a one-time nonce and a state value tied to the browser by a short-lived cookie, and checks the ID token's signature against the provider's published keys, as well as its issuer, audience and expiry. The provider must confirm the email address; with `OIDC_ALLOWED_DOMAINS` set, only those domains can sign in. On first sign-in an account is created for that email; if a password account with the same email exists, it is used. The session token is handed back after `#` in the address, so it never reaches a server log. Password sign-in keeps working.

## Workspaces (teams and roles)

**Workspaces** in the sidebar lets you work with your team. Create a workspace, then **Invite someone** by email with a role; InsightForge shows an invitation link (valid for 7 days, once, for that address only) that you send yourself, since it does not send email. The invited person signs in with that address, opens the link and clicks **Accept invitation**.

Share what you own with one workspace: **Share with workspace** in a dataset's "Data sharing and privacy" card, and **Show to workspace** on a dashboard. What members can do depends on their role:

| Role | Shared datasets | Shared dashboards | Workspace |
|---|---|---|---|
| Viewer | View, preview, ask questions, run checked metric reports, follow metrics | View | See members |
| Editor | Also change notes, relationships, metrics, approved questions, cleaning recipes, validation rules and data versions | View | See members |
| Owner | Same as editor | View | Also invite, change roles, remove members, rename or delete it |

Only a dataset's or dashboard's own owner can change its privacy mode, stop sharing it, create share links to it, or delete it. Questions you ask stay in your own sessions; other members do not see them. A workspace always keeps at least one owner. When someone leaves or is removed, the datasets and dashboards they shared with it stop being shared. Deleting a workspace stops sharing everything in it. Saved prediction models, scheduled analyses and verified sales reports remain personal for now.

## Share links

**Share** on a dashboard or on a completed answer creates a read-only link that anyone can open without signing in. Choose when it expires (1, 7, 30 or 90 days) and tick that you understand anyone with the link can see the data it shows; the link is shown once (InsightForge keeps only a fingerprint of it). **Sharing** in the sidebar lists your links with their views and last view, and **Revoke** stops a link at once.

A shared answer shows its question, summary, result tables, charts and assumptions. A shared dashboard shows its tiles as they are now. Visitors never see SQL, the run trace, sources, downloads or your account, and cannot refresh or change anything. The secret part of the link comes after `#`, which browsers never send to a server, so it does not appear in server logs. API tokens cannot create share links.

## API access tokens

Use InsightForge from scripts and other tools with a personal API token. Open **API tokens** in the sidebar, give the token a name, choose its access and when it expires (1 to 365 days, 90 by default), and copy it: it is shown only once, and InsightForge stores only a SHA-256 fingerprint of it.

- **Read only:** read your datasets, previews, sessions, runs, reports and downloads (every `GET`).
- **Read and ask questions:** also start sessions (`POST /api/sessions`) and ask questions (`POST /api/sessions/{id}/runs`). Answers follow each dataset's privacy mode, as in the app.
- No token can upload or delete data, change settings, metrics or rules, or create, list or revoke tokens; those need a signed-in session. Revoked and expired tokens stop working at once. A token only ever reaches its owner's data.

```bash
TOKEN=ifk_...   # from the API tokens page
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/datasets
curl -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"dataset_id": "<dataset id>"}' http://localhost:8000/api/sessions
curl -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"goal": "What is revenue by region?"}' http://localhost:8000/api/sessions/<session id>/runs
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/runs/<run id>
```

Runs are asynchronous: the last call returns the run with status `pending` or `running`; fetch it again until it is `completed` or `failed`. The full API is described at `http://localhost:8000/docs` (OpenAPI). Each user can have at most 20 active tokens.

## MCP server (Claude Desktop, Cursor and other assistants)

Use your datasets, approved metrics and approved questions from any assistant that supports the Model Context Protocol. The server runs on your computer and talks to InsightForge with an API token, so it can reach only your data and only what the token allows.

1. Install the extra once: `backend/.venv/Scripts/python.exe -m pip install -e "backend[mcp]"`.
2. Create a token on the **API tokens** page: **Read and ask questions** to ask and run reports, or **Read only** to browse.
3. Add the server to your assistant. For Claude Desktop (`claude_desktop_config.json`) or Cursor (`.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "insightforge": {
      "command": "E:/InsightForgeAI/backend/.venv/Scripts/insightforge.exe",
      "args": ["mcp", "--url", "http://localhost:8000"],
      "env": { "INSIGHTFORGE_TOKEN": "ifk_..." }
    }
  }
}
```

Tools: `list_datasets`, `describe_dataset`, `ask` (waits for the checked answer), `metric_report` (a checked report for an approved metric) and `get_run`. Results keep their trust label (approved metric, approved query or AI-written SQL), the assumptions and at most 50 rows per table.

The assistant sends what it receives to its own AI model, so each dataset's privacy mode decides what MCP returns:

| Privacy mode | What the assistant can see |
|---|---|
| Full sharing | Everything: sample values, approved SQL, answers and report numbers |
| Schema only | Table and column names and types, metric definitions without their fixed values, and approved questions; asking and reports are refused |
| Local | The dataset's name only |

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
- Per-provider token and cost tracking
