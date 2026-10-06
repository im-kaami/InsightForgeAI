# InsightForge: hands-on testing guide

This guide walks through every feature built so far (Phases 0 to 5f), in the order you would use
them. Each step says what to do and what you should see. Tick the box when it works; when it does
not, note the step number and what happened (see "Reporting a problem" at the end).

Plan about a day for everything. Parts 1 to 4 are the core; do them first.

---

## 0. Before you start

### 0.1 Start the app

Both services are normally running already. Check:

- http://localhost:3000 opens the InsightForge sign-in page.
- http://localhost:3000/api/health shows `"status":"ok"`, `"provider":"openai"` (Groq) and
  `"local_model":"qwen3:4b"`.

If not, start them as described in `AGENTS.md` ("Run the app"), or ask the agent to restart them.

### 0.2 Which AI answers your questions

Each dataset has a **privacy mode** (dataset page, "Data sharing and privacy" card). It decides which
AI is used:

| Mode | AI used | Notes |
|---|---|---|
| Local only (default) | `qwen3:4b` on this laptop, through Ollama | Ollama must be running. Slower (30 to 70 s per question), nothing leaves the laptop |
| Schema only | Groq cloud model | Sees table and column names and your question, never data values |
| Full | Groq cloud model | Sees sample values and results; fastest and most capable |

Approved metrics, checked reports, dashboards refresh, followed metrics, drift and SHAP never use an
AI at all, in any mode.

### 0.3 Test data (already in the repository)

| File | What it is |
|---|---|
| `backend/evals/data/orders.csv` | 400 shop orders in 2025: region, category, amount, status (completed, refunded, cancelled) |
| `backend/evals/data/customers.csv` | 60 customers: segment (Consumer, Business, ...), country |
| `backend/tests/fixtures/hr.csv` | 60 employees: department, job title, hire date, salary, location, performance score |
| `backend/evals/data/experiment.csv` | An A/B test: variant, pre-experiment revenue, converted, revenue |
| `backend/evals/data/subscribers.csv` | Subscribers with plan, fee, tenure, tickets and `churned` (for predictions) |
| `backend/tests/fixtures/workbook.xlsx`, `sales.parquet`, `sales.json` | Other file formats |

Useful to know: in `orders.csv`, completed revenue in the West is the largest region, and the latest
completed order is on 2025-12-29.

### 0.4 Accounts

Create two accounts so you can test teams later, for example `you@example.com` and
`colleague@example.com` (use a private/incognito window for the second one). Passwords need 8+
characters.

---

## Part 1. Accounts and data

### 1.1 Register, sign in, sign out
- [ ] On http://localhost:3000/register, create `you@example.com`. You land on **Datasets**.
- [ ] Click **Log out** (bottom of the sidebar). You are back on the sign-in page.
- [ ] Sign in again. A wrong password shows "Invalid email or password".
- [ ] Registering the same email twice shows an error.

### 1.2 Upload files and review the import
- [ ] **Add data** → **Files** → choose `orders.csv` and `customers.csv` together → **Preview import**.
- [ ] The review shows both tables, inferred column types, a preview and quality counts.
- [ ] Tick **I reviewed the import preview** → **Confirm import**. The dataset appears in the list.
- [ ] Open it. The page shows the tables, the **Data quality** health check, and the cards described
  below.

### 1.3 Other sources
- [ ] **Add data** → **Files** → `workbook.xlsx`: each sheet becomes a table.
- [ ] Upload `sales.parquet` and `sales.json`: both import.
- [ ] **From URL**: paste a public CSV link (any public CSV on GitHub, "Raw" link) → it imports.
- [ ] **Google Sheets**: paste a public sheet's share link → it imports (the sheet must be shared as
  "Anyone with the link").
- [ ] Private or internal addresses (for example `http://localhost:...` or `192.168...`) are refused.

