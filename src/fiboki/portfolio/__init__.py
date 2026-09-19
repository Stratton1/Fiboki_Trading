"""Portfolio layer: turns opinions into exactly one size, once.

``sizing`` is the single sizing authority. ``construction`` decides how much of
the risk budget each concurrent candidate is entitled to. Nothing downstream of
this package may re-decide a size.
"""
