"""Lineage: from a live candidate back to the raw dataset bytes.

The question this answers is the one an operator asks when a paper bot starts
losing money, and the one a regulator asks when anything goes wrong: *where did
this come from?* Not "which strategy file", but the whole chain --

    experiment -> its parent experiments (why it was tried at all)
               -> the strategy document and its mutation ancestry
               -> the dataset version it was validated on
               -> that version's transformation lineage
               -> the raw, unmodified source bytes and their checksum

Nothing here computes anything. It reads the experiment ledger (and, when one is
supplied, the dataset catalogue from :mod:`fiboki.data.versioning`) and assembles
the chain. A gap in the chain is REPORTED as a gap rather than skipped over: a
provenance chain that quietly omits its missing link is worse than no chain,
because it looks complete.

The dataset catalogue is accepted duck-typed (anything with ``lineage_chain``
and ``resolve``) so this module does not force a data-platform import on callers
who only want the experiment half of the graph.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from fiboki.research.experiment import Experiment, ExperimentLedger, ExperimentNotFound

__all__ = [
    "DatasetResolver",
    "LineageEdge",
    "LineageGraph",
    "LineageNode",
    "LineageService",
    "ProvenanceChain",
    "ProvenanceStep",
]


class DatasetResolver(Protocol):
    """The slice of :class:`fiboki.data.versioning.DatasetCatalogue` used here."""

    def lineage_chain(self, version_id: str) -> list[Any]: ...
    def exists(self, version_id: str) -> bool: ...


# --------------------------------------------------------------------------
# Graph
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LineageNode:
    kind: str
    """``experiment`` | ``strategy`` | ``dataset`` | ``raw_source``."""
    id: str
    label: str = ""
    attrs: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.id}"

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "id": self.id, "label": self.label, "attrs": self.attrs}


@dataclass(frozen=True, slots=True)
class LineageEdge:
    parent: str
    """``key`` of the parent node."""
    child: str
    relation: str

    def to_dict(self) -> dict[str, str]:
        return {"parent": self.parent, "child": self.child, "relation": self.relation}


@dataclass(slots=True)
class LineageGraph:
    nodes: dict[str, LineageNode] = field(default_factory=dict)
    edges: list[LineageEdge] = field(default_factory=list)

    def add_node(self, node: LineageNode) -> LineageNode:
        self.nodes.setdefault(node.key, node)
        return self.nodes[node.key]

    def add_edge(self, parent: LineageNode, child: LineageNode, relation: str) -> None:
        self.add_node(parent)
        self.add_node(child)
        edge = LineageEdge(parent.key, child.key, relation)
        if edge not in self.edges:
            self.edges.append(edge)

    def parents_of(self, key: str) -> list[LineageNode]:
        return [self.nodes[e.parent] for e in self.edges if e.child == key]

    def children_of(self, key: str) -> list[LineageNode]:
        return [self.nodes[e.child] for e in self.edges if e.parent == key]

    def ancestors(self, key: str) -> list[LineageNode]:
        """Every node reachable by walking parents. Cycle-safe."""
        seen: set[str] = set()
        out: list[LineageNode] = []
        frontier = [key]
        while frontier:
            current = frontier.pop(0)
            for parent in self.parents_of(current):
                if parent.key in seen:
                    continue
                seen.add(parent.key)
                out.append(parent)
                frontier.append(parent.key)
        return out

    def roots(self) -> list[LineageNode]:
        have_parents = {e.child for e in self.edges}
        return [n for k, n in sorted(self.nodes.items()) if k not in have_parents]

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [n.to_dict() for n in self.nodes.values()],
            "edges": [e.to_dict() for e in self.edges],
        }

    def __len__(self) -> int:
        return len(self.nodes)


# --------------------------------------------------------------------------
# Provenance chain
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProvenanceStep:
    kind: str
    id: str
    summary: str
    detail: dict[str, Any] = field(default_factory=dict)
    missing: bool = False
    """True when this link could not be resolved. Reported, never skipped."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "id": self.id,
            "summary": self.summary,
            "detail": self.detail,
            "missing": self.missing,
        }


@dataclass(frozen=True, slots=True)
class ProvenanceChain:
    """Ordered steps from a candidate back to the raw bytes."""

    steps: tuple[ProvenanceStep, ...] = ()

    @property
    def complete(self) -> bool:
        """True only when every link resolved AND the chain reaches raw source."""
        return bool(self.steps) and not self.gaps and self.steps[-1].kind == "raw_source"

    @property
    def gaps(self) -> tuple[ProvenanceStep, ...]:
        return tuple(s for s in self.steps if s.missing)

    def describe(self) -> str:
        lines = []
        for i, step in enumerate(self.steps):
            mark = "  !! " if step.missing else "     "
            lines.append(f"{i}.{mark}{step.kind}: {step.summary}")
        if not self.complete:
            lines.append(
                "     INCOMPLETE: this candidate cannot be traced to raw source "
                "bytes. Treat its numbers as unverified."
            )
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "complete": self.complete,
            "n_steps": len(self.steps),
            "gaps": [g.to_dict() for g in self.gaps],
            "steps": [s.to_dict() for s in self.steps],
        }


