import contextlib
import io
import json
import traceback

import numpy as np
import pandas as pd

MAX_ROWS = 10_000
MAX_STDOUT = 20_000


def _table(result: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(result.index, pd.RangeIndex):
        result = result.reset_index()
    result.columns = [str(column) for column in result.columns]
    return result


def main() -> None:
    frame = pd.read_parquet("/work/input.parquet")
    with open("/work/code.py", encoding="utf-8") as handle:
        code = handle.read()
    output = io.StringIO()
    scope = {"df": frame, "pd": pd, "np": np, "__name__": "__analysis__"}
    status: dict = {"ok": True}
    try:
        with contextlib.redirect_stdout(output):
            exec(compile(code, "analysis.py", "exec"), scope)
        result = scope.get("result")
        if isinstance(result, pd.Series):
            result = result.to_frame()
        if isinstance(result, pd.DataFrame):
            table = _table(result)
            table.head(MAX_ROWS).to_parquet("/out/result.parquet", index=False)
            status |= {"kind": "table", "rows": int(len(table))}
        elif result is None:
            status["kind"] = "none"
        else:
            if isinstance(result, np.generic):
                result = result.item()
            status |= {"kind": "value", "value": json.loads(json.dumps(result, default=str))}
    except Exception:
        status = {"ok": False, "error": traceback.format_exc(limit=4)[-2000:]}
    status["stdout"] = output.getvalue()[-MAX_STDOUT:]
    with open("/out/status.json", "w", encoding="utf-8") as handle:
        json.dump(status, handle)


if __name__ == "__main__":
    main()
