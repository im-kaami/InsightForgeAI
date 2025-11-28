# agent.py
"""
InsightForge AI - Agent entrypoint
"""

from typing import List, Dict, Any
import time

from planner import plan_with_llm
from tools import sql_tool, plot_tool, gemini_summarize
from memory import SimpleMemory

class InsightForgeAgent:
    def __init__(self, memory: SimpleMemory = None, planner_fn = None):
        self.memory = memory or SimpleMemory()
        self.planner = planner_fn or plan_with_llm

    def run(self, user_goal: str) -> Dict[str, Any]:
        plan = self.planner(user_goal)
        artifacts = []
        df_history = {}
        last_df = None

        # Execute steps
        for step in plan:
            name = step.get("name")
            action = step.get("action")
            args = step.get("args", {}) or {}

            if action == "sql":
                q = args.get("query")
                df = sql_tool(q)
                df_history[name] = df
                last_df = df
                artifacts.append({"name": name, "type": "table", "data": df})

            elif action == "plot":
                x = args.get("x")
                y = args.get("y")
                # choose df: explicit data_source > last_df > any df containing x > global fallback
                data_source = args.get("data_source")
                plot_df = None
                if data_source and data_source in df_history:
                    plot_df = df_history[data_source]
                elif last_df is not None:
                    plot_df = last_df
                    if x and x not in plot_df.columns:
                        # find a df with x
                        for d in df_history.values():
                            if x in d.columns:
                                plot_df = d
                                break
                else:
                    # fallback to global dataset in tools.py
                    from tools import GLOBAL_DF
                    plot_df = GLOBAL_DF

                plot_tool(kind=args.get("kind","line"), df=plot_df, x=x, y=y, title=args.get("title"))
                artifacts.append({"name": name, "type": "plot", "data": "(plot generated)"})

            elif action == "nlp":
                text = args.get("text","")
                # build context from prior artifacts
                summary_ctx = text + "\n\nArtifacts:\n" + ", ".join([a["name"] for a in artifacts])
                summary = gemini_summarize(summary_ctx)
                artifacts.append({"name": name, "type": "text", "data": summary})

            else:
                artifacts.append({"name": name, "type": "note", "data": f"Unknown action {action}"})

        # Final summary: prefer last text artifact, else synthesize
        text_artifacts = [a for a in artifacts if a["type"] == "text"]
        if text_artifacts:
            final_summary = text_artifacts[-1]["data"]
        else:
            final_summary = gemini_summarize("Summary of artifacts:\n" + ", ".join([a["name"] for a in artifacts]))

        # save to memory with metadata
        self.memory.add(f"run_{int(time.time())}", final_summary, metadata={"goal": user_goal, "artifacts": [a["name"] for a in artifacts]})
        return {"goal": user_goal, "plan": plan, "artifacts": artifacts, "summary": final_summary}
