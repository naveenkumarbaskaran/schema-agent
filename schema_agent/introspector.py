"""
DbIntrospector: Connect to a database via SQLAlchemy and extract its schema.

Extracts tables, columns (with types, nullability, defaults), foreign keys,
and indexes.
"""

from __future__ import annotations

from typing import Any


class DbIntrospector:
    """
    Database schema introspector backed by SQLAlchemy reflection.

    Parameters
    ----------
    connection_string:
        SQLAlchemy connection string, e.g.::

            'postgresql://user:pass@localhost:5432/mydb'
            'mysql+pymysql://user:pass@localhost/mydb'
            'sqlite:///path/to/db.sqlite'
    """

    def __init__(self, connection_string: str) -> None:
        self._connection_string = connection_string
        self._engine: Any = None
        self._inspector: Any = None

    @property
    def dialect(self) -> str:
        """Return the database dialect name (e.g. 'postgresql', 'sqlite')."""
        if self._engine is None:
            return "unknown"
        return self._engine.dialect.name

    def connect(self) -> None:
        """
        Create the SQLAlchemy engine and test the connection.

        Raises
        ------
        ImportError
            If SQLAlchemy is not installed.
        sqlalchemy.exc.OperationalError
            If the connection cannot be established.
        """
        try:
            from sqlalchemy import create_engine, inspect, text
        except ImportError as exc:
            raise ImportError(
                "SQLAlchemy is required. Install it with: pip install sqlalchemy"
            ) from exc

        self._engine = create_engine(self._connection_string)
        # Test the connection
        with self._engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        self._inspector = inspect(self._engine)

    def introspect(self) -> dict[str, Any]:
        """
        Reflect all tables in the database and return a schema snapshot.

        Returns
        -------
        dict with structure::

            {
              "dialect": "postgresql",
              "tables": {
                "<table_name>": {
                  "columns": [
                    {
                      "name": str,
                      "type": str,
                      "nullable": bool,
                      "default": str | None,
                      "primary_key": bool,
                      "autoincrement": bool
                    },
                    ...
                  ],
                  "primary_key": [str, ...],
                  "foreign_keys": [
                    {
                      "constrained_columns": [str, ...],
                      "referred_schema": str | None,
                      "referred_table": str,
                      "referred_columns": [str, ...],
                      "name": str | None
                    },
                    ...
                  ],
                  "indexes": [
                    {
                      "name": str,
                      "columns": [str, ...],
                      "unique": bool
                    },
                    ...
                  ],
                  "unique_constraints": [
                    {
                      "name": str | None,
                      "columns": [str, ...]
                    },
                    ...
                  ]
                }
              }
            }
        """
        if self._inspector is None:
            raise RuntimeError("Not connected. Call connect() first.")

        schema_data: dict[str, Any] = {
            "dialect": self.dialect,
            "tables": {},
        }

        try:
            table_names: list[str] = self._inspector.get_table_names()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"Failed to list tables: {exc}") from exc

        for table_name in table_names:
            schema_data["tables"][table_name] = self._introspect_table(table_name)

        return schema_data

    def _introspect_table(self, table_name: str) -> dict[str, Any]:
        """Return the full introspection dict for a single table."""
        columns = self._get_columns(table_name)
        pk_cols = self._get_primary_key(table_name)
        foreign_keys = self._get_foreign_keys(table_name)
        indexes = self._get_indexes(table_name)
        unique_constraints = self._get_unique_constraints(table_name)

        return {
            "columns": columns,
            "primary_key": pk_cols,
            "foreign_keys": foreign_keys,
            "indexes": indexes,
            "unique_constraints": unique_constraints,
        }

    def _get_columns(self, table_name: str) -> list[dict[str, Any]]:
        columns: list[dict[str, Any]] = []
        try:
            pk_constraint = self._inspector.get_pk_constraint(table_name)
            pk_cols: set[str] = set(pk_constraint.get("constrained_columns", []))
        except Exception:  # noqa: BLE001
            pk_cols = set()

        for col in self._inspector.get_columns(table_name):
            col_type = col.get("type")
            type_str = str(col_type) if col_type is not None else "UNKNOWN"

            default = col.get("default")
            if default is not None:
                default = str(default)

            columns.append(
                {
                    "name": col["name"],
                    "type": type_str,
                    "nullable": bool(col.get("nullable", True)),
                    "default": default,
                    "primary_key": col["name"] in pk_cols,
                    "autoincrement": bool(col.get("autoincrement", False)),
                }
            )
        return columns

    def _get_primary_key(self, table_name: str) -> list[str]:
        try:
            pk = self._inspector.get_pk_constraint(table_name)
            return pk.get("constrained_columns", [])
        except Exception:  # noqa: BLE001
            return []

    def _get_foreign_keys(self, table_name: str) -> list[dict[str, Any]]:
        fks: list[dict[str, Any]] = []
        try:
            for fk in self._inspector.get_foreign_keys(table_name):
                fks.append(
                    {
                        "name": fk.get("name"),
                        "constrained_columns": fk.get("constrained_columns", []),
                        "referred_schema": fk.get("referred_schema"),
                        "referred_table": fk.get("referred_table", ""),
                        "referred_columns": fk.get("referred_columns", []),
                    }
                )
        except Exception:  # noqa: BLE001
            pass
        return fks

    def _get_indexes(self, table_name: str) -> list[dict[str, Any]]:
        indexes: list[dict[str, Any]] = []
        try:
            for idx in self._inspector.get_indexes(table_name):
                indexes.append(
                    {
                        "name": idx.get("name"),
                        "columns": idx.get("column_names", []),
                        "unique": bool(idx.get("unique", False)),
                    }
                )
        except Exception:  # noqa: BLE001
            pass
        return indexes

    def _get_unique_constraints(self, table_name: str) -> list[dict[str, Any]]:
        ucs: list[dict[str, Any]] = []
        try:
            for uc in self._inspector.get_unique_constraints(table_name):
                ucs.append(
                    {
                        "name": uc.get("name"),
                        "columns": uc.get("column_names", []),
                    }
                )
        except Exception:  # noqa: BLE001
            pass
        return ucs

    def disconnect(self) -> None:
        """Dispose the SQLAlchemy engine and release connections."""
        if self._engine is not None:
            self._engine.dispose()
            self._engine = None
            self._inspector = None

    def __enter__(self) -> "DbIntrospector":
        self.connect()
        return self

    def __exit__(self, *_: Any) -> None:
        self.disconnect()
