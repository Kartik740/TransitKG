"""Compatibility launcher for transitkg.validation.verify_phase4_neo4j."""
if __package__:
    from ._transitkg_compat import forward
else:
    from _transitkg_compat import forward

forward(__name__, "transitkg.validation.verify_phase4_neo4j")
