"""Compatibility import for transitkg.normalization.gtfs_phase3_report."""
if __package__:
    from ._transitkg_compat import forward
else:
    from _transitkg_compat import forward

forward(__name__, "transitkg.normalization.gtfs_phase3_report")
