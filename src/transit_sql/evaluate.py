"""Score TransitSQL against the gold question set (execution accuracy).

A prediction is correct when its result table contains the gold result:
same number of rows, and every gold column matches some predicted column
(values compared after sorting, numbers with a small tolerance). Extra
columns and different column names are fine. For unanswerable questions
(gold sql: null) the model must decline.

Run:  uv run python -m transit_sql.evaluate --mode semantic --model claude-haiku-5-5
"""

import argparse
import math
import re
import time
from datetime import date, datetime
from pathlib import Path

import anthropic
import pandas as pd
import yaml

from transit_sql.agent import DEFAULT_MODEL, ask, default_backend, run_sql
from transit_sql.context import MODES
from transit_sql.data import connect

ROOT = Path(__file__).resolve().parents[2]
GOLD = ROOT / "evals" / "gold_questions.yaml"
RESULTS = ROOT / "evals" / "results"


def load_gold(path: Path = GOLD) -> list[dict]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _norm(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return str(v).strip().lower()


def _same(a, b) -> bool:
    if isinstance(a, float) and isinstance(b, float):
        # Tolerate rounding: 87.25 vs 87.3, or 0.873 written as a percent is NOT ok.
        return math.isclose(a, b, rel_tol=1e-3, abs_tol=0.051)
    return a == b


def _column_matches(gold_col: list, pred_col: list) -> bool:
    key = lambda v: (v is None, str(type(v)), v if v is not None else 0)
    g, p = sorted(gold_col, key=key), sorted(pred_col, key=key)
    return all(_same(x, y) for x, y in zip(g, p))


def _month(v):
    """A month written as a date ('2025-01-01', '2025-01') -> its number, else unchanged."""
    if isinstance(v, date):
        return float(v.month)
    if isinstance(v, str) and re.fullmatch(r"\d{4}-\d{2}(-\d{2})?( 00:00:00)?", v.strip()):
        return float(v.strip()[5:7])
    return _norm(v)


def results_match(gold: pd.DataFrame, pred: pd.DataFrame, top_only: bool = False) -> bool:
    """top_only: the gold asks for the top item (ends in LIMIT 1), so a ranked list is judged by its first row."""
    if pred is None:
        return False
    if top_only:
        pred = pred.head(1)
    if len(gold) != len(pred):
        return False
    # Each predicted column may also match as month numbers (gold uses month(), models often date_trunc).
    pred_cols = [[f(v) for v in pred[c]] for c in pred.columns for f in (_norm, _month)]
    for c in gold.columns:
        gold_col = [_norm(v) for v in gold[c]]
        if not any(_column_matches(gold_col, pc) for pc in pred_cols):
            return False
    return True


def evaluate(mode: str, model: str, ids: list[str] | None = None) -> pd.DataFrame:
    con = connect()
    client = anthropic.Anthropic() if default_backend() == "api" else None
    rows = []
    for item in load_gold():
        if ids and item["id"] not in ids:
            continue
        start = time.perf_counter()
        ans = ask(item["question"], con, mode=mode, model=model, client=client)
        seconds = time.perf_counter() - start

        if item["sql"] is None:
            correct = not ans.sql  # should decline
        else:
            top_only = bool(re.search(r"\bLIMIT 1\s*;?\s*$", item["sql"], re.I))
            correct = ans.error is None and results_match(run_sql(con, item["sql"]), ans.df, top_only)

        rows.append({
            "id": item["id"], "mode": mode, "model": model,
            "tags": ",".join(item["tags"]), "correct": correct,
            "attempts": ans.attempts, "error": ans.error,
            "input_tokens": ans.input_tokens, "output_tokens": ans.output_tokens,
            "seconds": round(seconds, 2), "sql": ans.sql, "notes": ans.notes,
        })
        print(f"{item['id']} {'PASS' if correct else 'FAIL'}  {item['question']}")
    return pd.DataFrame(rows)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--mode", default="semantic", choices=MODES)
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--ids", nargs="*", help="only these question ids")
    args = p.parse_args()

    df = evaluate(args.mode, args.model, args.ids)
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / f"{datetime.now():%Y%m%d-%H%M%S}_{args.model}_{args.mode}.csv"
    df.to_csv(out, index=False)
    print(f"\nExecution accuracy: {df.correct.mean():.1%} ({df.correct.sum()}/{len(df)})")
    print(df.groupby(df.tags.str.split(",").str[0]).correct.mean().round(2).to_string())
    print(f"Tokens in/out: {df.input_tokens.sum():,} / {df.output_tokens.sum():,}")
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
