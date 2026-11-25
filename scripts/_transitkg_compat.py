"""Bridge existing scripts and unittest discovery to the checkout layout."""
from importlib import import_module
from importlib.util import find_spec
from pathlib import Path
import runpy
import sys


def bootstrap():
    root = Path(__file__).resolve().parent.parent
    source = root / "src"
    if not (source / "transitkg" / "__init__.py").is_file():
        raise RuntimeError("The TransitKG source tree must be beside scripts/ in the checkout")
    for location in (root, source):
        path = str(location)
        if path not in sys.path:
            sys.path.insert(0, path)
    from transitkg._workspace import enable_legacy_imports

    enable_legacy_imports()


def run_script(target):
    # A file launch preserves the historic argparse program name and exits.
    runpy.run_path(find_spec(target).origin, run_name="__main__")


def forward(name, target):
    bootstrap()
    if name == "__main__":
        run_script(target)
    else:
        sys.modules[name] = import_module(target)


def test_hooks(name, target):
    bootstrap()
    if name == "__main__":
        run_script(target)
    else:
        real_module = import_module(target)

        def load_tests(loader, standard_tests, pattern):
            return loader.loadTestsFromModule(real_module)

        return load_tests, lambda attribute: getattr(real_module, attribute)
    return None, None
