# InsightForge data science roadmap: research report

This is the full research report from the Devin session "Transforming InsightForge into a Full-Stack Data Science Platform" (2026-09-27), saved so every agent can read it. The visual, plain-language version is `docs/lessons/0001-data-science-roadmap.html`.

- Code references describe the code as it was on 2026-09-27 (commit `dde2a5c`); line numbers may have moved since.
- Decisions made on 2026-09-28: InsightForge becomes a full data science platform and checked reports stay as one feature. The main users are both business people and data scientists, so the phase order below is kept. "Local only" mode uses `qwen3:4b` through Ollama.
- Progress is tracked in `AGENTS.md` (Roadmap and Change log). Phase 0a, finished on 2026-09-28, addressed the silent 10,000-row limit, the 500-row chart cap, "local only" mode without a local model, and the missing database lock on web analyses.

---

## Summary

Today InsightForge turns a question into SQL, a chart and a summary, with strong governance around it. That governance is its biggest asset. Datasets are saved as fixed versions with file fingerprints, and new data is reviewed before it goes live. SQL is limited to read-only queries, there are three privacy modes, and one sales report is calculated by fixed code with checks and evidence. A 2025 survey of 45 AI data science agents found that more than 90% had no explicit trust or safety features.

What's missing is the data science itself. InsightForge can't run Python, do statistics, train models, forecast or clean data. It also plans an analysis once, rather than working step by step and checking results like an analyst would.

The five changes that would matter most:
1. **Make the agent work in a loop:** plan, run a step, look at the result, check it, then continue.
2. **Add analysis methods that run as tested code:** statistical tests, "why did this change?" analysis, forecasting, clustering, automated model training (AutoML) and model explanations. Add a locked-down Python sandbox for anything else.
3. **Make local-only mode useful:** let it use a locally hosted model such as Ollama. Today it always falls back to a basic offline planner.
4. **Turn the one hard-coded sales report into a general metrics layer:** user-defined metrics, dimensions and joins, plus a library of approved question-and-SQL pairs.
5. **Add the rest of a full platform:** model lifecycle (versions, scoring, drift monitoring), alerts, notebooks, dashboards and team features.

---

## 1. Current state: what the code shows

**Strengths to keep:** reviewed imports with file fingerprints, the read-only SQL checker, protection against fetching internal URLs, encrypted connection strings, per-user data isolation, explicit privacy consent, a record of sources and settings on every run, and the checked sales report with its evidence references.

**Limitations I found:**

- **The agent plans once.** It makes one plan of at most 6 steps, using only SQL, chart and summary steps, and never looks at results to adjust. (`backend/insightforge/core/planner.py`, lines 93-138)
- **Invalid plan steps are quietly dropped** instead of being sent back to the model to fix. (`backend/insightforge/core/planner.py`, lines 103-109)
- **A failed query gets only one repair attempt.** (`backend/insightforge/core/executor.py`, lines 73-97)
- **Local-only mode always uses the offline stand-in model, even when Ollama is configured.** Local-only is the default for new datasets, so by default chat can only run a basic row-count and top-values summary. (`backend/insightforge/core/privacy.py`, lines 19-20)
- **Results are cut off without telling the user.**
  - The SQL checker adds `LIMIT 10000` to queries without one, and the reported row count then shows the capped number, not the true total. (`backend/insightforge/core/sql_guard.py`, lines 67-72)
  - Bar, line and pie charts only plot the first 500 rows. (`backend/insightforge/core/plotter.py`, lines 29-36)
