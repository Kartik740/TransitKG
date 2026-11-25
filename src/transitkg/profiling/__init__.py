"""GTFS discovery, integrity profiling and reports."""
from importlib import import_module
from transitkg._workspace import load_legacy

_pinned = ["profile_gtfs","gtfs_checks","gtfs_network"]
__all__ = [*_pinned, "gtfs_report"]


def __getattr__(name):
    if name == "gtfs_report":
        return import_module(f"{__name__}.{name}")
    return load_legacy(name, _pinned)

