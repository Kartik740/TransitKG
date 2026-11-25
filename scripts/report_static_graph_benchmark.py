"""Compatibility launcher for transitkg.benchmarking.report_static_graph_benchmark."""
if __package__:
    from ._transitkg_compat import forward
else:
    from _transitkg_compat import forward

forward(__name__, "transitkg.benchmarking.report_static_graph_benchmark")