- **Only 5 chart types, and users can't edit charts.** (`backend/insightforge/core/planner.py`, lines 20-28)
- **Conversation memory is thin:** it keeps the last 5 turns, with each answer cut to 300 characters. (`backend/insightforge/core/memory.py`, lines 13-22)
- **The data profile only counts things:** rows, exact duplicates, missing values and distinct values. There are no distributions, outliers or correlations. (`backend/insightforge/core/profiling.py`, lines 10-26)
- **Every run rescans the data** (row counts, missing-value share and sample values for every column), even though each dataset version already stores its schema. (`backend/insightforge/core/agent.py`, line 50), (`backend/insightforge/core/catalog.py`, lines 91-132)
- **Personal data detection only looks at column names**, never at the values themselves. (`backend/insightforge/core/sensitivity.py`, lines 58-67)
- **Checked reports exist for one template only** (`sales_margin_v1`). (`backend/insightforge/core/verified_report.py`, lines 33-50)
- **Web runs skip a safety lock.** `catalog.lock()`, which blocks access to files and external sources, is used by the command-line tool but not by web runs, so the SQL checker is the only barrier there. (`backend/insightforge/services/datasets.py`, lines 560-570)
- **Schedules run inside the web server process**, with no email or Slack delivery, no "only notify if" conditions, and no retries. (`backend/insightforge/services/scheduler.py`, lines 62-93)
- **There are only individual users:** no teams, roles or sharing. (`backend/insightforge/db/models.py`, lines 21-26)

---

## 2. Research findings

### What leading products offer

| Product | Relevant capabilities |
|---|---|
| **Julius** | Runs Python or R in a cloud sandbox and installs libraries itself; searches the database schema before writing SQL; lets users choose how hard it thinks; notebooks you can rerun; warehouse and ad-platform connectors |
| **Hex** | Notebook agent that edits SQL, Python and chart cells, with a before/after diff to accept or reject; a chat mode for business users that can be restricted to approved data and metric definitions; an agent that writes metric definitions; an admin view of what context the AI uses |
| **Deepnote** | Agent that plans, runs cells, reads the output, shows diffs and can undo; separate "ask" and "edit" modes; connects to external tools via MCP |
| **Databricks Genie Code** | Explores data, trains and evaluates models (tracked with MLflow), fixes its own errors, asks clarifying questions, waits for approval before running code, and respects existing data permissions |
| **Google Colab data science agent** | Produces a complete editable notebook: plan, exploration, cleaning, new features, model training and comparison, summary |
| **Snowflake Cortex Analyst** | Metric and dimension definitions stored in the database; a library of approved question-and-SQL pairs used as examples and as suggested starter questions |
| **ThoughtSpot Spotter 3** | "Why did this change?" analysis across dimensions; a deep mode that proposes hypotheses, lets the user edit the plan before it runs, and corrects itself; forecasting |
| **Tableau Pulse** | Users follow metrics and get automatic insights (drivers, outliers, unexpected values, trend changes) in Slack or email digests |
| **DataRobot, Dataiku, H2O** | AutoML leaderboards, automatic feature creation, model explanations (SHAP), bias checks, model versioning, deployment and drift monitoring; Dataiku also has 100+ point-and-click cleaning steps with a visual data-flow history |

### What research says about building data agents

- **Working in a loop beats planning once.**
  - Google's DS-STAR runs a planner, a code writer and a checker for up to 10 rounds. It raised accuracy on the DABStep benchmark from 41.0% to 45.2%, and on KramaBench from 31.3% to 44.7%.
  - Data Interpreter breaks a task into a graph of smaller steps and checks each one. It raised accuracy on InfiAgent-DABench from 75.9% to 94.9%.
- **Realistic tasks are still hard, so results need checking.** Top scores at launch were about 15% on DABstep's hard tasks, 34% on DSBench and 30.5% on DA-Code. On Spider 2.0, a realistic business SQL benchmark, the best agent solved 21%, compared with 91% on the older Spider 1.0.
- **Business context is the biggest lever for SQL accuracy.** A 2026 paper reports 94.15% on a Snowflake version of Spider 2.0. Its agent asks for metrics from a defined metrics layer, and fixed code turns that request into SQL.
- **Statistics is a weak spot.**
  - The P-Bench study (2026) found agents make subtle reasoning errors in hypothesis tests even when their code runs correctly.
  - A study using many AI "analysts" found their conclusions varied widely on the same data and often flipped between runs.
  - On the BLADE benchmark, the best agent scored 44.8%, and models mostly stayed with basic analyses.
