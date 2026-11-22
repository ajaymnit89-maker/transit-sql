"""Offline tests: no API key or network needed."""

import pandas as pd
import pytest

from transit_sql.agent import UnsafeSQL, check_sql, run_sql
from transit_sql.context import MODES, build_context, load_layer
from transit_sql.data import connect
from transit_sql.evaluate import load_gold, results_match


@pytest.fixture(scope="module")
def con():
    return connect()


# --- gold set -------------------------------------------------------------

GOLD = load_gold()


@pytest.mark.parametrize("item", [g for g in GOLD if g["sql"]], ids=lambda g: g["id"])
def test_gold_sql_runs_and_returns_rows(con, item):
    df = run_sql(con, item["sql"])
    assert len(df) > 0
    assert df.notna().all().all(), "gold answer contains NULLs"


def test_gold_ids_unique_and_not_in_fewshot():
    ids = [g["id"] for g in GOLD]
    assert len(ids) == len(set(ids))
    examples = {e["question"].lower() for e in load_layer()["examples"]}
    assert not examples & {g["question"].lower() for g in GOLD}


def test_known_answers(con):
    # Pin a few numbers so a data refresh can't silently change the benchmark.
    assert run_sql(con, "SELECT SUM(trips) FROM trips_daily WHERE year(service_date)=2025").iloc[0, 0] == 11_519_140
    otp = run_sql(con, next(g["sql"] for g in GOLD if g["id"] == "q08")).iloc[0, 0]
    assert otp == pytest.approx(92.0, abs=0.05)


# --- safety ---------------------------------------------------------------

@pytest.mark.parametrize("sql", [
    "DROP TABLE trips_daily",
    "DELETE FROM trips_daily",
    "SELECT 1; DROP TABLE trips_daily",
    "COPY trips_daily TO 'x.csv'",
])
def test_rejects_non_select(sql):
    with pytest.raises(UnsafeSQL):
        check_sql(sql)


def test_accepts_cte():
    check_sql("WITH t AS (SELECT 1 AS x) SELECT x FROM t")


def test_cannot_read_files(con):
    with pytest.raises(Exception):
        run_sql(con, "SELECT * FROM read_csv('pyproject.toml')")


# --- context modes --------------------------------------------------------

def test_context_modes_are_nested(con):
    texts = [build_context(con, m) for m in MODES]
    assert all(len(a) < len(b) for a, b in zip(texts, texts[1:]))
    assert "Metrics" not in texts[1] and "Metrics" in texts[2]
    assert "Examples" in texts[3]


# --- scoring --------------------------------------------------------------

def test_match_ignores_names_order_and_rounding():
    gold = pd.DataFrame({"borough": ["Bronx", "Queens"], "otp": [88.1, 90.0]})
    pred = pd.DataFrame({"pct": [90.04, 88.12], "b": ["queens", "Bronx"], "extra": [1, 2]})
    assert results_match(gold, pred)


def test_match_rejects_wrong_values():
    gold = pd.DataFrame({"otp": [88.1]})
    assert not results_match(gold, pd.DataFrame({"otp": [0.881]}))
    assert not results_match(gold, pd.DataFrame({"otp": [88.1, 1.0]}))


def test_match_months_as_dates():
    gold = pd.DataFrame({"month": [1, 2], "n": [5, 7]})
    as_dates = pd.DataFrame({"m": pd.to_datetime(["2025-01-01", "2025-02-01"]), "n": [5, 7]})
    as_text = pd.DataFrame({"m": ["2025-01", "2025-02"], "n": [5, 7]})
    assert results_match(gold, as_dates) and results_match(gold, as_text)
    assert not results_match(gold, pd.DataFrame({"m": ["2025-01", "2025-03"], "n": [5, 7]}))


def test_match_label_from_ranked_list():
    gold = pd.DataFrame({"provider_type": ["Broker"]})
    assert results_match(gold, pd.DataFrame({"provider_type": ["Broker", "Primary"], "n": [9, 3]}))
    assert not results_match(gold, pd.DataFrame({"provider_type": ["Primary", "Broker"], "n": [9, 3]}))
