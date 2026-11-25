"""Resolve checkout resources and bridge byte-pinned research modules."""
from importlib import import_module
from pathlib import Path
import sys


def workspace_root():
    for parent in Path(__file__).resolve().parents:
        if ((parent / "pyproject.toml").is_file()
                and (parent / "scripts" / "_transitkg_compat.py").is_file()
                and (parent / "schema" / "static_graph_schema.json").is_file()):
            return parent
    raise RuntimeError("TransitKG requires a repository checkout. Use an editable install from that checkout.")


ROOT = workspace_root()
SCRIPTS = ROOT / "scripts"


def enable_legacy_imports():
    """Byte-pinned modules still import siblings by their historical flat names."""
    location = str(SCRIPTS)
    if location not in sys.path:
        sys.path.insert(0, location)


def load_legacy(name, exported):
    if name not in exported:
        raise AttributeError(f"Unknown TransitKG module {name!r}")
    return import_module(name)