### 1.4 New versions
- [ ] On the shop dataset, **Upload new version** with an edited `orders.csv` (for example change one
  amount). A draft is shown for review; after **Confirm import** it becomes the current version, and
  the old version is kept.

### 1.5 Data quality
- [ ] The **Data quality** card lists columns with missing, distinct and repeated values, and
  findings. **Update health check** refreshes it.
- [ ] Columns with sensitive-looking names (for example email or phone) show "Hidden (sensitive)".

### 1.6 SQL editor (new in Phase 0c)
- [ ] On a dataset page, **SQL editor** opens a page with the tables and columns on the left and a
  query box prefilled with `SELECT * FROM "<table>" LIMIT 100`. **Run** (or Ctrl+Enter) shows the
  rows and a line such as "Showing 100 of 100 rows · 12 ms". No AI is involved and nothing is saved.
- [ ] Clicking a table name inserts its quoted name where the cursor is.
- [ ] Write your own query, for example
  `SELECT region, SUM(amount) AS revenue FROM orders GROUP BY region ORDER BY revenue DESC`.
- [ ] `DELETE FROM orders` (or `DROP TABLE orders`) is refused with a red message; the data is
  unchanged.
- [ ] **Download CSV** saves the result (up to 10,000 rows).

---

## Part 2. Asking questions

Use the shop dataset. Start with **Full** privacy for speed (dataset page → Data sharing and privacy →
Full → tick the box → save), then repeat a few questions in Local and Schema only.

### 2.1 A first question
- [ ] On the dataset page, **Start analysis**, then ask "What is the total amount of completed
  orders by region?".
- [ ] You see live steps, then a result table, a chart and a summary.
- [ ] Open **Assumptions**: the tables read, the filter on status, the grouping.
- [ ] Open **Run trace**: the model calls, the query and the number check, with timings.
- [ ] Numbers in the summary are checked: matched numbers link to evidence; an unmatched number would
  mark the answer **Needs review**.
- [ ] **Download report** → Markdown, HTML, PDF and **Notebook (.ipynb)** each download.
- [ ] Under the table, **Download CSV** works.

### 2.2 Charts
- [ ] Above a bar or line chart, **Show as** switches between bar, line, area and points without a
  new AI call.

### 2.3 Follow-up questions (memory)
- [ ] In the same session ask "And only for Electronics?". The answer builds on the previous
  question.

### 2.4 Clarifying questions
- [ ] Ask "Who are our best customers?". Instead of guessing, it asks "by total amount or by number
  of orders?" with buttons. Click one: a new answer starts with your choice.

### 2.5 Quick and Deep modes
- [ ] Under the chat box choose **Deep**, ask "Which category had the most refunds?". The answer shows
  a **Deep · N rounds** badge and the review decisions.

### 2.6 Checks on results
- [ ] Ask "How many orders are there in region remote?" (no such region). You get a warning that the
  filter matched no rows, with a similar-value suggestion if there is one.

### 2.7 Value index (loose wording)
- [ ] Ask "How many orders came from the west?". It filters on `region = 'West'`.
- [ ] Ask "Total amount of refunded electronics orders". It uses `status = 'refunded'` and
  `category = 'Electronics'`.
- [ ] In **Assumptions** you see lines like `"west" -> orders.region = 'West'`.

### 2.8 Privacy modes
- [ ] Switch the dataset to **Schema only** and ask a question. In **Run trace**, the model call says
  it saw "table and column names, types and the question only".
- [ ] Switch to **Local only** (Ollama running) and ask a question: the trace shows `qwen3:4b`; it is
  slower.
- [ ] Stop Ollama, ask in Local mode: you still get a basic answer from the offline planner.

### 2.9 Notes for the AI
- [ ] In **Notes for the AI**, write "Revenue and sales mean completed orders only" → **Save notes**.
- [ ] Ask "What is our total revenue?". It counts completed orders only.

### 2.10 History and sessions
- [ ] **History** lists your sessions; opening one shows its answers.

