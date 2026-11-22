"""Build the database context the LLM sees, at four levels of detail.

These four modes are the experimental conditions of the semantic-layer study:

    raw_schema        table and column names + types, straight from DuckDB
    schema_dict       + plain-English table/column descriptions (data dictionary)
    semantic          + metric definitions and business rules (semantic layer)
    semantic_fewshot  + worked question -> SQL examples
"""

from pathlib import Path

import duckdb
import yaml

ROOT = Path(__file__).resolve().parents[2]
SEMANTIC_LAYER = ROOT / "semantic_layer.yaml"

MODES = ("raw_schema", "schema_dict", "semantic", "semantic_fewshot")


def load_layer(path: Path = SEMANTIC_LAYER) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def build_context(con: duckdb.DuckDBPyConnection, mode: str = "semantic") -> str:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    layer = load_layer()
    with_dict = mode != "raw_schema"
    parts = ["## Tables"]

    for table in sorted(r[0] for r in con.execute("SHOW TABLES").fetchall()):
        meta = layer["tables"].get(table, {})
        header = f"### {table}"
        if with_dict and meta.get("description"):
            header += f"\n{meta['description']}"
        parts.append(header)
        for name, dtype, *_ in con.execute(f"DESCRIBE {table}").fetchall():
            line = f"- {name} ({dtype})"
            if with_dict and name in meta.get("columns", {}):
                line += f": {meta['columns'][name]}"
            parts.append(line)

    if mode in ("semantic", "semantic_fewshot"):
        parts.append("\n## Metrics (use these exact definitions)")
        for name, m in layer["metrics"].items():
            line = f"- {name} = {m['sql']}  -- {m['description']}"
            if m.get("filter"):
                line += f" Default filter: {m['filter']}."
            parts.append(line)
        parts.append("\n## Business rules")
        parts += [f"- {rule}" for rule in layer["rules"]]

    if mode == "semantic_fewshot":
        parts.append("\n## Examples")
        for ex in layer["examples"]:
            parts.append(f"Question: {ex['question']}\nSQL:\n{ex['sql'].strip()}\n")

    return "\n".join(parts)
