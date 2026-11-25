"""Graph-ready validation, Neo4j readback and regional auditing."""
from importlib import import_module
from transitkg._workspace import load_legacy

_pinned = ["validate_graph_ready","verify_imported_graph","audit_static_graph"]
__all__ = [*_pinned, "verify_phase4_neo4j"]


def __getattr__(name):
    if name == "verify_phase4_neo4j":
        return import_module(f"{__name__}.{name}")
    return load_legacy(name, _pinned)

