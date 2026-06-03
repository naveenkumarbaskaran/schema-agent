# schema-agent-ai

AI-powered tool for inferring, documenting, and validating database schemas using
[Claude](https://www.anthropic.com/claude) (Anthropic).

## Features

- Connect to a **live database** (PostgreSQL, MySQL, SQLite, MSSQL, Oracle) and auto-document it
- Load a **SQL DDL file** and generate documentation from it
- Produce three output artefacts for every schema:
  - **Mermaid ER diagram** - paste into any Mermaid renderer or GitHub Markdown
  - **Table documentation** - per-table Markdown with column details and relationships
  - **Data dictionary** - flat Markdown table of every column across the database
- **Validate** a live database against an expected SQL schema definition

## Installation

```bash
pip install schema-agent-ai

# Extra drivers (install the one(s) you need)
pip install "schema-agent-ai[postgresql]"  # psycopg2-binary
pip install "schema-agent-ai[mysql]"       # pymysql
```

Set your Anthropic API key:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

## CLI usage

### Document a live database

```bash
schema-agent document --db "postgresql://user:pass@localhost/mydb" --output ./schema-docs
```

### Document a SQL file

```bash
schema-agent document --sql ./migrations/001_initial.sql --output ./schema-docs
```

Both commands write three files into `--output`:
- `er_diagram.md`
- `table_docs.md`
- `data_dictionary.md`

And display a Markdown preview in the terminal.

### Validate a database against a schema

```bash
schema-agent validate \
    --schema ./expected_schema.sql \
    --db "postgresql://user:pass@localhost/mydb"
```

Exits with code `0` if valid, `1` if differences are found.

## Python API

```python
from schema_agent import SchemaAgent

agent = SchemaAgent()  # reads ANTHROPIC_API_KEY from environment

# --- Document a live database ---
outputs = agent.document_from_db(
    connection_string="postgresql://user:pass@localhost/mydb",
    output_dir="./schema-docs",   # optional: write files
)
print(outputs["er_diagram"])       # Mermaid erDiagram block
print(outputs["table_docs"])       # Per-table Markdown
print(outputs["data_dictionary"])  # Column inventory Markdown table

# --- Document a SQL DDL file ---
outputs = agent.document_from_sql(
    sql_path="./schema.sql",
    output_dir="./schema-docs",
)

# --- Validate live DB against a schema file ---
result = agent.validate_schema(
    schema_sql_path="./expected_schema.sql",
    connection_string="postgresql://user:pass@localhost/mydb",
)
print(result["valid"])           # True / False
print(result["missing_tables"])  # list[str]
print(result["extra_tables"])    # list[str]
print(result["summary"])         # prose summary
```

## Architecture

```
 schema_agent/
 ├── __init__.py        - package exports
 ├── agent.py           - SchemaAgent: Claude tool-use loop + output formatting
 ├── introspector.py    - DbIntrospector: SQLAlchemy reflection
 └── cli.py             - Click CLI with Rich output
```

### How it works

1. `SchemaAgent` sends a user request to `claude-sonnet-4-6` along with three tool definitions:
   - `read_file(path)` - reads a SQL/text file from disk
   - `connect_db(connection_string)` - creates a SQLAlchemy engine and tests connectivity
   - `introspect_db()` - reflects all tables/columns/FKs/indexes and returns JSON
2. Claude drives the loop: it calls tools as needed, receives results, and produces the final
   Markdown documentation.
3. `DbIntrospector` does the actual SQLAlchemy reflection work; the introspected schema is
   serialised to JSON and fed back to Claude as a tool result.
4. The agent loop continues until `stop_reason == "end_turn"`, then parses the three output
   sections from the response.

## Supported databases

| Database   | Connection string example                         | Extra package     |
|------------|---------------------------------------------------|-------------------|
| PostgreSQL | `postgresql://user:pass@host/db`                  | `[postgresql]`    |
| MySQL      | `mysql+pymysql://user:pass@host/db`               | `[mysql]`         |
| SQLite     | `sqlite:///path/to/file.db`                       | *(none)*          |
| MSSQL      | `mssql+pyodbc://user:pass@host/db?driver=...`     | `[mssql]`         |
| Oracle     | `oracle+cx_oracle://user:pass@host:1521/service`  | `[oracle]`        |

## Development

```bash
git clone https://github.com/example/schema-agent-ai
cd schema-agent-ai
pip install -e ".[dev]"
pytest
```

## License

MIT
