"""A licence-gated reading list for the librarian. Not a training set.

The manifest is JSONL under :attr:`fiboki.core.paths.FibokiPaths.corpus_dir`.
Each line names a source URL, a retrieval time, a licence, a sha256 and a
title. A line without a licence is refused. Unknown terms are a refusal.

This module does not download files, does not update model weights, and does
not write the experiment ledger. Chunks, when a later pass reads them, are
notes for the librarian. They are not instructions to trade. Holdout text,
IMF bulk text, Coinbase text meant for training, and scraped calendar or
news-aggregator extracts are not accepted licences.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "ALLOWED_LICENCES",
    "CorpusEntry",
    "CorpusManifestError",
    "load_manifest",
]

#: Licences a line may name. ``publisher-research`` also requires ``terms_url``,
#: the page where that publisher allows local research storage.
ALLOWED_LICENCES = frozenset(
    {"public-domain", "cc0", "cc-by", "cc-by-sa", "publisher-research"}
)

_REQUIRED = ("title", "source_url", "retrieved_at", "sha256")


class CorpusManifestError(ValueError):
    """A manifest line is missing a licence or names one this loader refuses."""


@dataclass(frozen=True, slots=True)
class CorpusEntry:
    title: str
    source_url: str
    retrieved_at: str
    licence: str
    sha256: str
    terms_url: str
    path: str = ""


def load_manifest(path: str | Path) -> tuple[CorpusEntry, ...]:
    """Read a manifest. Raises :class:`CorpusManifestError` on a bad line.

    A missing file is :class:`FileNotFoundError`: absence is not an empty
    corpus.
    """
    manifest = Path(path)
    text = manifest.read_text(encoding="utf-8")
    entries: list[CorpusEntry] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CorpusManifestError(f"{manifest}:{lineno}: {exc}") from exc
        if not isinstance(raw, Mapping):
            raise CorpusManifestError(f"{manifest}:{lineno}: a line must be an object")
        entries.append(_entry(manifest, lineno, raw))
    return tuple(entries)


def _entry(manifest: Path, lineno: int, raw: Mapping[str, object]) -> CorpusEntry:
    licence = str(raw.get("licence") or "").strip().lower()
    if not licence:
        raise CorpusManifestError(
            f"{manifest}:{lineno}: licence is required. Unknown terms are a refusal. "
            "Refused outright: in-copyright scans, aggregator extracts, IMF bulk text, "
            "Coinbase text for training, and holdout text."
        )
    if licence not in ALLOWED_LICENCES:
        raise CorpusManifestError(
            f"{manifest}:{lineno}: licence {licence!r} is refused. "
            f"Allowed: {', '.join(sorted(ALLOWED_LICENCES))}."
        )
    terms_url = str(raw.get("terms_url") or "").strip()
    if licence == "publisher-research" and not terms_url:
        raise CorpusManifestError(
            f"{manifest}:{lineno}: publisher-research requires terms_url, "
            "the page that allows local research storage."
        )
    fields: dict[str, str] = {}
    for key in _REQUIRED:
        value = str(raw.get(key) or "").strip()
        if not value:
            raise CorpusManifestError(f"{manifest}:{lineno}: {key} is required")
        fields[key] = value
    return CorpusEntry(
        title=fields["title"],
        source_url=fields["source_url"],
        retrieved_at=fields["retrieved_at"],
        licence=licence,
        sha256=fields["sha256"],
        terms_url=terms_url,
        path=str(raw.get("path") or "").strip(),
    )


def verify_bytes(manifest: str | Path, entries: tuple[CorpusEntry, ...]) -> None:
    """Hash each line that names a ``path``. A line with no file is not a match."""
    root = Path(manifest).parent
    for entry in entries:
        if not entry.path:
            continue
        file = Path(entry.path)
        if not file.is_absolute():
            file = root / file
        if not file.is_file():
            raise CorpusManifestError(f"{entry.title}: file named by path is missing ({file.name})")
        digest = hashlib.sha256(file.read_bytes()).hexdigest()
        if digest != entry.sha256:
            raise CorpusManifestError(f"{entry.title}: sha256 mismatch")
