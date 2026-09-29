"""The run manifest: one hash for everything that shapes an agent's behaviour.

A research cycle's output depends on more than the model.  It depends on the
system prompt every role was given, on the tool schemas those roles could
call, on the capability vocabulary, and on the numerical libraries the
deterministic worker ran.  Change any of those and two runs with the same
model and the same question are no longer the same experiment.

:func:`build_run_manifest` hashes each of those components separately, then
hashes the sorted set of component digests into one ``RunManifest.hash``.
Every workflow stamps that hash on its start record (and every session stamps
it on every record it writes), so "which prompts produced this critique?" is
answered by the ledger, not by memory.  The components are kept alongside the
hash, so two manifests can be diffed to see WHICH component moved.

Pattern after Vibe-Trading ``governance/manifest.py`` (MIT), written fresh.

What the hash does NOT cover, stated so it is not assumed:

* the model weights (pinned separately, per record, by ``model_digest``);
* uncommitted edits outside the hashed components: ``git_head`` names the
  commit, not the working tree.  The prompts and schemas that matter are
  hashed from the live objects, so an uncommitted prompt edit DOES move the
  hash; an uncommitted edit to, say, the engine does not;
* libraries other than the four named in :data:`PINNED_PACKAGES`.

Read-only: this module reads role specs, the tool registry and package
metadata.  It imports nothing that can write.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path
from typing import Any

from fiboki.agents.capabilities import Capability
from fiboki.agents.roles import RoleSpec, all_roles
from fiboki.agents.tools import REGISTRY, ToolRegistry

#: Version of the manifest's own layout.  Part of the hash.
MANIFEST_SCHEMA = "run-manifest:1"

#: Libraries whose version changes a deterministic result.
PINNED_PACKAGES: tuple[str, ...] = ("numpy", "pandas", "scipy", "pydantic")

#: ``git_head`` when no repository can be found (an installed wheel).
GIT_UNAVAILABLE = "unavailable"


def _sha256(value: Any) -> str:
    blob = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class RunManifest:
    """``hash`` over ``components``; each component is a short string."""

    hash: str
    components: Mapping[str, str] = field(default_factory=dict)
    schema: str = MANIFEST_SCHEMA

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "hash": self.hash,
            "components": dict(sorted(self.components.items())),
        }

    def diff(self, other: RunManifest) -> dict[str, tuple[str | None, str | None]]:
        """Components that differ, as ``name -> (mine, theirs)``."""
        names = set(self.components) | set(other.components)
        return {
            name: (self.components.get(name), other.components.get(name))
            for name in sorted(names)
            if self.components.get(name) != other.components.get(name)
        }


def manifest_components(
    *,
    roles: Iterable[RoleSpec] | None = None,
    registry: ToolRegistry | None = None,
    packages: Iterable[str] = PINNED_PACKAGES,
    git_root: Path | None = None,
) -> dict[str, str]:
    """The named inputs to the manifest hash.  Deterministic for fixed inputs."""
    reg = registry or REGISTRY
    components: dict[str, str] = {}
    for spec in sorted(roles if roles is not None else all_roles(), key=lambda s: s.role.value):
        components[f"role_prompt:{spec.role.value}"] = _sha256(spec.system_prompt(reg))
    for tool in reg.all():
        components[f"tool_schema:{tool.name}"] = _sha256(
            {
                "name": tool.name,
                "input_schema": tool.input_model.model_json_schema(),
                "output_schema": tool.output_model.model_json_schema(),
            }
        )
    components["capability_enum"] = _sha256(sorted(c.value for c in Capability))
    for name in packages:
        try:
            components[f"package:{name}"] = metadata.version(name)
        except metadata.PackageNotFoundError:
            components[f"package:{name}"] = "not-installed"
    components["git_head"] = git_head(git_root)
    return components


def build_run_manifest(
    *,
    roles: Iterable[RoleSpec] | None = None,
    registry: ToolRegistry | None = None,
    packages: Iterable[str] = PINNED_PACKAGES,
    git_root: Path | None = None,
) -> RunManifest:
    """Hash every role prompt, tool schema, the capability enum, library
    versions and the git HEAD into one :class:`RunManifest`."""
    components = manifest_components(
        roles=roles, registry=registry, packages=packages, git_root=git_root
    )
    digest = _sha256({"schema": MANIFEST_SCHEMA, "components": components})
    return RunManifest(hash=digest, components=components)


# ---------------------------------------------------------------------------
# git HEAD, read from the files (no subprocess: this package may not spawn)
# ---------------------------------------------------------------------------


def _find_git_dir(start: Path) -> Path | None:
    for candidate in (start, *start.parents):
        dotgit = candidate / ".git"
        if dotgit.is_dir():
            return dotgit
        if dotgit.is_file():
            # A worktree or submodule: ".git" is a file naming the real directory.
            text = dotgit.read_text(encoding="utf-8").strip()
            if text.startswith("gitdir:"):
                target = Path(text.split(":", 1)[1].strip())
                return target if target.is_absolute() else (candidate / target).resolve()
    return None


def git_head(root: Path | None = None) -> str:
    """The commit HEAD points at, or :data:`GIT_UNAVAILABLE`.

    Resolves a symbolic ref through loose refs, then ``packed-refs``; for a
    worktree, also through the ``commondir`` it names.  Never raises: a
    manifest built outside a checkout records that fact rather than failing.
    """
    try:
        git_dir = _find_git_dir((root or Path(__file__).resolve().parent).resolve())
        if git_dir is None:
            return GIT_UNAVAILABLE
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
        if not head.startswith("ref:"):
            return head if len(head) >= 40 else GIT_UNAVAILABLE
        ref = head.split(":", 1)[1].strip()
        search = [git_dir]
        commondir = git_dir / "commondir"
        if commondir.is_file():
            common = Path(commondir.read_text(encoding="utf-8").strip())
            search.append(common if common.is_absolute() else (git_dir / common).resolve())
        for base in search:
            loose = base / ref
            if loose.is_file():
                return loose.read_text(encoding="utf-8").strip()
        for base in search:
            packed = base / "packed-refs"
            if packed.is_file():
                for line in packed.read_text(encoding="utf-8").splitlines():
                    parts = line.strip().split(" ")
                    if len(parts) == 2 and parts[1] == ref:
                        return parts[0]
    except OSError:
        return GIT_UNAVAILABLE
    return GIT_UNAVAILABLE


__all__ = [
    "GIT_UNAVAILABLE",
    "MANIFEST_SCHEMA",
    "PINNED_PACKAGES",
    "RunManifest",
    "build_run_manifest",
    "git_head",
    "manifest_components",
]