### 2.11 Schedules
- [ ] **Schedules** → **New schedule**: choose the shop dataset, a question, preset "Daily 09:00" →
  create. **Run now** runs it at once and the answer appears in its session.

---

## Part 3. Tested methods (statistics, forecasts, predictions)

Use Full privacy for these (they work in Local too, just slower).

### 3.1 Statistical tests (HR data: upload `hr.csv` first)
- [ ] "Does salary really differ between departments, or could it be chance?" → a **Tested method**
  card: the test used (for example ANOVA), the p-value, an effect size, assumption checks and
  cautions.
- [ ] "Is salary significantly correlated with performance score?" → a correlation with a 95%
  interval.
- [ ] "How much does performance score affect salary, controlling for department?" → a regression.
- [ ] Each result shows a **robustness check**: whether the conclusion holds under alternatives.

### 3.2 Why did it change? (shop data)
- [ ] "Why did total order amount drop from May to June 2025?" → contributions by segment that add up
  to the change, with mix and rate effects.

### 3.3 Forecasts and unusual values
- [ ] "Forecast the total order amount per month for the next 3 months" → history, forecast and a
  range on a chart, with the model chosen by backtest.
- [ ] "Were there any unusual weeks in total order amount?" → flagged weeks on a chart.

### 3.4 Customer groups
- [ ] "Segment our customers by their total order amount and number of orders" → groups with sizes
  and plain-language profiles.

### 3.5 A/B test (upload `experiment.csv`)
- [ ] "Did the new checkout variant significantly change the conversion rate?" → difference,
  interval, lift and a sample-ratio check.
- [ ] "Did the new variant raise revenue per user? Use pre-experiment revenue to reduce noise." →
  CUPED is used.

### 3.6 Predict a column (upload `subscribers.csv`)
- [ ] "Which factors predict whether a subscriber churns? Build a model." → a leaderboard of models
  against a baseline, the most important factors, and a warning naming `cancellation_reason` as
  possible leakage.

### 3.7 Saved models, drift and explanations
- [ ] On that prediction card, **Save model** (give it a name).
- [ ] On the dataset page, **Saved models** → pick a version → **Score and check drift**: predictions,
  a preview, a CSV download, and drift per input.
- [ ] Choose a row → **Explain this row**: each input's contribution.
- [ ] **Monitoring**: set a daily schedule, then **Check now**: the check appears under **Recent
  checks**.

### 3.8 Python sandbox (optional; needs Docker Desktop running)
- [ ] "Using Python, compute the median salary for each department." → a **Free-form code
  (sandboxed)** card with the code and its output. With Docker stopped, the step says the sandbox is
  off.

---

## Part 4. Preparing and governing data

### 4.1 Cleaning recipes
- [ ] **Cleaning recipe** → **Add a step**, for example trim spaces in a text column, or remove
  duplicate rows → save → **Apply to current data as a draft**. The review shows rows before and
  after each step. Confirm it.
- [ ] **Suggest from health check** proposes steps; nothing changes until you add and save.

### 4.2 Validation rules
- [ ] **Validation rules** → add "amount never missing" and "status uses allowed values: completed,
  refunded, cancelled" → save. Each shows passed or failed with failing examples.
- [ ] Make a rule fail and mark it **Blocking**: a verified report or checked metric report is then
  blocked.

### 4.3 Table relationships
- [ ] On the shop dataset, **Table relationships** → **Suggest joins** → `orders.customer_id ->
  customers.customer_id` with its match rate → add it → **Save relationships**.
- [ ] Ask "What is the total amount of completed orders for each customer segment?": it joins the two
  tables correctly.

### 4.4 Approved metrics
- [ ] **Metrics** → **Add a metric**: name `revenue`, label Revenue, table orders, Sum of a column,
  column amount, date column order_date, groupings region and customers.segment, condition status
  equals completed, other names "sales", unit GBP. Tick **I checked this definition; the AI may use
  it** → **Add to list** → **Save metrics**.
