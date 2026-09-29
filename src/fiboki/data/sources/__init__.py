"""The source registry: every external data source, its cost and its terms.

See :mod:`fiboki.data.sources.registry`. ``describe_sources()`` is the one
listing the API and the docs read.
"""
from __future__ import annotations

from fiboki.data.sources.registry import (
    SOURCE_REGISTRY,
    SourceEntry,
    SourceKind,
    TermsStatus,
    describe_sources,
    registry_markdown,
    source,
)

__all__ = [
    "SOURCE_REGISTRY",
    "SourceEntry",
    "SourceKind",
    "TermsStatus",
    "describe_sources",
    "registry_markdown",
    "source",
]