- **Model-building agents are improving fast.** The best agents on MLE-bench now earn a medal in about 63–64% of Kaggle competitions, up from 16.9% at launch, though they get 24 hours per task.
- **The 2025 survey's recommendations:** ask clarifying questions, link every claim to its evidence, evaluate the intermediate steps and not just the final answer, run code in secure sandboxes, and keep a human in the loop.

### What users report

- **Databox (2026):** 74% of business users have shipped a wrong number produced by AI. When shown a flawed analysis, only 5% caught every error.
- **Alteryx (2026):** analysts spend 5.7 hours a week cleaning data and 3.7 hours a week checking AI output. Only 3% want fully autonomous AI, and 65% say AI works best when business logic is defined centrally.
- **insightsoftware (2026):** the top requirements for trusting AI answers are keeping data in-region (54%), audit trails (53%) and repeatable, checkable results (51%).
- **Anaconda (2025):** the biggest blockers to getting data science work into production are data quality (45%) and security, privacy or compliance (40%).
- **Pecan, on ChatGPT:** the mistakes happen around the code rather than in it: silent guesses about date formats, currency strings and duplicates, answers that change between runs, and no deployed model at the end.

**What this means for InsightForge:** "every number comes from tested code, can be traced to the data, and can be reproduced" is still an unmet need. Build the data science features on top of your existing trust features, rather than adding a model that writes and runs arbitrary code with no checks.

---

## 3. Gap analysis

### Coverage across the data science lifecycle

| Stage | Today | Missing |
|---|---|---|
| Understanding the question | Free-text question | Clarifying questions, a glossary, metric definitions beyond the sales report |
| Getting data | Files, URLs, public Google Sheets, PostgreSQL, MySQL, SQLite | Snowflake, BigQuery, Databricks, SQL Server, cloud storage, SaaS apps, private Sheets and Drive, PDFs |
| Cleaning and preparing data | Import options only | Cleaning, type fixes, joins, calculated columns, repeatable cleaning steps, validation rules |
| Exploring data | Counts-only profile, 5 chart types | Distributions, outliers, correlations, missing-data patterns, data quality alerts |
| Statistics | None | Tests that check their own assumptions, effect sizes, confidence intervals, corrections for many tests, regression, A/B tests |
| Modeling | None | AutoML, prediction models, clustering, forecasting, anomaly detection |
| Explaining results | AI-written summary | "Why did this change?", model explanations (SHAP), what-if, cause-and-effect analysis, summaries linked to evidence |
| Deploying and monitoring | Scheduled reruns of a question | Model versions, scoring new data, drift monitoring, alerts |
| Working together | Single user | Teams, roles, sharing, comments, notebooks, dashboards |

### A. Improvements to existing features

1. **Agent.** Keep today's planner as a fast "Quick" mode. Add a "Deep" mode that plans, runs one step, looks at the result, adjusts, checks and then writes the summary, with limits on steps, cost and time. Ask the model for output in a strict schema, and send invalid steps back for fixing instead of dropping them.
2. **Clarifying questions.** Let the agent ask the user when the metric, time period or comparison is unclear, and show the options as clickable choices.
3. **Local-only mode.** Treat a self-hosted model (Ollama, llama.cpp or vLLM on your own machine or network) as local, so local-only mode runs the real agent without data leaving your network.
4. **What the model can see in each privacy mode.** A looping agent needs to see results to correct itself.
   - With a local model, it can see everything.
   - In schema-only mode, it can see row counts, column types, missing-value counts and cleaned-up error messages, but no actual values.
   - In full mode, it can see a limited number of values.
5. **Honest results.** Detect when the 10,000-row limit cut a result off, summarize in SQL before charting instead of taking the first 500 rows, and show "N of M rows" on every table and chart.
6. **Summaries linked to evidence.** Reuse the evidence system from the checked sales report. Every number in a summary should point to the table cell it came from, and numbers that can't be matched should be flagged. Add an "Assumptions" section listing, for example, which date column was used and which rows were left out.
7. **Better data profiling.** DuckDB's built-in `SUMMARIZE` covers the basic statistics; add these on top:
   - histograms and most common values per column
   - numbers or dates stored as text
   - outliers
   - columns that are constant, highly skewed or have too many distinct values
   - correlations and missing-data patterns
   - gaps in time coverage

   The alert list in the ydata-profiling library is a good checklist.
