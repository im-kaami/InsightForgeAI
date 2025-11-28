# 🚀 InsightForge AI  
### Autonomous Corporate Analytics Agent Powered by LLM Planning + SQL Tools + Visual Intelligence

<p align="center">
  <img src="https://img.shields.io/badge/Category-Enterprise%20AI-blue?style=for-the-badge">
  <img src="https://img.shields.io/badge/Models-GPT--4o--mini%20%7C%20Gemini%202.0%20Flash-brightgreen?style=for-the-badge">
  <img src="https://img.shields.io/badge/Tools-SQL%20%7C%20Plots%20%7C%20Memory-orange?style=for-the-badge">
  <img src="https://img.shields.io/badge/Status-Competition%20Ready-success?style=for-the-badge">
</p>

---

# ✨ Overview

**InsightForge AI** is an intelligent **Enterprise Analytics Agent** that converts natural-language business questions into structured, actionable insights.

It uses:
- 🧠 GPT-4o-mini for planning  
- 🗄️ DuckDB SQL for fast querying  
- 📊 Matplotlib for visual analytics  
- 📝 Gemini Flash for business summaries  
- 🧱 Lightweight memory for storing insights  

This agent automatically generates:
✔ Multi-step analysis plans  
✔ SQL transformations  
✔ Data visualizations  
✔ Executive-style summaries  
✔ Full trace of reasoning artifacts  

---

# 📐 Architecture

### High-Level System Pipeline


                   ┌────────────────────────┐
                   │      User Prompt       │
                   └───────────┬────────────┘
                               │
                               ▼
                   ┌────────────────────────┐
                   │    GPT-4o-mini Planner │
                   │  (JSON Step Generator) │
                   └───────────┬────────────┘
                               │ Steps []
                               ▼
        ┌─────────────────────────────────────────────────┐
        │                   Agent Loop                    │
        │   Executes steps sequentially:                  │
        │   • SQL → DuckDB                                │
        │   • PLOT → Matplotlib                           │
        │   • NLP → Gemini Summarizer                     │
        └───────────────────┬─────────────────────────────┘
                            │
                            ▼
                ┌────────────────────────────┐
                │  Memory (SimpleMemory)     │
                │ Stores summaries + artifacts│
                └────────────┬──────────────┘
                             │
                             ▼
                ┌────────────────────────────┐
                │ Final Summary + Artifacts  │
                └────────────────────────────┘

flowchart TD
    A[User Prompt] --> B[GPT-4o-mini Planner<br/>JSON Plan Generator]

    B --> C[Agent Executor]

    C --> D[SQL Tool<br/>DuckDB]
    C --> E[Plot Tool<br/>Matplotlib]
    C --> F[NLP Tool<br/>Gemini Summary]

    D --> C
    E --> C
    F --> C

    C --> G[Memory Store]
    C --> H[Final Summary + Visualizations]

🔧 Components
1. Planner — planner.py

Uses GPT-4o-mini to generate a JSON plan of steps.

Ensures:

Valid SQL using only known dataset columns

Valid plot instructions

Optional summarization steps

Includes deterministic fallback if the LLM output fails.

2. Tools — tools.py

sql_tool() → DuckDB SQL queries on GLOBAL_DF

plot_tool() → Automated detection of appropriate dataframe

gemini_summarize() → Optional summarizer using Gemini

3. Agent — agent.py

Executes planner steps sequentially.

Automatically selects correct dataframes for plots.

Produces final summary through NLP step (Gemini or fallback).

Appends memory entries.

4. Memory — memory.py

Simple document store:

add(id, text, metadata)

query_recent(k) returns latest k reasoning episodes.


📈 Example Agent Output

Prompt:

"Show revenue trends over time and identify growth drivers."

Planner Output (sample):

[
  {"name":"rev_by_date","action":"sql",
   "args":{"query":"SELECT date, SUM(revenue) AS revenue FROM sales GROUP BY date ORDER BY date"}},

  {"name":"plot_revenue_trends","action":"plot",
   "args":{"kind":"line","x":"date","y":"revenue","title":"Revenue Trends"}},

  {"name":"rev_by_product","action":"sql",
   "args":{"query":"SELECT product, SUM(revenue) AS revenue FROM sales GROUP BY product ORDER BY revenue DESC"}},

  {"name":"plot_top_products","action":"plot",
   "args":{"kind":"bar","x":"product","y":"revenue","title":"Top Products"}} 
]

Outputs produced:

Line plot (revenue over time)

Bar chart (top-performing products)

Optional summary (Gemini-based)

Memory record storing the session

🛠️ How to Run (Local)
Install:
pip install -r requirements.txt

Set environment variables:
export OPENAI_API_KEY="sk-xxxxx"
export GEMINI_API_KEY="AIza-xxxxx"   # optional

Run the agent:
from agent import InsightForgeAgent
from tools import register_global_df
import pandas as pd

df = pd.read_csv("your_sales.csv")
register_global_df(df)

agent = InsightForgeAgent()
out = agent.run("Show revenue trends and top product performance")
print(out["summary"])

🧪 Testing

Included test utilities:

smoke_test.py confirms planner + SQL + plots + agent loop

pytest tests for tools (optional)

Notebook can be executed via:jupyter nbconvert --execute submission.ipynb

