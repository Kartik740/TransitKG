"""Compatibility launcher and discovery hook for tests.test_graph_ready."""
if __package__:
    from ._transitkg_compat import test_hooks
else:
    from _transitkg_compat import test_hooks

load_tests, __getattr__ = test_hooks(__name__, "tests.test_graph_ready")
