# 🚀 InsightForge AI  
### Autonomous Corporate Analytics Agent Powered by LLM Planning + SQL Tools + Visual Intelligence

<p align="center">
  <img src="https://img.shields.io/badge/Category-Enterprise%20AI-blue?style=for-the-badge">
  <img src="https://img.shields.io/badge/Models-GPT--4o--mini%20%7C%20Gemini%202.0%20Flash-brightgreen?style=for-the-badge">
  <img src="https://img.shields.io/badge/Tools-SQL%20%7C%20Plots%20%7C%20Memory-orange?style=for-the-badge">
  <img src="https://img.shields.io/badge/Status-Competition%20Ready-success?style=for-the-badge">
</p>
<p align="center">
  <img src="docs/images/InsightForge Logo.png" width="175" height="175">
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


![Diagram](https://github.com/im-kaami/InsightForgeAI/blob/main/docs/images/Agent%20Architecture.png)

    
### Activity Diagram

![Diagram](https://github.com/im-kaami/InsightForgeAI/blob/main/docs/images/Activity%20Diagram.png)

# 🔧 Components
## 🧠 Planner — planner.py

* Uses GPT-4o-mini to generate structured JSON plans
* Enforces valid column names
* Includes deterministic fallback
* Ensures zero hallucinations

## 🛠 Tools — tools.py

#### SQL Tool:
- Runs DuckDB SQL on the registered dataframe.

#### Plot Tool:
- Supports line & bar charts with automatic fallback mapping.

#### Gemini Summarizer:
- Optional NLP summary using Gemini 2.0 Flash.


## 🤖 Agent Controller — agent.py

#### Executes each planner step:
- SQL execution
- Plot generation
- Summaries
- Artifact collection
- Memory writing


## 🧱 Memory — memory.py

#### Simple, clean memory for:
- Storing summaries
- Query metadata
- Retrieving context


## 📈 Example

#### Input Prompt:

  "Show revenue trends over time and identify growth drivers."

#### Planner Output:
```json
[
  {
    "name": "rev_by_date",
    "action": "sql",
    "args": {
      "query": "SELECT date, SUM(revenue) AS revenue FROM sales GROUP BY date ORDER BY date"
    }
  },
  {
    "name": "plot_revenue_trends",
    "action": "plot",
    "args": {
      "kind": "line",
      "x": "date",
      "y": "revenue",
      "title": "Revenue Trends"
    }
  }
]

```

#### Final Output:
- 📊 Revenue trend line chart

![Diagram](https://github.com/im-kaami/InsightForgeAI/blob/main/docs/images/Revenue%20Trends%20Over%20Time.png)

- 🔎 Product & region breakdowns

![Diagram](https://github.com/im-kaami/InsightForgeAI/blob/main/docs/images/Revenue%20Trends%20by%20Regions.png)

![Diagram](https://github.com/im-kaami/InsightForgeAI/blob/main/docs/images/Revenue%20Trends%20by%20Channels.png)

- 📝 Executive summary via Gemini


# 🛠️ How to Run
### Install:
```nginx
pip install -r requirements.txt
```
### Set API Keys:
```arduino
export OPENAI_API_KEY="sk-your-key"
export GEMINI_API_KEY="AIza-your-key"
```
### Execute:
```python
from agent import InsightForgeAgent
from tools import register_global_df
import pandas as pd

df = pd.read_csv("data.csv")
register_global_df(df)

agent = InsightForgeAgent()
res = agent.run("Show revenue trends and top products")
print(res["summary"])
```

### Execute Notebook:
```css
jupyter nbconvert --execute submission.ipynb
```

# 📦 Repository Structure
```txt


insightforge-agent/
│
├── agent.py
├── planner.py
├── tools.py
├── memory.py
├── submission.ipynb
├── README.md
└── requirements.txt
```


