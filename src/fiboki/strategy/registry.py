"""Strategy registry: content-hash deduplication plus a health check.

A search process generates thousands of documents, most of them small variations
on each other. Registering by content hash means an identical strategy proposed
twice is recognised as the same strategy once, no matter what it was named -- and
the health check surfaces the classes of mistake that V1 only discovered months
later in production.
"""
from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from fiboki.core.enums import AssetClass
from fiboki.core.instruments import get as get_instrument
from fiboki.indicators.registry import VOLUME_INDICATORS
from fiboki.strategy.compiler import CompilationError, CompiledStrategy, compile_strategy
from fiboki.strategy.dsl import StrategyDocument

#: A warmup beyond this many bars costs a meaningful slice of any backtest.
MAX_SANE_WARMUP = 1000
#: Above this, a document has more knobs than a 2-year sample can support.
COMPLEXITY_WARN_AT = 14.0


class DuplicateStrategyError(ValueError):
    """A different strategy_id already holds this exact content hash."""


@dataclass(frozen=True, slots=True)
class HealthIssue:
    strategy_id: str
    severity: str  # "error" | "warning"
    code: str
    detail: str


@dataclass(frozen=True, slots=True)
class HealthReport:
    issues: tuple[HealthIssue, ...] = ()
    checked: int = 0

    @property
    def errors(self) -> tuple[HealthIssue, ...]:
        return tuple(i for i in self.issues if i.severity == "error")

    @property
    def warnings(self) -> tuple[HealthIssue, ...]:
        return tuple(i for i in self.issues if i.severity == "warning")

    @property
    def ok(self) -> bool:
        return not self.errors

    def summary(self) -> str:
        return (
            f"{self.checked} strategies checked, "
            f"{len(self.errors)} errors, {len(self.warnings)} warnings"
        )


