"""Risk layer: the mandatory gateway, the kill switch and the versioned limits.

Every order in every mode passes through :mod:`fiboki.risk.gateway`. There is
no bypass path, and ``tests/unit/test_no_gateway_bypass.py`` proves it over the
AST of the whole source tree.
"""
