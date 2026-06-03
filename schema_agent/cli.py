"""
CLI for schema-agent.

Commands
--------
document  --db <conn_str> [--output <dir>]
          --sql <file>    [--output <dir>]

validate  --schema <file> --db <conn_str>
"""

from __future__ import annotations

import sys
from pathlib import Path

import click
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from .agent import SchemaAgent

console = Console()


def _make_agent(api_key: str | None, model: str) -> SchemaAgent:
    """Create a SchemaAgent, aborting if no API key is found."""
    import os

    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        console.print(
            "[bold red]Error:[/bold red] ANTHROPIC_API_KEY environment variable is not set. "
            "Pass --api-key or set the environment variable."
        )
        sys.exit(1)
    return SchemaAgent(api_key=key, model=model)


@click.group()
def cli() -> None:
    """schema-agent: Document and validate database schemas using AI."""


@cli.command("document")
@click.option("--db", "connection_string", default=None, help="SQLAlchemy connection string.")
@click.option("--sql", "sql_file", default=None, type=click.Path(exists=True), help="SQL DDL file.")
@click.option(
    "--output",
    "output_dir",
    default=None,
    type=click.Path(),
    help="Directory to write output Markdown files.",
)
@click.option("--api-key", default=None, envvar="ANTHROPIC_API_KEY", help="Anthropic API key.")
@click.option(
    "--model",
    default="claude-sonnet-4-6",
    show_default=True,
    help="Claude model to use.",
)
@click.option("--no-preview", is_flag=True, default=False, help="Skip inline Markdown preview.")
def document(
    connection_string: str | None,
    sql_file: str | None,
    output_dir: str | None,
    api_key: str | None,
    model: str,
    no_preview: bool,
) -> None:
    """
    Generate ER diagram, table documentation, and data dictionary for a database.

    Either --db (live database) or --sql (SQL file) must be provided.
    """
    if not connection_string and not sql_file:
        console.print(
            "[bold red]Error:[/bold red] Provide either --db <connection_string> or --sql <file>."
        )
        sys.exit(1)

    if connection_string and sql_file:
        console.print(
            "[bold red]Error:[/bold red] Provide only one of --db or --sql, not both."
        )
        sys.exit(1)

    agent = _make_agent(api_key, model)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
    ) as progress:
        if connection_string:
            task = progress.add_task("Connecting and introspecting database...", total=None)
            source_label = connection_string
        else:
            task = progress.add_task(f"Reading SQL file: {sql_file} ...", total=None)
            source_label = str(sql_file)

        progress.update(task, description="Generating documentation with Claude...")

        try:
            if connection_string:
                outputs = agent.document_from_db(
                    connection_string=connection_string,
                    output_dir=output_dir,
                )
            else:
                outputs = agent.document_from_sql(
                    sql_path=str(sql_file),
                    output_dir=output_dir,
                )
        except Exception as exc:  # noqa: BLE001
            console.print(f"[bold red]Error:[/bold red] {exc}")
            sys.exit(1)

    console.print()
    console.print(
        Panel(
            f"[bold green]Documentation generated[/bold green] for [cyan]{source_label}[/cyan]",
            expand=False,
        )
    )

    if output_dir:
        out = Path(output_dir)
        console.print(f"\nFiles written to [cyan]{out.resolve()}[/cyan]:")
        for fname in ["er_diagram.md", "table_docs.md", "data_dictionary.md"]:
            fpath = out / fname
            if fpath.exists():
                size = fpath.stat().st_size
                console.print(f"  [green]checkmark[/green] {fname} ({size} bytes)")

    if not no_preview:
        console.print()
        _show_section("ER Diagram", outputs.get("er_diagram", ""))
        _show_section("Table Documentation", outputs.get("table_docs", ""))
        _show_section("Data Dictionary", outputs.get("data_dictionary", ""))


@cli.command("validate")
@click.option(
    "--schema",
    "schema_file",
    required=True,
    type=click.Path(exists=True),
    help="Expected SQL schema file.",
)
@click.option(
    "--db",
    "connection_string",
    required=True,
    help="SQLAlchemy connection string for the live database.",
)
@click.option("--api-key", default=None, envvar="ANTHROPIC_API_KEY", help="Anthropic API key.")
@click.option(
    "--model",
    default="claude-sonnet-4-6",
    show_default=True,
    help="Claude model to use.",
)
def validate(
    schema_file: str,
    connection_string: str,
    api_key: str | None,
    model: str,
) -> None:
    """
    Validate a live database against an expected SQL schema definition.

    Compares tables, columns, and types; reports missing, extra, and mismatched items.
    """
    agent = _make_agent(api_key, model)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
    ) as progress:
        progress.add_task("Validating schema...", total=None)
        try:
            result = agent.validate_schema(
                schema_sql_path=schema_file,
                connection_string=connection_string,
            )
        except Exception as exc:  # noqa: BLE001
            console.print(f"[bold red]Error:[/bold red] {exc}")
            sys.exit(1)

    console.print()

    valid = result.get("valid")
    if valid is True:
        console.print(
            Panel("[bold green]VALID[/bold green] - Schema matches the live database.", expand=False)
        )
    elif valid is False:
        console.print(
            Panel(
                "[bold red]INVALID[/bold red] - Differences found between schema and live database.",
                expand=False,
            )
        )
    else:
        console.print(Panel("Validation complete (could not determine overall validity).", expand=False))

    # Show differences table
    missing = result.get("missing_tables", [])
    extra = result.get("extra_tables", [])

    if missing or extra:
        diff_table = Table(title="Table Differences", show_lines=True)
        diff_table.add_column("Type", style="bold")
        diff_table.add_column("Table")
        for t in missing:
            diff_table.add_row("[red]Missing[/red]", t)
        for t in extra:
            diff_table.add_row("[yellow]Extra[/yellow]", t)
        console.print(diff_table)
    else:
        console.print("[dim]No table-level differences detected.[/dim]")

    # Show summary
    summary = result.get("summary", "")
    if summary:
        console.print()
        console.print(Panel(Markdown(summary), title="Summary", expand=False))

    sys.exit(0 if valid else 1)


def _show_section(title: str, content: str) -> None:
    """Render a Markdown section inside a rich Panel."""
    if not content.strip():
        return
    try:
        md = Markdown(content)
        console.print(Panel(md, title=f"[bold]{title}[/bold]", expand=False))
    except Exception:  # noqa: BLE001
        console.print(Panel(content, title=title, expand=False))


def main() -> None:
    """Entry point for the schema-agent CLI."""
    cli()


if __name__ == "__main__":
    main()
