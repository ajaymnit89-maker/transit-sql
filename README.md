# TransitSQL

Ask questions about NYC Access-A-Ride (paratransit) service in plain English and get answers from real MTA open data. An LLM writes the SQL; a **semantic layer** tells it how the business defines each metric; an **evaluation harness** measures how often it gets the right answer.

> "What was pickup on-time performance in 2025?" → `92.0%`, with the SQL shown.

**Status:** working end to end; first evaluation scored 31/32 (see Results). Live demo: _coming soon (Streamlit Community Cloud)_.

## Why this project

Most "chat with your data" demos give the model a table schema and hope. In real analytics, the hard part is the business logic. On-time performance must be a ratio of sums. The OTP table repeats every trip once per standard. No-shows are already a rate. A Power BI developer encodes these rules once in a semantic model. TransitSQL does the same for an LLM, and the companion study [`../semantic-layer-study`](../semantic-layer-study) measures how much that helps.

## How it works

```
question ──► context (schema + data dictionary + metrics + rules) ──► Claude
                                                                       │ JSON {sql, notes}
                     DuckDB (read-only, no file access) ◄── safety check (one SELECT only)
                                   │ error? send it back once for a fix
                                   ▼
                       answer table / chart + the SQL
```

| File | What it does |
|---|---|
| `src/transit_sql/data.py` | Downloads 8 MTA datasets from data.ny.gov, aggregates them server-side, builds `data/paratransit.duckdb` |
| `semantic_layer.yaml` | Table and column descriptions, metric definitions, business rules, few-shot examples |
| `src/transit_sql/context.py` | Builds the prompt context at four levels of detail (the study's conditions) |
| `src/transit_sql/agent.py` | One Claude call with structured output → SQL safety check → run → one repair attempt |
| `src/transit_sql/evaluate.py` | Runs the 32 gold questions and scores execution accuracy |
| `evals/gold_questions.yaml` | Questions, reference SQL, difficulty tags |
| `app.py` | Streamlit chat UI |

## Data

MTA NYCT Paratransit datasets on [data.ny.gov](https://data.ny.gov) (ridership, pickup on-time performance, complaints, ride time, provider no-shows), Oct 2016 to Sep 2026. The hourly source data is aggregated to daily or monthly tables, about 260K rows and 1.3 MB of Parquet in `data/raw/`.

| Table | Grain | Years |
|---|---|---|
| `trips_daily` | day × provider type × borough | 2016–2026 |
| `pickup_otp_daily` | day × provider type × OTP standard × borough | 2016–2026 |
| `complaints_daily` | day × provider type × category | 2016–2026 |
| `ride_time_monthly` | month × provider × peak × origin × max-ride-time flag | 2017–2023 |
| `provider_no_shows_monthly` | month × provider type | 2016–2023 |

## Run it

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run pytest -q                  # 41 offline tests, no key needed
uv run streamlit run app.py       # the app
uv run python -m transit_sql.evaluate --mode semantic   # score the gold set (about 2 cents on Haiku)
uv run python -m transit_sql.data # optional: re-download the data
```

**Two ways to call Claude** (`TRANSITSQL_BACKEND`, picked automatically):
- `claude-cli`: uses your Claude Pro/Max subscription through the `claude` command (Claude Code, headless, no tools, our own system prompt). No API key. Good for local development and the study; subject to your plan's usage limits.
- `api`: the Anthropic API, billed per token. Used when `ANTHROPIC_API_KEY` is set (copy `.env.example` to `.env`). Needed for the public deployment.

## Safety

The model's SQL runs on a read-only DuckDB connection with external file access disabled, after a parser check that allows exactly one `SELECT`/`WITH` statement. Results are capped at 1,000 rows.

## Results

First run, 22 Nov 2026: `semantic` mode, `claude-haiku-5-5`, `claude-cli` backend.

| Metric | Value |
|---|---|
| Execution accuracy | 31/32 (96.9%) |
| Needed a repair attempt | 0 of 32 |
| Tokens in / out | 127,456 / 9,789 (about 4,000 in and 300 out per question) |
| Median time per question | 4.1 s |

The miss was q29, "Which full calendar year had the fewest trips?" The model added a coverage check that no single year can pass, so the query returned no rows. The repair step only fires on SQL errors, so an empty result goes uncaught. Across four runs that day, one or two questions failed per run, and a different one each time, so expect 31 or 32 out of 32 from run to run.

Fixes made along the way:

- The scorer rejected correct answers. It required months as numbers when the model wrote them as dates (`2025-01-01`), and it failed a ranked list for a "which year" or "which provider" question whose top row was right. A gold query ending in `LIMIT 1` now judges only the top row. The first run scored 27/32 under the old scorer.
- q14, "How many clients were picked up in 2024?", had gold SQL that summed `pickup_otp_daily.total_pickups` (7.73M). That table covers only Primary and Broker providers, so the gold now counts `trips_daily.trips` (9.69M), as the semantic layer's rules say. The column description used to read "Clients picked up", which made the model switch between the two answers. It now says to use `total_pickups` only as the on-time denominator.

Other context modes and models are still to run.

## Author

Ajay Sharma, data analyst at MTA NYC Transit Paratransit. Built with public data only; nothing here comes from internal MTA systems.
