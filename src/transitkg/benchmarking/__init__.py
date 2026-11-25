"""Regional workloads, timing and benchmark reporting."""
from importlib import import_module
from transitkg._workspace import load_legacy

_pinned = ["benchmark_static_graph","static_graph_workloads"]
__all__ = [*_pinned, "report_static_graph_benchmark"]


def __getattr__(name):
    if name == "report_static_graph_benchmark":
        return import_module(f"{__name__}.{name}")
    return load_legacy(name, _pinned)

