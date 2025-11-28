# planner.py
"""
Planner that uses OpenAI (gpt-4o-mini recommended).
This module is robust to both new and old openai SDKs.
"""

import os, json, re
from typing import List, Dict, Any

# openai client compat
openai_new_sdk = False
client = None
try:
    from openai import OpenAI
    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    openai_new_sdk = True
except Exception:
    import openai
    openai.api_key = os.environ.get("OPENAI_API_KEY")
    client = openai

OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

COLUMNS = ["date","product","region","channel","units","price","revenue","customer_id"]

PLANNER_PROMPT = f"""
You are a data-analysis planner for a dataset with EXACT columns:
{COLUMNS}

Rules:
- Use only these column names.
- When aggregating revenue, alias as: SUM(revenue) AS revenue
- Output ONLY valid JSON: an array of step objects with keys: name, action (sql|plot|nlp|code), args (object).
- For plots, prefer to include "data_source": "<previous_sql_step_name>" when appropriate.

Example:
[
  {{ "name":"rev_by_date","action":"sql","args":{{"query":"SELECT date, SUM(revenue) AS revenue FROM sales GROUP BY date ORDER BY date"}}}},
  {{ "name":"plot_rev","action":"plot","args":{{"kind":"line","x":"date","y":"revenue","title":"Revenue Over Time"}}}},
  {{ "name":"summary","action":"nlp","args":{{"text":"Summarize findings and recommend actions"}}}}
]

Now produce a JSON array of steps for this user goal:
<<GOAL>>
"""

def extract_json(text: str):
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"(\[[\s\S]*\])", text)
        if m:
            try:
                return json.loads(m.group(1))
            except Exception:
                try:
                    return json.loads(m.group(1).replace("'", '"'))
                except Exception:
                    return None
    return None

def plan_with_llm(goal: str) -> List[Dict[str,Any]]:
    prompt = PLANNER_PROMPT.replace("<<GOAL>>", goal)
    try:
        if openai_new_sdk:
            # new SDK
            res = client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[{"role":"user","content":prompt}],
            )
            # new SDK returns structure with choices[0].message.content
            text = res.choices[0].message.content
        else:
            # old SDK
            res = client.ChatCompletion.create(model=OPENAI_MODEL, messages=[{"role":"user","content":prompt}])
            text = res.choices[0].message["content"]
        parsed = extract_json(text)
        if isinstance(parsed, list):
            # validate and sanitize
            sanitized = []
            forbidden = ["product_name","product_id","sales","total_sales","amount","total_revenue","recent_revenue"]
            for step in parsed:
                if not all(k in step for k in ("name","action","args")):
                    continue
                if step["action"] == "sql":
                    q = step["args"].get("query","").lower()
                    if any(bad in q for bad in forbidden):
                        # reject -> use fallback
                        return deterministic_plan(goal)
                sanitized.append(step)
            return sanitized if sanitized else deterministic_plan(goal)
        else:
            return deterministic_plan(goal)
    except Exception:
        return deterministic_plan(goal)

def deterministic_plan(goal: str) -> List[Dict[str,Any]]:
    # minimal deterministic fallback
    goal_l = goal.lower()
    plan = []
    if "trend" in goal_l or "over time" in goal_l or "growth" in goal_l:
        plan.append({"name":"rev_by_date","action":"sql","args":{"query":"SELECT date, SUM(revenue) AS revenue FROM sales GROUP BY date ORDER BY date"}})
        plan.append({"name":"plot_revenue_trends","action":"plot","args":{"kind":"line","x":"date","y":"revenue","title":"Revenue Trends Over Time","data_source":"rev_by_date"}})
    if "product" in goal_l or "top products" in goal_l:
        plan.append({"name":"rev_by_product","action":"sql","args":{"query":"SELECT product, SUM(revenue) AS revenue FROM sales GROUP BY product ORDER BY revenue DESC"}})
        plan.append({"name":"plot_top_products","action":"plot","args":{"kind":"bar","x":"product","y":"revenue","title":"Top Products by Revenue","data_source":"rev_by_product"}})
    if "region" in goal_l or "region" in goal_l:
        plan.append({"name":"rev_by_region","action":"sql","args":{"query":"SELECT region, SUM(revenue) AS revenue FROM sales GROUP BY region ORDER BY revenue DESC"}})
        plan.append({"name":"plot_revenue_by_region","action":"plot","args":{"kind":"bar","x":"region","y":"revenue","title":"Revenue by Region","data_source":"rev_by_region"}})
    if not plan:
        plan = [
            {"name":"top_products","action":"sql","args":{"query":"SELECT product, SUM(revenue) AS revenue FROM sales GROUP BY product ORDER BY revenue DESC LIMIT 5"}},
            {"name":"by_region","action":"sql","args":{"query":"SELECT region, SUM(revenue) AS revenue FROM sales GROUP BY region ORDER BY revenue DESC"}}
        ]
    return plan