# --------------------------------------------------------------------------
# Service
# --------------------------------------------------------------------------


class LineageService:
    """Assembles lineage from the experiment ledger and the dataset catalogue."""

    def __init__(
        self, ledger: ExperimentLedger, datasets: DatasetResolver | None = None
    ) -> None:
        self.ledger = ledger
        self.datasets = datasets

    # ----------------------------------------------------------- experiments

    def experiment_ancestry(self, experiment_id: str) -> list[Experiment]:
        """The experiment and its parents, OLDEST first. Cycle-safe."""
        chain: list[Experiment] = []
        seen: set[str] = set()
        current: str | None = str(experiment_id)
        while current:
            if current in seen:
                break
            seen.add(current)
            try:
                exp = self.ledger.get(current)
            except ExperimentNotFound:
                break
            chain.append(exp)
            current = exp.parent_experiment_id or None
        return list(reversed(chain))

    def descendants(self, experiment_id: str) -> list[Experiment]:
        """Every experiment that descends from this one, breadth-first."""
        out: list[Experiment] = []
        seen: set[str] = {str(experiment_id)}
        frontier = [str(experiment_id)]
        while frontier:
            current = frontier.pop(0)
            for child in self.ledger.children(current):
                if child.id in seen:
                    continue
                seen.add(child.id)
                out.append(child)
                frontier.append(child.id)
        return out

    # ------------------------------------------------------------ strategies

    def produced_by(self, strategy_content_hash: str) -> Experiment | None:
        """The FIRST experiment that ran this exact strategy content."""
        found = self.ledger.list(strategy_content_hash=str(strategy_content_hash))
        return found[0] if found else None

    def strategy_ancestry(self, strategy_content_hash: str) -> list[dict[str, Any]]:
        """Mutation ancestry of a strategy, oldest first.

        Walks ``MutationRecord.parent_hash`` through the documents stored on the
        ledger, so it works even when the parent document's file has since been
        deleted -- the ledger is the record, not the filesystem.
        """
        chain: list[dict[str, Any]] = []
        seen: set[str] = set()
        current = str(strategy_content_hash)
        while current and current not in seen:
            seen.add(current)
            exp = self.produced_by(current)
            doc = exp.strategy_document if exp is not None else None
            mutation = (doc or {}).get("mutation") or {}
            chain.append(
                {
                    "content_hash": current,
                    "strategy_id": (exp.strategy_id if exp else ""),
                    "experiment_id": (exp.id if exp else ""),
                    "operator": mutation.get("operator", ""),
                    "generation": mutation.get("generation", 0),
                    "description": mutation.get("description", ""),
                    "known_to_ledger": exp is not None,
                }
            )
            current = str(mutation.get("parent_hash") or "")
        return list(reversed(chain))

    # ------------------------------------------------------------ provenance

    def provenance_chain(self, experiment_id: str) -> ProvenanceChain:
        """From this experiment back to the raw dataset bytes."""
        steps: list[ProvenanceStep] = []
        ancestry = self.experiment_ancestry(experiment_id)
        if not ancestry:
            return ProvenanceChain(
                (
                    ProvenanceStep(
                        kind="experiment",
                        id=str(experiment_id),
                        summary=f"experiment {experiment_id} is not in this ledger",
                        missing=True,
                    ),
                )
            )

        leaf = ancestry[-1]
        steps.append(
            ProvenanceStep(
                kind="experiment",
                id=leaf.id,
                summary=leaf.describe(),
                detail={
                    "actor": f"{leaf.actor_kind.value}:{leaf.actor_name}",
                    "reason": leaf.reason,
                    "code_version": leaf.code_version,
                    "validation_report_hash": leaf.validation_report_hash,
                    "n_ancestor_experiments": len(ancestry) - 1,
                    "ancestor_ids": [e.id for e in ancestry[:-1]],
                },
            )
        )
        steps.append(
            ProvenanceStep(
                kind="strategy",
                id=leaf.strategy_content_hash or leaf.strategy_id,
                summary=(
                    f"strategy {leaf.strategy_id or '?'} "
                    f"[{(leaf.strategy_content_hash or 'unhashed')[:12]}] "
                    f"structure {(leaf.structure_hash or 'unknown')[:12]}"
                ),
                detail={
                    "content_hash": leaf.strategy_content_hash,
                    "structure_hash": leaf.structure_hash,
                    "mutation_ancestry": self.strategy_ancestry(leaf.strategy_content_hash)
                    if leaf.strategy_content_hash
                    else [],
                },
                missing=not leaf.strategy_content_hash,
            )
        )

        dataset_id = leaf.dataset_version_id
        if not dataset_id:
            steps.append(
                ProvenanceStep(
                    kind="dataset",
                    id="",
                    summary="the experiment records no dataset version",
                    missing=True,
                )
            )
            return ProvenanceChain(tuple(steps))

        if self.datasets is None:
            steps.append(
                ProvenanceStep(
                    kind="dataset",
                    id=dataset_id,
                    summary=(
                        f"dataset version {dataset_id} (no catalogue supplied, so its "
                        "transformation lineage could not be resolved)"
                    ),
                    missing=True,
                )
            )
            return ProvenanceChain(tuple(steps))

        try:
            versions = list(self.datasets.lineage_chain(dataset_id))
        except Exception as exc:
            steps.append(
                ProvenanceStep(
                    kind="dataset",
                    id=dataset_id,
                    summary=f"dataset version {dataset_id} could not be resolved: {exc}",
                    missing=True,
                )
            )
            return ProvenanceChain(tuple(steps))

        # ``lineage_chain`` returns root-first; provenance reads newest-first.
        for version in reversed(versions):
            steps.append(
                ProvenanceStep(
                    kind="dataset",
                    id=str(getattr(version, "version_id", "")),
                    summary=_describe_version(version),
                    detail={
                        "content_checksum": getattr(version, "content_checksum", ""),
                        "instrument": _value(getattr(version, "instrument", "")),
                        "timeframe": _value(getattr(version, "timeframe", "")),
                        "quality": _value(getattr(version, "quality", "")),
                        "row_count": getattr(version, "row_count", None),
                        "lineage": [
                            _value(getattr(step, "operation", ""))
                            for step in getattr(version, "lineage", ())
                        ],
                    },
                )
            )
        root = versions[0] if versions else None
        if root is not None:
            steps.append(
                ProvenanceStep(
                    kind="raw_source",
                    id=str(getattr(root, "content_checksum", "")),
                    summary=(
                        f"raw source {_value(getattr(root, 'source', 'unknown'))} "
                        f"checksum {str(getattr(root, 'content_checksum', ''))[:16]} "
                        f"at {getattr(root, 'storage_path', '?')}"
                    ),
                    detail={
                        "source": _value(getattr(root, "source", "")),
                        "storage_path": str(getattr(root, "storage_path", "")),
                        "content_checksum": str(getattr(root, "content_checksum", "")),
                    },
                    missing=not getattr(root, "content_checksum", ""),
                )
            )
        return ProvenanceChain(tuple(steps))

    # ----------------------------------------------------------------- graph

    def graph(self, experiments: Iterable[Experiment] | None = None) -> LineageGraph:
        """Experiment / strategy / dataset graph over the given experiments."""
        rows: Sequence[Experiment] = (
            list(experiments) if experiments is not None else self.ledger.list()
        )
        graph = LineageGraph()
        by_id = {e.id: e for e in rows}
        for exp in rows:
            node = graph.add_node(
                LineageNode(
                    kind="experiment",
                    id=exp.id,
                    label=exp.describe(),
                    attrs={
                        "outcome": exp.outcome.value,
                        "actor": f"{exp.actor_kind.value}:{exp.actor_name}",
                        "created_at": exp.created_at.isoformat(),
                    },
                )
            )
            if exp.parent_experiment_id and exp.parent_experiment_id in by_id:
                parent = by_id[exp.parent_experiment_id]
                graph.add_edge(
                    LineageNode("experiment", parent.id, parent.describe()),
                    node,
                    "followed_from",
                )
            if exp.strategy_content_hash:
                strategy = LineageNode(
                    kind="strategy",
                    id=exp.strategy_content_hash,
                    label=f"{exp.strategy_id} [{exp.strategy_content_hash[:12]}]",
                    attrs={"structure_hash": exp.structure_hash},
                )
                graph.add_edge(node, strategy, "evaluated")
            if exp.dataset_version_id:
                dataset = LineageNode(
                    kind="dataset",
                    id=exp.dataset_version_id,
                    label=exp.dataset_version_id,
                )
                graph.add_edge(dataset, node, "data_for")
        return graph


def _value(item: Any) -> Any:
    return getattr(item, "value", item)


def _describe_version(version: Any) -> str:
    describe = getattr(version, "describe", None)
    if callable(describe):
        try:
            return str(describe())
        except Exception:
            pass
    return str(getattr(version, "version_id", version))