8. **Charts.** Add box plots, heatmaps, stacked and area charts, treemaps, funnels and maps. Let users change the chart type and axes without another model call, and pin charts to a dashboard.
9. **Memory and context.** Replace the 300-character memory with structured findings: the question, key numbers with their evidence, and assumptions. Add dataset notes covering column descriptions, units, synonyms and business rules.
10. **Checked reports for any metric.** Generalize the sales template so users can define their own metrics and still get the same checks on uniqueness, joins and bad values. Add a library of approved question-and-SQL pairs, used as examples for the model and as starter questions.
11. **Personal data detection.** Scan sample values as well as column names, using patterns and checksums or Microsoft's Presidio library. Add per-column rules: never send, mask, or hash.
12. **Schedules.** Move them to a separate job queue with retries. Add delivery (email, Slack, Teams, webhook), conditions such as "notify only if the metric crosses X", and a comparison with the previous run.
13. **Speed.** Reuse each version's stored schema and profile instead of rescanning the data on every run.
14. **Security before adding code execution.** Block DuckDB's file and external access for the read-only version snapshots when they are opened (this needs testing on DuckDB 1.4.x). Log what data each run sent to which model, and add rate limits.
15. **Exports.** Excel with the SQL included, PowerPoint, and notebook files (`.ipynb` or `.py`).

### B. New features

