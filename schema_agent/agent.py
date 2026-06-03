"""
SchemaAgent: Uses Claude claude-sonnet-4-6 with tool use to introspect databases
and generate ER diagrams, table documentation, and data dictionaries.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import anthropic

from .introspector import DbIntrospector

# Tool definitions for the agent
READ_FILE_TOOL: dict[str, Any] = {
    "name": "read_file",
    "description": (
        "Read the contents of a SQL or text file from the filesystem. "
        "Use this to load SQL schema files (.sql) or any other text-based schema definitions."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute or relative path to the file to read.",
            }
        },
        "required": ["path"],
    },
}

CONNECT_DB_TOOL: dict[str, Any] = {
    "name": "connect_db",
    "description": (
        "Connect to a live database using a SQLAlchemy connection string. "
        "Call this before introspect_db. Supported dialects: postgresql, mysql, sqlite, "
        "mssql, oracle. Returns a confirmation message on success."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "connection_string": {
                "type": "string",
                "description": (
                    "SQLAlchemy connection string, e.g. "
                    "'postgresql://user:pass@host/dbname' or 'sqlite:///path/to/db.sqlite'"
                ),
            }
        },
        "required": ["connection_string"],
    },
}

INTROSPECT_DB_TOOL: dict[str, Any] = {
    "name": "introspect_db",
    "description": (
        "Introspect the currently connected database and return a full schema snapshot "
        "including all tables, columns (with types, nullability, defaults), foreign key "
        "relationships, and indexes. Must call connect_db first."
    ),
    "input_schema": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}

SYSTEM_PROMPT = """\
You are a database schema documentation expert. Your job is to analyze database schemas
(from live databases or SQL files) and produce three outputs:

1. **Mermaid ER diagram** – A complete entity-relationship diagram in Mermaid `erDiagram` syntax
   showing all tables, their columns with types, and all foreign-key relationships.

2. **Table documentation** – Markdown with a section per table describing its purpose,
   columns (name, type, nullable, default, description), and relationships.

3. **Data dictionary** – A Markdown table listing every column across all tables:
   Table | Column | Type | Nullable | Default | Description

Workflow:
- If given a connection string, call connect_db then introspect_db.
- If given a SQL file path, call read_file to load the DDL.
- Analyse the schema and produce all three outputs.
- Write final outputs to the paths provided (if any) or return them inline.

