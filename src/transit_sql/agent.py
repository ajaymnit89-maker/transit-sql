"""Text-to-SQL: question -> Claude writes SQL -> validate -> run on DuckDB.

One LLM call per attempt. If the SQL fails to run or returns no rows, that
goes back to Claude once for a fix. That is the whole loop.
"""

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field

import anthropic
import duckdb
import pandas as pd
import sqlglot
from dotenv import load_dotenv
from sqlglot import exp

from transit_sql.context import build_context

load_dotenv()

DEFAULT_MODEL = "claude-haiku-5-5"  # cheapest current model; fine for SQL
MAX_ROWS = 1000

SYSTEM = """You are a careful analytics engineer. You answer questions about \
NYC paratransit (Access-A-Ride) data by writing ONE DuckDB SQL query.

Rules:
- Return a single read-only SELECT (CTEs allowed). No DDL/DML.
- Use only the tables and columns listed in the context.
- Give result columns short, readable names. Round percentages to 1 decimal.
- If the question cannot be answered from this data, set sql to an empty \
string and explain why in notes.

{context}"""

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "sql": {"type": "string", "description": "One DuckDB SELECT query, or empty."},
        "notes": {"type": "string", "description": "One sentence: assumptions made."},
    },
    "required": ["sql", "notes"],
    "additionalProperties": False,
}


@dataclass
class Answer:
    question: str
    sql: str = ""
    notes: str = ""
    df: pd.DataFrame | None = None
    error: str | None = None
    attempts: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    log: list = field(default_factory=list)


class UnsafeSQL(ValueError):
    pass


def check_sql(sql: str) -> None:
    """Allow exactly one SELECT statement. The DB is also opened read-only."""
    statements = sqlglot.parse(sql, dialect="duckdb")
    if len(statements) != 1 or statements[0] is None:
        raise UnsafeSQL("Expected exactly one SQL statement.")
    if not isinstance(statements[0], exp.Query):
        raise UnsafeSQL(f"Only SELECT queries are allowed, got {statements[0].key}.")


def run_sql(con: duckdb.DuckDBPyConnection, sql: str) -> pd.DataFrame:
    check_sql(sql)
    return con.execute(sql).fetch_df().head(MAX_ROWS)


def default_backend() -> str:
    """'api' (pay per token, needs ANTHROPIC_API_KEY) or 'claude-cli' (uses
    your Claude Pro/Max subscription through the `claude` command)."""
    return os.getenv("TRANSITSQL_BACKEND") or ("api" if os.getenv("ANTHROPIC_API_KEY") else "claude-cli")


def _call_api(client, model, system, messages):
    """Returns (parsed JSON, input tokens, output tokens)."""
    resp = client.messages.create(
        model=model,
        max_tokens=4000,
        system=system,
        messages=messages,
        cache_control={"type": "ephemeral"},  # the context is identical every call
        output_config={
            "effort": "low",
            "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA},
        },
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError("Model declined the request.")
    text = next(b.text for b in resp.content if b.type == "text")
    u = resp.usage
    tokens_in = u.input_tokens + (u.cache_read_input_tokens or 0) + (u.cache_creation_input_tokens or 0)
    return json.loads(text), tokens_in, u.output_tokens


def _call_cli(model, system, messages):
    """Same call through Claude Code in headless mode. No tools, no user
    settings or plugins, and our system prompt replaces Claude Code's own."""
    prompt = "\n\n".join(f"[{m['role']}]\n{m['content']}" for m in messages)
    cmd = [
        shutil.which("claude") or "claude", "-p",
        "--model", model, "--effort", "low",
        "--tools", "", "--strict-mcp-config", "--setting-sources", "project",
        "--no-session-persistence", "--output-format", "json",
        "--system-prompt", system,
        "--json-schema", json.dumps(OUTPUT_SCHEMA),
    ]
    proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                          encoding="utf-8", timeout=300, cwd=tempfile.gettempdir())
    try:
        out = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"claude CLI failed: {proc.stderr.strip() or proc.stdout[:500]}")
    if out.get("is_error") or "structured_output" not in out:
        raise RuntimeError(f"claude CLI error: {out.get('result') or out.get('subtype')}")
    u = out["usage"]
    tokens_in = u["input_tokens"] + u.get("cache_read_input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
    return out["structured_output"], tokens_in, u["output_tokens"]


def ask(
    question: str,
    con: duckdb.DuckDBPyConnection,
    mode: str = "semantic",
    model: str = DEFAULT_MODEL,
    client: anthropic.Anthropic | None = None,
    max_attempts: int = 2,
    backend: str | None = None,
) -> Answer:
    backend = backend or default_backend()
    if backend == "api":
        client = client or anthropic.Anthropic()
        call = lambda s, m: _call_api(client, model, s, m)
    else:
        call = lambda s, m: _call_cli(model, s, m)

    system = SYSTEM.format(context=build_context(con, mode))
    messages = [{"role": "user", "content": question}]
    ans = Answer(question)

    for _ in range(max_attempts):
        ans.attempts += 1
        out, tokens_in, tokens_out = call(system, messages)
        ans.input_tokens += tokens_in
        ans.output_tokens += tokens_out
        ans.sql, ans.notes = out["sql"].strip(), out["notes"]
        ans.log.append(ans.sql)
        if not ans.sql:
            ans.error = "Model said the question cannot be answered."
            return ans
        try:
            ans.df, ans.error = run_sql(con, ans.sql), None
        except (UnsafeSQL, duckdb.Error, sqlglot.errors.ParseError) as e:
            ans.error = str(e)
            feedback = f"That SQL failed:\n{e}\nReturn a corrected query."
        else:
            if not ans.df.empty or ans.attempts == max_attempts:
                return ans  # an empty result on the last attempt may be the true answer
            feedback = ("That SQL ran but returned no rows. Check the filters against the "
                        "data's date ranges and values. If no rows is the true answer, return the same query.")
        messages += [
            {"role": "assistant", "content": json.dumps(out)},
            {"role": "user", "content": feedback},
        ]
    return ans
