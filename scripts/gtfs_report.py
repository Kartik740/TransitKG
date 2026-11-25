"""Compatibility import for transitkg.profiling.gtfs_report."""
if __package__:
    from ._transitkg_compat import forward
else:
    from _transitkg_compat import forward

forward(__name__, "transitkg.profiling.gtfs_report")
