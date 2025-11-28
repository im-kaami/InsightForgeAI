# tools.py
import os
import pandas as pd
import duckdb
import matplotlib.pyplot as plt
import warnings
import numpy as np

# GLOBAL_DF is expected to be loaded externally or can be created by the notebook
GLOBAL_DF = None

def register_global_df(df: pd.DataFrame):
    global GLOBAL_DF
    GLOBAL_DF = df.copy()

def sql_tool(query: str) -> pd.DataFrame:
    if GLOBAL_DF is None:
        raise RuntimeError("GLOBAL_DF is not registered. Call register_global_df(df) first.")
    con = duckdb.connect()
    con.register("sales", GLOBAL_DF)
    try:
        df = con.execute(query).fetchdf()
    except Exception as e:
        con.close()
        raise RuntimeError(f"SQL Error: {e}\nQuery:\n{query}")
    con.close()
    return df

def plot_tool(kind: str, df: pd.DataFrame, x=None, y=None, title=None):
    if df is None or df.empty:
        print("Plot skipped: empty dataframe")
        return
    plt.figure(figsize=(10,4))
    # fallback mapping
    if y not in df.columns:
        if "revenue" in df.columns:
            warnings.warn(f"y={y} missing, using 'revenue'")
            y = "revenue"
        else:
            print(df.head()); return
    if kind == "line":
        if x not in df.columns and "date" in df.columns:
            x = "date"
        plt.plot(pd.to_datetime(df[x]), df[y])
        plt.xlabel(x); plt.ylabel(y)
    elif kind == "bar":
        if x not in df.columns:
            # attempt groupby index
            try:
                agg = df.groupby(df.index)[y].sum().sort_values(ascending=False)
                agg.plot.bar()
            except Exception:
                print(df.head()); return
        else:
            agg = df.groupby(x)[y].sum().sort_values(ascending=False)
            agg.plot.bar()
            plt.xlabel(x); plt.ylabel(y)
    plt.title(title or "")
    plt.tight_layout()
    plt.show()

# Gemini summarizer (optional)
try:
    import google.generativeai as genai
    GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
    if GEMINI_KEY:
        genai.configure(api_key=GEMINI_KEY)
except Exception:
    genai = None

def gemini_summarize(text: str) -> str:
    if genai is None:
        # fallback: simple heuristic summary
        return text[:400] + ("\n\n(Truncated fallback summary.)" if len(text) > 400 else "")
    try:
        model = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
        gm = genai.GenerativeModel(model)
        resp = gm.generate_content("Summarize succinctly:\n\n" + text)
        if hasattr(resp, "text") and resp.text:
            return resp.text.strip()
        return str(resp)[:800]
    except Exception:
        return text[:400] + ("\n\n(Truncated fallback)")
