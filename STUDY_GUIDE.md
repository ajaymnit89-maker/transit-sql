# TransitSQL study guide

Read this with the code open. Goal: you can explain every part in an interview and change it yourself.

## 1. The ideas, in your own world first

| AI term | What you already know that is the same thing |
|---|---|
| Text-to-SQL | A business user asks you a question; you write the SQL. Here Claude writes it. |
| Context / prompt | The briefing you'd give a new analyst on day one: tables, what columns mean, how KPIs are defined. |
| Semantic layer | Your Power BI semantic model: measures like `OTP % = DIVIDE(SUM(OnTime), SUM(Total))` defined once. |
| Structured output | Forcing the answer into a fixed shape (JSON with `sql` and `notes`), like a typed column. |
| Evaluation / eval | A QA checklist: known questions with known answers, re-run after every change. Like reconciling a report against the source. |
| Execution accuracy | The model's query returns the same numbers as the reference query. We compare results, not SQL text. |
| Few-shot examples | Showing the new analyst two or three finished queries before they start. |
| Tokens | The units the API bills by, roughly 4 characters each. |

## 2. Walk through one question

Question: *"What was pickup on-time performance in 2025?"*

1. `app.py` receives the question and calls `ask()` in `agent.py`.
2. `build_context()` in `context.py` reads the table list from DuckDB and adds descriptions, metric definitions and rules from `semantic_layer.yaml`. In `semantic` mode the model is told OTP = `100 * SUM(ontime_pickups) / SUM(total_pickups)`, filtered to the `20-minutes` standard.
3. `_call()` sends the system prompt + context + question to Claude with `output_config.format` set to a JSON schema, so the reply is always `{"sql": ..., "notes": ...}`. `effort: "low"` keeps it cheap and fast.
4. `check_sql()` parses the SQL with `sqlglot` and refuses anything that is not exactly one SELECT.
5. `run_sql()` executes it on a read-only DuckDB connection with file access off.
6. If DuckDB raises an error, the error message goes back to Claude once ("That SQL failed... return a corrected query"). This self-repair step fixes most typos and wrong column names.
7. The app shows a big number for a single value, otherwise a table and chart, plus the SQL.

## 3. File by file

- **`data.py`**: `SOURCES` maps each local table to one or two data.ny.gov datasets plus a SoQL query (Socrata's SQL dialect) that aggregates hourly rows to daily. `fetch()` pages through results 50,000 rows at a time. Bug we hit and fixed: paging needs `ORDER BY` on *all* group columns. Ordering only by date returned duplicate and missing rows, which inflated 2017 trips by about 30%. Good interview story about data validation.
- **`semantic_layer.yaml`**: the most important file. Changing a rule here changes model behavior without touching code.
- **`context.py`**: four modes, each a superset of the previous one. That nesting is what makes the study a clean comparison.
- **`agent.py`**: about 100 lines. Note `cache_control`: the context is identical across calls, so the API caches it and later calls are cheaper.
- **`evaluate.py`**: `results_match()` checks that every gold column appears among the predicted columns (names and row order ignored; numbers within ±0.05 or 0.1%).
- **`tests/test_core.py`**: runs every gold query, checks safety rules, checks scoring logic. No API key needed, so it runs in CI on every push.

## 4. Things to try (do these yourself)

1. Run the eval in `raw_schema` mode and in `semantic` mode. Open the CSVs in `evals/results/` and find a question that flips from FAIL to PASS. Read both SQL queries. Why did it change?
2. Add a metric to `semantic_layer.yaml`, e.g. `trips_per_day`. Add a gold question that needs it. Run `pytest`.
3. Delete the OTP standard rule from `rules:`. Re-run q08 and q14. What breaks?
4. Change `DEFAULT_MODEL` to `claude-sonnet-5-5` and compare accuracy and cost.
5. Write 5 new gold questions from your MTA experience (public-data facts only).

## 5. Interview questions you should be ready for

**"How do you stop the model from running dangerous SQL?"**
Three layers: a parser check that allows only one SELECT, a read-only database connection, and external file access disabled. Plus a 1,000-row cap. The model never sees credentials.

**"How do you know it works?"**
A gold set of 32 questions with reference SQL, tagged by difficulty. I score execution accuracy, comparing results rather than SQL text, because many SQL queries give the same right answer. Tests run in CI.

**"Why a semantic layer instead of just a better prompt?"**
The business logic is the hard part, not SQL syntax. In BI we solved this with semantic models. Giving the LLM the same definitions makes answers consistent with the official dashboards, and the companion study measures the gain.

**"What happens when the model is wrong?"**
SQL errors get one automatic repair attempt. Wrong-but-valid SQL is the real risk. That's why the app always shows the SQL, and why the eval tags failures by type so I can fix the layer.

**"What does it cost?"**
About 1,500 input tokens and a few hundred output tokens per question. On Claude Haiku 5.5 that's a fraction of a cent. The context is cached across calls.

**"How would you productionize it at a transit agency?"**
Point it at the governed warehouse (e.g. Trino over ADLS), generate the semantic layer from the existing Power BI model, add row-level security, log every question and SQL for audit, and grow the eval set from real user questions.