**B1. Analysis methods that run as tested code.** These are the core of the data science upgrade. The model only picks the method and the columns, and your code calculates the answer. That means it also works in schema-only mode. Each method fits into the existing plan and result types as a new step and result kind.
- **Hypothesis tests that check their own assumptions:**
  - comparing groups (t-test, Mann-Whitney, ANOVA, Kruskal-Wallis)
  - comparing categories (chi-square, Fisher's exact test)
  - correlations with confidence intervals
  - effect sizes, corrections when running many tests, and sample-size (power) calculations

  Libraries: scipy, statsmodels, pingouin.
- **Regression** with checks on how well the model fits.
- **"Why did X change?"** Break a period-over-period change down by dimension, separating changes in mix from changes in rate.
- **Anomaly detection and customer segmentation** (clustering) with a profile of each group.
- **Product analytics:** cohorts and retention, funnels, survival analysis.
- **A/B test analysis:** sample-ratio mismatch checks, variance reduction (CUPED) and sequential testing. GrowthBook's approach is a good reference.
- **Cause-and-effect analysis**, clearly marked as advanced, using DoWhy with its built-in robustness checks.
- **Robustness check:** rerun a key conclusion with reasonable alternative choices and report whether it holds. This responds directly to the finding that AI analysts' conclusions vary so much between runs.

**B2. A locked-down Python/R sandbox for everything else.**
- Make the backend swappable: Docker for development, gVisor or a self-hosted E2B (microVM) setup for production. Use E2B's cloud only with explicit consent, because data leaves your servers.
- Block network access by default, cap CPU, memory and time, and mount data read-only.
- Save the code and package versions so results can be rerun.
- Don't use Pyodide as a server-side sandbox. Pydantic shut down its `mcp-run-python` project for exactly this reason: Python in Pyodide can run arbitrary JavaScript.
- Label code-based results so they're clearly distinct from results produced by the tested methods.

**B3. Full model lifecycle.**
- **"Predict this column" wizard:** detects the problem type, flags leaked information (ID-like columns, or fields only known after the outcome), splits data correctly for time-based problems, and shows a leaderboard of models.
- **Libraries:**
  - FLAML: lightweight and runs within a time limit
  - AutoGluon: the most accurate in AutoML benchmarks, but heavier
  - TabPFN: very strong on tables up to about 10,000 rows (published in Nature, 2025); check its license first
- **Evaluation:** ROC and precision-recall curves, calibration, confusion matrix, and choosing a decision threshold based on business costs.
- **Explanations:** SHAP, feature importance, what-if simulation, and fairness checks on the columns you already mark as sensitive.
- **Model versions:** tie each model to the dataset version it was trained on. Your fixed dataset versions make this traceability easy.
- **Deployment:** batch scoring of new data, a prediction API endpoint, and scheduled rescoring.
- **Monitoring:** drift detection with the Evidently library, performance tracking once real outcomes arrive, and retraining triggers.

**B4. Time series.**
- Detect date columns and their frequency automatically, fill gaps, and split series into trend and seasonality.
- Forecast with simple baseline methods from the statsforecast library, plus pretrained forecasting models that need no training (Amazon's Chronos-Bolt, Google's TimesFM).
- Always test forecasts on past data against a naive baseline, and show uncertainty ranges.

**B5. Data preparation and quality.**
- **Repeatable cleaning steps:** type fixes, date parsing, deduplication, filling gaps, reshaping, joins and calculated columns, all turned into SQL. Applying them creates a new draft version, so your existing review-and-confirm flow keeps a full history.
- **AI-suggested fixes** based on profile alerts, which the user previews and approves.
- **Validation rules checked on every new version:** not empty, unique, within range, allowed values, fresh enough.
- **Join suggestions** between tables, with checks that each join matches rows the way it should.

**B6. Metrics layer.**
- Define entities, dimensions, metrics, relationships and synonyms in YAML files. Make them compatible with the emerging Apache Ossie standard (formerly Open Semantic Interchange) and with dbt.
- The agent asks for a metric, and fixed code writes the SQL.
- Let data owners approve trusted tables and metrics, with an "approved data only" mode for business users.
- Keep a local index of actual values so that "NYC" maps to "New York".

**B7. Workspace.**
- **SQL editor** with a table browser, the existing SQL checker and saved queries.
- **Notebook view:** each agent run becomes editable, rerunnable cells, with automatic re-running of dependent cells (like marimo) and notebook export.
- **Branching:** start a new line of analysis from any step, like Microsoft's Data Formulator.
- **Dashboards and apps:** pinned charts, filters and scheduled refresh, with read-only share links.

**B8. Proactive insights.** Let users follow a metric. Check it daily for threshold breaches, anomalies, trend changes and top contributors, and send a digest by Slack or email, like Tableau Pulse.

**B9. Collaboration and governance.** Organizations with roles, sharing, comments on results, and approval workflows for metrics, approved queries and models. Add single sign-on (OIDC or SAML), audit logs, and cleanup of old draft files (your README notes this isn't built yet).

**B10. Integrations.**
- **MCP server:** make your datasets, approved metrics and analysis methods usable from Claude, Cursor or ChatGPT, as MotherDuck, Microsoft Fabric and Hex already do.
- **Public API and Python library.**
- **Connectors:** Snowflake, BigQuery, Databricks and SQL Server (already on your roadmap), cloud storage, and SaaS apps via tools like dlt or Airbyte.
- **Text columns:** classify, extract, sentiment and embeddings. DuckDB's `ai` extension provides these with network restrictions and usage tracking, but it must follow the dataset's privacy mode.
- **Maps and search:** DuckDB's `spatial`, `fts` (full-text search) and `vss` (vector search) extensions.

**B11. Quality engineering.**
- A test suite of known questions with expected answers for each dataset, run on every prompt or model change. It tracks accuracy, speed and cost, and can be seeded from public benchmarks (BIRD, Spider 2.0-lite, DABstep, DA-Code).
- Step-by-step tracing of every agent run.
- Cost tracking, and sending simple steps to cheaper models.
- Docker Compose, and PostgreSQL as the default app database for multi-user setups.

---

## 4. Target architecture

```text
question --> Context: schema cache | metrics layer | approved queries | dataset notes | findings | value index
                 |
                 v
      Agent loop: plan -> act -> observe -> check  (limits, cancel, clarifying questions, Quick/Deep)
                 |
   +-------------+--------------+-----------------+--------------+
 run SQL (checked)   analysis methods      Python sandbox    charts / reports
                     (stats, forecasting,
                      models, "why")
                 |
   Privacy filter: what the model may see in each mode --> LLM
                 |
   Checks: row counts before/after joins, missing values, number check, LLM reviewer
                 |
   Summary: every number linked to evidence, plus an Assumptions section
```

Start by adding the new analysis methods as new step types in today's plan format. They are useful immediately, and they become the agent's tools once the loop exists.

---

## 5. Prioritized roadmap

| Phase | Focus | Key items | Why this order |
|---|---|---|---|
| 0 | Foundations and quick wins | Truncation warnings, local model support in local-only mode, schema caching, fuller data profile with alerts, strict model output, more chart types, suggested follow-up questions, SQL editor, cost tracking, log of data sent to models, job queue, Docker Compose | Fixes correctness problems and makes the default mode useful; mostly small changes |
| 1 | Analyst agent | Loop, tools, privacy filter, clarifying questions, Quick/Deep modes, checks, evidence-linked summaries, better memory, test suite and tracing | Every later feature plugs in as a tool; the test suite catches quality drops |
| 2 | Data science toolkit | Tests, regression, "why" analysis, anomalies, segmentation, forecasting, A/B tests, robustness checks, Python sandbox, notebook view | Goes beyond SQL while keeping results repeatable and checkable |
| 3 | Data preparation and models | Cleaning steps with history, validation rules, AI fix suggestions, join suggestions, AutoML, evaluation, SHAP, model versions, scoring, drift monitoring | Clean data first, then models tied to dataset versions |
| 4 | Metrics layer and proactive insights | Metric definitions, checked reports for any metric, approved query library, approved data, value index, followed metrics, alerts and digests | The biggest SQL accuracy gain, and what business users need |
| 5 | Teams and integrations | Roles, single sign-on, sharing, comments and approvals, dashboards, MCP server, API, more connectors, text analysis, maps | Scales to teams and fits into tools people already use |

If your main users are business analysts rather than data scientists, move Phase 4 ahead of Phase 3.

## 6. Pitfalls to avoid

- **Don't let the model compute numbers.** The model chooses the method, tested code does the maths, and the summary points to the evidence. You already do this for the sales report; extend it everywhere.
- **Label how each result was produced:** approved metric, tested method, exploratory SQL, or free-form code, in that order of trust.
- **Sandboxes must block network access**, not just file access. Standard containers share the host's operating system kernel, and Pyodide can't contain untrusted code.
- **Privacy modes must cover every place data goes:** what the agent sees between steps, cloud sandboxes, text analysis functions and the MCP server, not only the prompt.
- **Build the test suite in Phase 1.** Without it, agent quality drops quietly.
- **Be careful with statistics:** report effect sizes and confidence intervals rather than just p-values, correct for running many tests, and avoid cause-and-effect language unless a proper causal method was used. Your summary prompt already forbids causal claims; keep that.

---

## Sources

**Products**
- [Julius tools](https://julius.ai/docs/get-started/tools)
- Hex: [Notebook Agent](https://learn.hex.tech/docs/explore-data/notebook-view/notebook-agent), [Threads](https://learn.hex.tech/docs/explore-data/threads)
- [Deepnote Agent](https://deepnote.com/docs/deepnote-agent)
- [Databricks Data Science Agent](https://www.databricks.com/blog/introducing-databricks-assistant-data-science-agent)
- [Colab Data Science Agent](https://docs.cloud.google.com/colab/docs/use-data-science-agent)
- [Snowflake Verified Query Repository](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-analyst/verified-query-repository)
- [ThoughtSpot Spotter deep mode](https://docs.thoughtspot.com/cloud/26.8.0.cl/spotter-research-mode)
- [Tableau Pulse insights](https://help.tableau.com/current/online/en-us/pulse_insights_platform_insight_types.htm)
- [Dataiku data preparation](https://www.dataiku.com/product/data-preparation)
- [DataRobot MLOps](https://docs.datarobot.com/11.0/en/docs/get-started/robot-to-robot/rr-mlops.html)
- [Microsoft Fabric data agent](https://learn.microsoft.com/en-us/fabric/data-science/semantic-model-best-practices)

**Research**
- [Survey of 45 data science agents](https://arxiv.org/abs/2510.04023)
- [DS-STAR](https://research.google/blog/ds-star-a-state-of-the-art-versatile-data-science-agent/)
- [Data Interpreter](https://arxiv.org/abs/2402.18679)
- [DABstep](https://arxiv.org/html/2506.23719)
- [DSBench](https://proceedings.iclr.cc/paper_files/paper/2025/hash/50e9ad960ae78b741a6b4fea533f2eaf-Abstract-Conference.html)
- [DA-Code](https://arxiv.org/html/2410.07331v2)
- [Spider 2.0](https://spider2-sql.github.io/)
- [SQL through a metrics layer](https://arxiv.org/html/2606.31041v1)
- [BLADE](https://blade-bench.github.io/)
- [Variation between AI analysts](https://www.arxiv.org/pdf/2602.18710)
- [P-Bench](https://export.arxiv.org/pdf/2608.07437)
- [MLE-bench](https://github.com/openai/mle-bench/)
- [TabPFN](https://www.nature.com/articles/s41586-024-08328-6)
- [Chronos-Bolt](https://aws.amazon.com/blogs/machine-learning/fast-and-accurate-zero-shot-forecasting-with-chronos-bolt-and-autogluon/)
- [TimesFM](https://github.com/google-research/timesfm)
- [AutoML benchmark](https://www.jmlr.org/papers/volume25/22-0493/22-0493.pdf)

**Tools**
- [ydata-profiling alerts](https://docs.profiling.ydata.ai/latest/getting-started/concepts/)
- [Evidently](https://github.com/evidentlyai/evidently)
- [MLflow 3](https://mlflow.org/releases/3/)
- [DoWhy](https://www.pywhy.org/dowhy/main/example_notebooks/tutorial-causalinference-machinelearning-using-dowhy-econml.html)
- [GrowthBook statistics](https://docs.growthbook.io/statistics/overview)
- [Apache Ossie (Open Semantic Interchange)](https://github.com/open-semantic-interchange/OSI)
- [Data Formulator](https://github.com/microsoft/data-formulator)
- [marimo](https://docs.marimo.io/)
- [E2B](https://e2b.dev/)
- [Sandbox comparison](https://blog.logrocket.com/comparing-ai-agent-sandbox-platforms-e2b-modal-daytona-and-more/)
- [mcp-run-python shutdown notice](https://github.com/pydantic/mcp-run-python)
- [DuckDB ai extension](https://duckdb.org/community_extensions/extensions/ai)
- [MotherDuck MCP server](https://motherduck.com/docs/sql-reference/mcp/)
- [Presidio for tables](https://github.com/microsoft/presidio/tree/main/presidio-structured)

**User surveys**
- [Databox](https://databox.com/research-reports/using-ai-you-dont-trust)
- [Alteryx](https://www.prnewswire.com/news-releases/65-of-analysts-say-ai-works-best-when-the-logic-is-managed-at-the-business-level-alteryx-research-finds-302776773.html)
- [insightsoftware](https://insightsoftware.com/blog/why-dont-data-leaders-trust-ai-and-other-insights-from-our-2026-ai-survey/)
- [Anaconda](https://www.anaconda.com/blog/state-of-data-science-and-ai-how-companies-are-moving-ahead)
- [Pecan](https://www.pecan.ai/blog/chatgpt-for-data-science-risks/)

---

The main decision that changes the order is who your primary user is: business analysts (metrics layer and proactive insights sooner) or data scientists (notebooks, sandbox and models sooner). Once you've decided, I can turn Phases 0 and 1 into a detailed implementation plan covering files, data models, API changes and tests, or save this report as `docs/ROADMAP.md`.