"""schema-agent: Infer, document, and validate database schemas using LLMs."""

from .agent import SchemaAgent
from .introspector import DbIntrospector

__all__ = ["SchemaAgent", "DbIntrospector"]
__version__ = "0.1.0"