- [ ] **Preview** shows the number and the SQL.
- [ ] Ask "What are sales by region?": the table has an **Approved metric: Revenue** badge.

### 4.5 Checked metric reports
- [ ] On the metric, **Checked report**: first day 2025-07-01, last day 2025-12-31, split by region →
  **Run checked report**. You see current against previous period per region, change and change %,
  a chart, a summary citing evidence, and "Calculation checks passed" (or Needs review with reasons).
  It says no AI model was used.

### 4.6 Followed metrics and alerts
- [ ] On the metric, **Follow**: window 30 days, alert at 5% → **Start following** → **Check now**. A
  sentence such as "Revenue fell 12% in the 30 days to 2025-12-29 ..." appears.
- [ ] If the change is at or above the threshold, **Metric alerts** appears in the sidebar; **Dismiss**
  clears it. **Open report** opens the checked report.
- [ ] Upload a new version of the data: a follow with "Also check when new data is confirmed" checks
  by itself.

### 4.7 Approved questions and "Approved data only"
- [ ] **Approved questions** → **Add a question**: "What is completed revenue by region?" with
  `SELECT region, SUM(amount) AS revenue FROM orders WHERE status = 'completed' GROUP BY region` →
  **Save approved questions**.
- [ ] Ask "Completed revenue for each region": the answer has an **Approved query** badge ("matched by
  code").
- [ ] Under any other answer's table, **Save as approved question** adds it to the list.
- [ ] Tick **Approved data only** and save. Ask "How many customers are there?": it is refused, with a
  list of what you can ask.
- [ ] Untick it again before continuing.

### 4.8 Verified sales report
- [ ] On the shop dataset, **Approved report setup**: sales table orders, row key order_id, reporting
  date order_date, gross sales amount amount, currency GBP, confirm single currency and no refunds →
  save → **Run saved report** for a period. You get net sales, orders, margin (if cost is mapped),
  the comparison with the previous period, checks and evidence, with no AI.

---

## Part 5. Sharing and teams (new in Phase 5)

### 5.1 Dashboards
- [ ] Under an answer's table or chart, **Pin to dashboard** → **New dashboard...** → name it.
- [ ] **Dashboards** → open it: the pinned tile shows where it came from and how it was produced.
- [ ] **Add a live tile**: dataset shop → "Metric report: Revenue", last 30 days → **Add tile**; then
  "Question: What is completed revenue by region?" → **Add tile**.
- [ ] **Refresh all** re-runs the live tiles (pinned ones stay). **Up**/**Down** reorder;
  **Remove** deletes a tile.
- [ ] Delete the approved question, then **Refresh** its tile: it shows why it failed and keeps the
  last good result.

### 5.2 Share links
- [ ] On the dashboard, **Share** → choose 7 days → tick the consent box → **Create link** → copy it.
- [ ] Open the link in a private window (not signed in): the dashboard is shown read only, with no
  SQL and no buttons.
- [ ] On an answer, **Share** works the same way.
- [ ] **Sharing** in the sidebar lists your links with views; **Revoke** one, then reload the private
  window: "This link does not exist, has expired or was revoked".

### 5.3 Workspaces (teams and roles)
- [ ] **Workspaces** → **New workspace** "Finance".
- [ ] **Invite someone**: `colleague@example.com`, role Viewer → **Create invitation** → copy the link.
- [ ] In a private window, register `colleague@example.com`, open the invitation link → **Accept
  invitation**. Their Workspaces page shows Finance, "Your role: viewer".
- [ ] As you, on the shop dataset, **Share with workspace** → Finance.
- [ ] As the colleague: Datasets lists the shop dataset; its page shows "Shared with you: view only";
  they can ask questions (in their own session, which you cannot see); saving a metric fails with "ask
  an editor or the owner"; there is no privacy or delete control.
- [ ] As you, change their role to **editor**: they can now save metrics and notes, but still cannot
  change privacy or delete.
- [ ] Share a dashboard with **Show to workspace**: the colleague sees it, view only.
- [ ] Remove the colleague (or have them **Leave**): the dataset and dashboard disappear for them.
- [ ] An invitation opened by a different email is refused; used or revoked invitations no longer
  work.

### 5.4 API tokens
- [ ] **API tokens** → name "test", **Read only**, 30 days → **Create token** → copy it.
- [ ] In a terminal: `curl -H "Authorization: Bearer <token>" http://localhost:8000/api/datasets`
  lists your datasets.
- [ ] `curl -X DELETE -H "Authorization: Bearer <token>" http://localhost:8000/api/datasets/<id>`
  returns 403.
- [ ] Create a **Read and ask questions** token and ask a question with the commands in the README
  ("API access tokens").
- [ ] **Revoke** the token: the same curl now returns 401.

### 5.5 MCP server (Claude Desktop or Cursor)
- [ ] Install once: `backend/.venv/Scripts/python.exe -m pip install -e "backend[mcp]"` (already done
  on this laptop).
- [ ] Create a **Read and ask questions** token and add the server to Claude Desktop or Cursor as shown
  in the README ("MCP server").
- [ ] In the assistant: "List my InsightForge datasets" → it calls `list_datasets`.
- [ ] With the shop dataset on **Full**: "Ask InsightForge what revenue by region is" → an answer with
  its trust label.
- [ ] Switch the dataset to **Schema only**: `describe_dataset` shows names and types but no values,
  and asking is refused with an explanation. On **Local only** it shows the name only.

### 5.6 Single sign-on (needs an identity-provider account)
This needs a provider such as Google. Steps for Google:
- [ ] In Google Cloud Console → APIs & Services → Credentials → **Create OAuth client ID** (Web
  application). Authorized redirect URI: `http://localhost:3000/api/auth/oidc/callback`.
- [ ] Add to `backend/.env`: `OIDC_ISSUER=https://accounts.google.com`, `OIDC_CLIENT_ID=...`,
  `OIDC_CLIENT_SECRET=...`, `OIDC_REDIRECT_URI=http://localhost:3000/api/auth/oidc/callback`,
  `OIDC_PROVIDER_NAME=Google`. Restart the backend.
- [ ] The sign-in page shows **Sign in with Google**; signing in lands you on Datasets with your
  Google email.
- [ ] Optional: `OIDC_ALLOWED_DOMAINS=["yourcompany.com"]` refuses other domains.

This is the one feature tested only against a simulated provider, so please report exactly what
happens.

---

## Part 6. Command line and evaluation (optional, technical)

- [ ] `backend/.venv/Scripts/insightforge.exe schema backend/evals/data/orders.csv` prints the schema.
- [ ] `backend/.venv/Scripts/insightforge.exe ask backend/evals/data/orders.csv "Total amount by region" --fake`
  prints a plan, results and a summary.
- [ ] The AI evaluation (`insightforge eval --model local`, about 30 minutes) last scored 60 of 64.

---

## Known limits (not bugs)

- Alerts are only shown in the app; nothing is emailed yet.
- Saved models, scheduled analyses and verified sales reports cannot be shared with a workspace yet.
- Invitations are links you send yourself; InsightForge does not send email.
- Snowflake, BigQuery and SQL Server connectors are not built (Phase 5g).
- Single sign-on has not yet been tried with a real provider.
- Schedules and followed-metric checks run only while the backend is running.
- The local model (`qwen3:4b`) is small: it answers about 9 in 10 evaluation questions correctly.
  Wrong answers usually show up as a "Needs review" badge or an odd plan; check the SQL and
  Assumptions.

## Reporting a problem

For each problem, note:

1. The step number from this guide (for example 4.5).
2. What you did, the dataset and its privacy mode.
3. What you expected and what happened (copy the error message or take a screenshot).
4. For answers: the question, and the **Run trace** and **Assumptions** (or download the Markdown
   report).

Give the agent the list; it can reproduce each one, fix it with a test, and update this guide.