Always be thorough. Infer sensible descriptions for columns when not explicitly documented.
"""


class SchemaAgent:
    """Agent that documents and validates database schemas using Claude."""

    def __init__(self, api_key: str | None = None, model: str = "claude-sonnet-4-6") -> None:
        self._client = anthropic.Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))
        self._model = model
        self._introspector: DbIntrospector | None = None
        self._tools = [READ_FILE_TOOL, CONNECT_DB_TOOL, INTROSPECT_DB_TOOL]

    # ------------------------------------------------------------------
    # Tool execution
    # ------------------------------------------------------------------

    def _execute_tool(self, name: str, tool_input: dict[str, Any]) -> str:
        """Dispatch a tool call and return a string result."""
        if name == "read_file":
            return self._tool_read_file(tool_input["path"])
        if name == "connect_db":
            return self._tool_connect_db(tool_input["connection_string"])
        if name == "introspect_db":
            return self._tool_introspect_db()
        return f"Unknown tool: {name}"

    def _tool_read_file(self, path: str) -> str:
        try:
            content = Path(path).read_text(encoding="utf-8")
            return content
        except FileNotFoundError:
            return f"Error: file not found: {path}"
        except Exception as exc:  # noqa: BLE001
            return f"Error reading file {path}: {exc}"

    def _tool_connect_db(self, connection_string: str) -> str:
        try:
            self._introspector = DbIntrospector(connection_string)
            self._introspector.connect()
            dialect = self._introspector.dialect
            return f"Connected successfully to {dialect} database."
        except Exception as exc:  # noqa: BLE001
            return f"Error connecting to database: {exc}"

    def _tool_introspect_db(self) -> str:
        if self._introspector is None:
            return "Error: no database connection. Call connect_db first."
        try:
            schema = self._introspector.introspect()
            return json.dumps(schema, indent=2, default=str)
        except Exception as exc:  # noqa: BLE001
            return f"Error introspecting database: {exc}"

    # ------------------------------------------------------------------
    # Core agentic loop
    # ------------------------------------------------------------------

    def _run_loop(self, messages: list[dict[str, Any]]) -> str:
        """Run the tool-use agentic loop and return the final assistant text."""
        while True:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=8192,
                system=SYSTEM_PROMPT,
                tools=self._tools,  # type: ignore[arg-type]
                messages=messages,
            )

            # Append assistant turn
            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "end_turn":
                # Extract final text
                for block in response.content:
                    if block.type == "text":
                        return block.text
                return ""

            if response.stop_reason == "tool_use":
                # Execute all requested tool calls
                tool_results: list[dict[str, Any]] = []
                for block in response.content:
                    if block.type == "tool_use":
                        result_content = self._execute_tool(block.name, block.input)  # type: ignore[arg-type]
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": result_content,
                            }
                        )
                messages.append({"role": "user", "content": tool_results})
                continue

            # Any other stop reason – return what we have
            for block in response.content:
                if block.type == "text":
                    return block.text
            return ""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def document_from_db(
        self,
        connection_string: str,
        output_dir: str | Path | None = None,
    ) -> dict[str, str]:
        """
        Connect to a live database, introspect its schema, and produce
        ER diagram, table docs, and data dictionary.

        Parameters
        ----------
        connection_string:
            SQLAlchemy-style connection string.
        output_dir:
            Directory to write output files into. If None, outputs are
            returned only in the result dict.

        Returns
        -------
        dict with keys 'er_diagram', 'table_docs', 'data_dictionary'.
        """
        extra = ""
        if output_dir:
            extra = (
                f"\n\nWrite the outputs to these files:\n"
                f"- ER diagram: {output_dir}/er_diagram.md\n"
                f"- Table docs: {output_dir}/table_docs.md\n"
                f"- Data dictionary: {output_dir}/data_dictionary.md"
            )

        prompt = (
            f"Document the database at connection string: {connection_string}\n"
            "Connect to it, introspect the schema, then produce all three outputs "
            "(Mermaid ER diagram, table documentation, data dictionary)."
            + extra
        )

        messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
        result_text = self._run_loop(messages)

        outputs = self._parse_outputs(result_text)

        if output_dir:
            self._write_outputs(Path(output_dir), outputs)

        return outputs

    def document_from_sql(
        self,
        sql_path: str | Path,
        output_dir: str | Path | None = None,
    ) -> dict[str, str]:
        """
        Read a SQL DDL file and produce ER diagram, table docs, and data dictionary.

        Parameters
        ----------
        sql_path:
            Path to the .sql file containing CREATE TABLE statements.
        output_dir:
            Directory to write output files into.

        Returns
        -------
        dict with keys 'er_diagram', 'table_docs', 'data_dictionary'.
        """
        extra = ""
        if output_dir:
            extra = (
                f"\n\nWrite the outputs to these files:\n"
                f"- ER diagram: {output_dir}/er_diagram.md\n"
                f"- Table docs: {output_dir}/table_docs.md\n"
                f"- Data dictionary: {output_dir}/data_dictionary.md"
            )

        prompt = (
            f"Document the database schema defined in the SQL file: {sql_path}\n"
            "Read the file, analyse the DDL, then produce all three outputs "
            "(Mermaid ER diagram, table documentation, data dictionary)."
            + extra
        )

        messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
        result_text = self._run_loop(messages)

        outputs = self._parse_outputs(result_text)

        if output_dir:
            self._write_outputs(Path(output_dir), outputs)

        return outputs

    def validate_schema(
        self,
        schema_sql_path: str | Path,
        connection_string: str,
    ) -> dict[str, Any]:
        """
        Compare a SQL schema definition against a live database.

        Parameters
        ----------
        schema_sql_path:
            Path to the expected schema SQL file.
        connection_string:
            SQLAlchemy connection string for the live database.

        Returns
        -------
        dict with keys 'valid' (bool), 'differences' (list[str]), 'summary' (str).
        """
        prompt = (
            f"Validate the database at '{connection_string}' against the schema "
            f"defined in '{schema_sql_path}'.\n\n"
            "Steps:\n"
            "1. Read the SQL schema file to get the expected schema.\n"
            "2. Connect to the live database and introspect its actual schema.\n"
            "3. Compare the two: list tables/columns/types that are missing, extra, "
            "or have mismatched types.\n"
            "4. Produce a structured report with sections:\n"
            "   - VALID: yes/no\n"
            "   - MISSING_TABLES: list\n"
            "   - EXTRA_TABLES: list\n"
            "   - COLUMN_DIFFERENCES: table-by-table list\n"
            "   - SUMMARY: one paragraph\n"
        )

        messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
        result_text = self._run_loop(messages)

        return self._parse_validation_result(result_text)

    # ------------------------------------------------------------------
    # Output helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_outputs(text: str) -> dict[str, str]:
        """Extract the three output sections from the agent response."""
        sections: dict[str, str] = {
            "er_diagram": "",
            "table_docs": "",
            "data_dictionary": "",
            "raw": text,
        }

        # Try to find delimited sections; fall back to the full text
        import re

        er_match = re.search(
            r"(?:#+\s*(?:ER Diagram|Entity.Relationship Diagram)[^\n]*\n)(.*?)(?=#+|$)",
            text,
            re.DOTALL | re.IGNORECASE,
        )
        td_match = re.search(
            r"(?:#+\s*Table Documentation[^\n]*\n)(.*?)(?=#+|$)",
            text,
            re.DOTALL | re.IGNORECASE,
        )
        dd_match = re.search(
            r"(?:#+\s*Data Dictionary[^\n]*\n)(.*?)(?=#+|$)",
            text,
            re.DOTALL | re.IGNORECASE,
        )

        if er_match:
            sections["er_diagram"] = er_match.group(1).strip()
        if td_match:
            sections["table_docs"] = td_match.group(1).strip()
        if dd_match:
            sections["data_dictionary"] = dd_match.group(1).strip()

        # If regex extraction failed, put everything in raw
        if not any([sections["er_diagram"], sections["table_docs"], sections["data_dictionary"]]):
            sections["er_diagram"] = text
            sections["table_docs"] = text
            sections["data_dictionary"] = text

        return sections

    @staticmethod
    def _parse_validation_result(text: str) -> dict[str, Any]:
        """Parse the validation report text into a structured dict."""
        import re

        valid_match = re.search(r"VALID:\s*(yes|no)", text, re.IGNORECASE)
        valid = valid_match.group(1).lower() == "yes" if valid_match else None

        missing_tables: list[str] = []
        mt_match = re.search(
            r"MISSING_TABLES:\s*\n(.*?)(?=\n[A-Z_]+:|$)", text, re.DOTALL | re.IGNORECASE
        )
        if mt_match:
            missing_tables = [
                line.strip().lstrip("-* ") for line in mt_match.group(1).splitlines() if line.strip()
            ]

        extra_tables: list[str] = []
        et_match = re.search(
            r"EXTRA_TABLES:\s*\n(.*?)(?=\n[A-Z_]+:|$)", text, re.DOTALL | re.IGNORECASE
        )
        if et_match:
            extra_tables = [
                line.strip().lstrip("-* ") for line in et_match.group(1).splitlines() if line.strip()
            ]

        summary_match = re.search(
            r"SUMMARY:\s*\n(.*?)$", text, re.DOTALL | re.IGNORECASE
        )
        summary = summary_match.group(1).strip() if summary_match else text

        return {
            "valid": valid,
            "missing_tables": missing_tables,
            "extra_tables": extra_tables,
            "differences": missing_tables + extra_tables,
            "summary": summary,
            "raw": text,
        }

    @staticmethod
    def _write_outputs(output_dir: Path, outputs: dict[str, str]) -> None:
        """Write documentation outputs to the given directory."""
        output_dir.mkdir(parents=True, exist_ok=True)

        er_content = "# ER Diagram\n\n" + outputs.get("er_diagram", "")
        (output_dir / "er_diagram.md").write_text(er_content, encoding="utf-8")

        td_content = "# Table Documentation\n\n" + outputs.get("table_docs", "")
        (output_dir / "table_docs.md").write_text(td_content, encoding="utf-8")

        dd_content = "# Data Dictionary\n\n" + outputs.get("data_dictionary", "")
        (output_dir / "data_dictionary.md").write_text(dd_content, encoding="utf-8")
