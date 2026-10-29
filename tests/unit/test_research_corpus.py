"""The reading corpus fails closed when a line has no licence."""
from __future__ import annotations

import hashlib
import json

import pytest
from typer.testing import CliRunner

from fiboki.cli import app
from fiboki.research.corpus import CorpusManifestError, load_manifest


def _line(**extra: str) -> str:
    body = {
        "title": "A public-domain note",
        "source_url": "https://example.test/note",
        "retrieved_at": "2026-10-19T00:00:00Z",
        "licence": "cc0",
        "sha256": "abc",
    }
    body.update(extra)
    return json.dumps(body)


def test_a_line_without_a_licence_is_refused(tmp_path) -> None:
    path = tmp_path / "manifest.jsonl"
    raw = json.loads(_line())
    del raw["licence"]
    path.write_text(json.dumps(raw) + "\n", encoding="utf-8")
    with pytest.raises(CorpusManifestError, match="licence is required"):
        load_manifest(path)


def test_an_unknown_licence_is_refused(tmp_path) -> None:
    path = tmp_path / "manifest.jsonl"
    path.write_text(_line(licence="all-rights-reserved") + "\n", encoding="utf-8")
    with pytest.raises(CorpusManifestError, match="refused"):
        load_manifest(path)


def test_an_allowed_line_loads(tmp_path) -> None:
    path = tmp_path / "manifest.jsonl"
    path.write_text(_line() + "\n", encoding="utf-8")
    (entry,) = load_manifest(path)
    assert entry.licence == "cc0"
    assert entry.title == "A public-domain note"


def test_a_missing_manifest_is_not_an_empty_corpus(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        load_manifest(tmp_path / "missing.jsonl")


def test_cli_refuses_a_missing_manifest_unknown_licence_and_a_bad_hash(tmp_path) -> None:
    runner = CliRunner()
    missing = runner.invoke(app, ["corpus", "verify", "--manifest", str(tmp_path / "nope.jsonl")])
    assert missing.exit_code != 0

    unknown = tmp_path / "unknown.jsonl"
    unknown.write_text(_line(licence="all-rights-reserved") + "\n", encoding="utf-8")
    refused = runner.invoke(app, ["corpus", "verify", "--manifest", str(unknown)])
    assert refused.exit_code != 0

    payload = tmp_path / "note.txt"
    payload.write_bytes(b"public domain note")
    digest = hashlib.sha256(b"public domain note").hexdigest()
    good = tmp_path / "good.jsonl"
    good.write_text(_line(sha256=digest, path="note.txt") + "\n", encoding="utf-8")
    ok = runner.invoke(app, ["corpus", "verify", "--manifest", str(good)])
    assert ok.exit_code == 0
    assert "cc0" in ok.stdout
    assert digest in ok.stdout

    bad = tmp_path / "bad.jsonl"
    bad.write_text(_line(sha256="0" * 64, path="note.txt") + "\n", encoding="utf-8")
    mismatch = runner.invoke(app, ["corpus", "verify", "--manifest", str(bad)])
    assert mismatch.exit_code != 0

    publisher = tmp_path / "publisher.jsonl"
    publisher.write_text(_line(licence="publisher-research") + "\n", encoding="utf-8")
    terms = runner.invoke(app, ["corpus", "verify", "--manifest", str(publisher)])
    assert terms.exit_code != 0
