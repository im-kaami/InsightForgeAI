InsightForge AI — Corporate Insights Agent
Data-Driven Enterprise Analytics Powered by GPT-4o-Mini, Gemini, DuckDB, and Intelligent Tooling
<img src="https://img.shields.io/badge/Category-Enterprise%20Agent-blue"> <img src="https://img.shields.io/badge/LLMs-GPT--4o--mini%20%7C%20Gemini%202.0%20Flash-brightgreen"> <img src="https://img.shields.io/badge/Tools-SQL%20%7C%20Plotting%20%7C%20Memory-orange">

🧠 Overview

InsightForge AI is an enterprise-grade agent purpose-built for fast, flexible, and explainable analysis of corporate sales data.
It autonomously:

Generates multi-step analytical plans

Executes SQL queries using DuckDB on an in-memory dataset

Produces clean plots (revenue trends, top products, best regions, etc.)

Summarizes insights using Gemini 2.0 Flash

Stores summaries and metadata in agent memory for retrieval

This system combines:
✔ LLM reasoning (GPT-4o-mini planner)
✔ Deterministic analytics tools (SQL + Matplotlib)
✔ Enterprise-friendly architecture (modular, testable, scalable)

All components are delivered as modular Python modules plus a Kaggle-ready notebook.

🏗️ Architecture

Below is the high-level architecture of InsightForge AI.

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