@dataclass(slots=True)
class StrategyRegistry:
    _by_id: dict[str, StrategyDocument] = field(default_factory=dict)
    _by_hash: dict[str, str] = field(default_factory=dict)

    # ---------------------------------------------------------- mutation

    def register(self, doc: StrategyDocument, replace: bool = False) -> str:
        """Register a document. Returns its content hash."""
        digest = doc.content_hash()
        owner = self._by_hash.get(digest)
        if owner is not None and owner != doc.strategy_id:
            raise DuplicateStrategyError(
                f"{doc.strategy_id} is semantically identical to {owner} "
                f"(content hash {digest[:12]}). Registering both would double-count "
                "it in every ranking."
            )
        if doc.strategy_id in self._by_id and not replace:
            existing = self._by_id[doc.strategy_id]
            if existing.content_hash() != digest:
                raise ValueError(
                    f"strategy_id {doc.strategy_id!r} already registered with "
                    "different content; pass replace=True to overwrite"
                )
        old = self._by_id.get(doc.strategy_id)
        if old is not None:
            self._by_hash.pop(old.content_hash(), None)
        self._by_id[doc.strategy_id] = doc
        self._by_hash[digest] = doc.strategy_id
        return digest

    def register_many(self, docs: Iterable[StrategyDocument]) -> list[str]:
        return [self.register(d) for d in docs]

    def load_directory(self, path: str | Path, pattern: str = "*.json") -> list[str]:
        """Load every DSL document in a directory, sorted by filename."""
        directory = Path(path)
        if not directory.is_dir():
            raise FileNotFoundError(f"{directory} is not a directory")
        hashes: list[str] = []
        for file in sorted(directory.glob(pattern)):
            raw = json.loads(file.read_text())
            claimed = raw.get("complexity_score")
            doc = StrategyDocument.model_validate(raw)
            if claimed is not None and abs(float(claimed) - doc.complexity_score) > 1e-9:
                raise ValueError(
                    f"{file}: stored complexity_score {claimed} disagrees with the "
                    f"rules, which score {doc.complexity_score}. The file was edited "
                    "by hand and not regenerated."
                )
            hashes.append(self.register(doc))
        return hashes

    # ----------------------------------------------------------- reading

    def get(self, strategy_id: str) -> StrategyDocument:
        if strategy_id not in self._by_id:
            raise KeyError(f"unknown strategy {strategy_id!r}")
        return self._by_id[strategy_id]

    def by_hash(self, digest: str) -> StrategyDocument | None:
        sid = self._by_hash.get(digest)
        return self._by_id[sid] if sid else None

    def ids(self) -> list[str]:
        return sorted(self._by_id)

    def documents(self) -> list[StrategyDocument]:
        return [self._by_id[k] for k in self.ids()]

    def compiled(self) -> list[CompiledStrategy]:
        """One compiled strategy per registered document.

        A registered document is usually a TEMPLATE: it declares parameters and
        references them, and the compiler refuses such a document outright. The
        registry answers the question "can each of these be realised at all?",
        and realising one requires choosing a binding, so it uses the binding the
        document itself declares. That choice is explicit here and recorded on
        every compiled document's ``binding`` -- it is never inferred downstream.
        """
        return [compile_strategy(d.bind_defaults()) for d in self.documents()]

    def __len__(self) -> int:
        return len(self._by_id)

    def __iter__(self) -> Iterator[StrategyDocument]:
        return iter(self.documents())

    def __contains__(self, strategy_id: object) -> bool:
        return strategy_id in self._by_id

    # ------------------------------------------------------ health check

    def health_check(self) -> HealthReport:
        issues: list[HealthIssue] = []
        seen_hashes: dict[str, str] = {}

        for doc in self.documents():
            sid = doc.strategy_id
            digest = doc.content_hash()
            if digest in seen_hashes and seen_hashes[digest] != sid:
                issues.append(
                    HealthIssue(
                        sid, "error", "duplicate_content",
                        f"identical content to {seen_hashes[digest]}",
                    )
                )
            seen_hashes.setdefault(digest, sid)

            try:
                # Same reasoning as `compiled()`: a template is checked at its
                # own declared defaults, which is the one binding the document
                # can be held to without a caller supplying anything.
                compiled = compile_strategy(doc.bind_defaults())
            except (CompilationError, ValueError) as exc:
                issues.append(
                    HealthIssue(sid, "error", "does_not_compile", str(exc))
                )
                continue

            if compiled.warmup_period > MAX_SANE_WARMUP:
                issues.append(
                    HealthIssue(
                        sid, "error", "warmup_too_long",
                        f"warmup {compiled.warmup_period} bars > {MAX_SANE_WARMUP}",
                    )
                )

            # Volume indicators on FX/CFD: V1 shipped an OBV strategy that could
            # never trade because the feed's volume was identically zero.
            volume_specs = [
                s.indicator
                for s in compiled.indicator_specs
                if s.indicator in VOLUME_INDICATORS
            ]
            if volume_specs:
                fx_like = [
                    sym
                    for sym in doc.universe
                    if get_instrument(sym).asset_class
                    in (AssetClass.FX_MAJOR, AssetClass.FX_CROSS)
                ]
                if fx_like:
                    issues.append(
                        HealthIssue(
                            sid, "warning", "volume_on_fx",
                            f"uses {sorted(set(volume_specs))} on FX instruments "
                            f"{fx_like[:4]}; spot FX volume is a tick count or zero",
                        )
                    )

            allocation = sum(leg.allocation for leg in doc.take_profits)
            has_trail = doc.trailing is not None and doc.trailing.kind != "none"
            has_time_stop = doc.position_management.max_bars_in_trade is not None
            if not doc.take_profits and not has_trail and not has_time_stop:
                issues.append(
                    HealthIssue(
                        sid, "warning", "stop_only_exit",
                        "no take-profit, trailing or time stop: the only exit is the "
                        "hard stop or an opposite signal",
                    )
                )
            elif doc.take_profits and allocation < 1.0 - 1e-9 and not has_trail:
                issues.append(
                    HealthIssue(
                        sid, "warning", "unmanaged_runner",
                        f"take-profit legs close {allocation:.0%} of the position and "
                        "there is no trailing model for the remainder",
                    )
                )

            if doc.complexity_score > COMPLEXITY_WARN_AT:
                issues.append(
                    HealthIssue(
                        sid, "warning", "high_complexity",
                        f"complexity {doc.complexity_score} > {COMPLEXITY_WARN_AT}; "
                        "expect a large multiple-testing penalty",
                    )
                )

            for parent in doc.parent_strategy_ids:
                if parent not in self._by_id:
                    issues.append(
                        HealthIssue(
                            sid, "warning", "missing_parent",
                            f"parent {parent!r} is not registered; lineage is broken",
                        )
                    )

            if doc.direction.value == "both" and not (
                doc.entry.long and doc.entry.short
            ):  # pragma: no cover - schema enforces this first
                issues.append(
                    HealthIssue(sid, "error", "direction_mismatch", "both-sided but one-sided rules")
                )

        return HealthReport(tuple(issues), len(self._by_id))


def load_seed_registry(path: str | Path = "research/strategies") -> StrategyRegistry:
    registry = StrategyRegistry()
    registry.load_directory(path)
    return registry
