"""GTFS cleaning, versioned normalization and reports."""
from importlib import import_module
from transitkg._workspace import load_legacy

_pinned = ["normalize_gtfs","gtfs_cleaning","gtfs_normalized"]
__all__ = [*_pinned, "gtfs_phase3_report"]


def __getattr__(name):
    if name == "gtfs_phase3_report":
        return import_module(f"{__name__}.{name}")
    return load_legacy(name, _pinned)

