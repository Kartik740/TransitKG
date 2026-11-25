"""Static schema analysis, transformations and Neo4j import."""
from transitkg._workspace import load_legacy

_pinned = ["design_static_graph","transform_static_graph","import_static_graph"]
__all__ = [*_pinned]


def __getattr__(name):
    return load_legacy(name, _pinned)

