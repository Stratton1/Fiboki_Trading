"""The run manifest: what it covers, that it moves when they move, and only then.

Plus the audit-record provenance fields it is stamped into, and the proof that
adding those fields left every existing record's hash where it was.
"""
from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy
import pytest

from fiboki.agents import roles as roles_module
from fiboki.agents.audit import (
    ActionKind,
    AuditRecord,
    JsonlAuditLedger,
    Outcome,
)
from fiboki.agents.manifest import (
    GIT_UNAVAILABLE,
    build_run_manifest,
    git_head,
    manifest_components,
)
from fiboki.agents.roles import AgentRole, all_roles
from fiboki.agents.tools import REGISTRY, ToolRegistry

ROOT = Path(__file__).resolve().parents[2]


def test_identical_inputs_give_an_identical_hash() -> None:
    a, b = build_run_manifest(), build_run_manifest()
    assert a.hash == b.hash
    assert a.components == b.components
    assert len(a.hash) == 64


def test_the_manifest_covers_every_role_every_tool_the_enum_and_the_pins() -> None:
    components = build_run_manifest().components
    for spec in all_roles():
        assert f"role_prompt:{spec.role.value}" in components
    for name in REGISTRY.names():
        assert f"tool_schema:{name}" in components
    assert "capability_enum" in components
    assert components["package:numpy"] == numpy.__version__
    for package in ("pandas", "scipy", "pydantic"):
        assert components[f"package:{package}"] not in ("", "not-installed")
    assert "git_head" in components


def test_changing_the_shared_prompt_paragraph_changes_every_role_and_the_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = build_run_manifest()
    monkeypatch.setattr(
        roles_module, "CARDINAL_RULE", roles_module.CARDINAL_RULE.replace("plainly", "clearly")
    )
    after = build_run_manifest()
    assert after.hash != before.hash
    changed = set(after.diff(before))
    assert changed == {f"role_prompt:{s.role.value}" for s in all_roles()}


def test_changing_one_role_prompt_changes_only_that_component() -> None:
    specs = list(all_roles())
    edited = [
        dataclasses.replace(s, remit=s.remit + " Also this.")
        if s.role is AgentRole.ADVERSARIAL_QUANT_CRITIC else s
        for s in specs
    ]
    before, after = build_run_manifest(roles=specs), build_run_manifest(roles=edited)
    assert after.hash != before.hash
    assert set(after.diff(before)) == {"role_prompt:adversarial_quant_critic"}


def test_a_different_tool_registry_changes_the_hash() -> None:
    trimmed = ToolRegistry()
    for spec in REGISTRY.all():
        if spec.name != "search_web":
            trimmed.register(spec)
    roles = [s for s in all_roles() if "search_web" not in s.tools]
    full = build_run_manifest(roles=roles)
    smaller = build_run_manifest(roles=roles, registry=trimmed)
    assert smaller.hash != full.hash
    assert "tool_schema:search_web" in full.diff(smaller)


def test_the_hash_is_identical_in_a_fresh_process() -> None:
    code = "from fiboki.agents.manifest import build_run_manifest; print(build_run_manifest().hash)"
    env = {**os.environ, "FIBOKEI_WORKER_EXTERNAL": "true"}
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True,
        cwd=ROOT, env=env,
    )
    assert out.stdout.strip() == build_run_manifest().hash


def test_the_hash_does_not_depend_on_component_order() -> None:
    manifest = build_run_manifest()
    reordered = dict(reversed(list(manifest_components().items())))
    assert json.dumps(reordered, sort_keys=True) == json.dumps(
        dict(manifest.components), sort_keys=True
    )


# ------------------------------------------------------------- git HEAD


def _git(tmp: Path, head: str, **files: str) -> Path:
    git = tmp / ".git"
    git.mkdir(parents=True)
    (git / "HEAD").write_text(head + "\n")
    for rel, content in files.items():
        path = git / rel.replace("__", "/")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return tmp


def test_git_head_reads_a_loose_ref(tmp_path: Path) -> None:
    root = _git(tmp_path, "ref: refs/heads/v2/integration",
                refs__heads__v2__integration="1" * 40 + "\n")
    assert git_head(root / "src") == "1" * 40


def test_git_head_reads_packed_refs_and_a_detached_head(tmp_path: Path) -> None:
    root = _git(tmp_path / "a", "ref: refs/heads/main",
                **{"packed-refs": f"# pack-refs\n{'2' * 40} refs/heads/main\n"})
    assert git_head(root) == "2" * 40
    detached = _git(tmp_path / "b", "3" * 40)
    assert git_head(detached) == "3" * 40


def test_git_head_outside_a_checkout_says_so(tmp_path: Path) -> None:
    lonely = tmp_path / "x"
    lonely.mkdir()
    # tmp_path has no .git above it on any sane CI runner; guard anyway.
    if any((p / ".git").exists() for p in (lonely, *lonely.parents)):
        pytest.skip("tmp_path is inside a git checkout")
    assert git_head(lonely) == GIT_UNAVAILABLE


# ------------------------------------------ audit record provenance fields

#: The hashed payload keys of every record written before Wave 2.
LEGACY_KEYS = {
    "action_id", "sequence", "recorded_at", "agent_id", "role", "kind", "tool",
    "capability", "inputs", "outputs", "reason", "prompt", "parent_action_id",
    "workflow_id", "model", "model_version", "provider", "prompt_tokens",
    "completion_tokens", "cost_usd", "wall_ms", "outcome", "error", "previous_hash",
}


def _record(**extra: object) -> AuditRecord:
    return AuditRecord(
        agent_id="a", role="r", kind=ActionKind.TOOL_CALL, tool="t", inputs={"x": 1},
        outputs={}, reason="why", outcome=Outcome.OK, **extra,  # type: ignore[arg-type]
    )


def test_a_record_without_provenance_hashes_exactly_as_before() -> None:
    sealed = _record().sealed(sequence=0, previous_hash="0" * 64)
    assert set(sealed.payload(with_hash=False)) == LEGACY_KEYS


def test_provenance_fields_are_hashed_when_present_and_round_trip(tmp_path: Path) -> None:
    ledger = JsonlAuditLedger(tmp_path / "audit.jsonl")
    legacy = ledger.append(_record())
    stamped = ledger.append(
        _record(model_id="m", model_digest="sha256:" + "d" * 64, manifest_hash="h" * 64)
    )
    assert set(stamped.payload(with_hash=False)) == LEGACY_KEYS | {
        "model_id", "model_digest", "manifest_hash"
    }
    reloaded = JsonlAuditLedger(tmp_path / "audit.jsonl")
    assert reloaded.verify_chain()
    old, new = reloaded.records()
    assert old.record_hash == legacy.record_hash
    assert (old.model_id, old.model_digest, old.manifest_hash) == (None, None, None)
    assert (new.model_id, new.manifest_hash) == ("m", "h" * 64)


def test_a_doctored_manifest_hash_breaks_the_chain(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    ledger = JsonlAuditLedger(path)
    ledger.append(_record(model_id="m", manifest_hash="h" * 64))
    line = json.loads(path.read_text())
    line["manifest_hash"] = "e" * 64
    path.write_text(json.dumps(line, sort_keys=True, separators=(",", ":")) + "\n")
    assert not JsonlAuditLedger(path).verify_chain()
