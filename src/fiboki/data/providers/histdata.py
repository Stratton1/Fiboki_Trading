"""HistData.com importer — bid-only bars on an EST-without-DST clock.

Two facts about HistData that V1 ignored, and that cost it correctness:

**1. The bars are BID, not mid.**
HistData's free ASCII M1 series is built from bid quotes. V1 loaded them as
`open/high/low/close` and every engine downstream treated those as mid prices,
then *additionally* subtracted a modelled half-spread on entry and exit. A long
entry was therefore charged a spread it had already implicitly paid, and a short
entry was credited one it never received. The fix is not a constant: it is
declaring ``price_basis = BID`` in the data so that any component needing mid
has to convert explicitly, with an explicit spread assumption it can be held to.

**2. The timestamps are EST with no daylight saving.**
HistData stamps every bar in US Eastern *Standard* Time, year round — a fixed
UTC-05:00, never UTC-04:00. It is not "America/New_York": it is New York's
winter offset applied in July as well. V1's ingest attached ``tz="UTC"`` to
these naive timestamps, so:

* every bar was labelled five hours earlier than it happened;
* a "London open" session filter fired at the wrong hour year-round;
* and the error was constant, so nothing ever looked obviously broken.

The evidence is in the data itself and is checked by
:func:`detect_timestamp_convention`: a genuinely-UTC FX series has its weekly
open drift between 21:00 and 22:00 UTC with US daylight saving, while these
files show a rock-solid 17:00 boundary all year, which is only possible on a
fixed offset.

The correction is ``true_utc = naive_est + 5h``, recorded as a declared
:class:`~fiboki.data.schema.Adjustment` so it is visible in the dataset metadata
forever rather than buried in an ingest script.
"""
from __future__ import annotations

import csv
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from fiboki.core.enums import DataQuality, Timeframe
from fiboki.data.providers.base import (
    BarBatch,
    BarProvider,
    ProviderCapabilities,
    ProviderError,
)
from fiboki.data.schema import (
    Adjustment,
    DatasetKind,
    PriceBasis,
    canonical_frame,
    describe_frame,
)

HISTDATA_SOURCE = "histdata"

#: HistData's fixed clock. Not America/New_York — that observes DST; this does not.
HISTDATA_UTC_OFFSET = timedelta(hours=-5)
HISTDATA_TZ_LABEL = "EST-fixed (UTC-05:00, no DST)"

#: Volume marker. HistData FX ships a volume column that is always zero: it has
#: no volume data at all. Storing a literal 0 would let a volume strategy run and
#: silently produce a flat feature, so absence is stored as -1 and flagged.
ABSENT_VOLUME = -1

_FILENAME_RE = re.compile(
    r"^(?P<symbol>[a-z0-9]+)[_\-](?P<timeframe>m1|m5|m15|m30|h1|h4|d1)\.parquet$",
    re.IGNORECASE,
)


def convert_histdata_index(
    index: pd.DatetimeIndex, *, already_mislabelled_utc: bool
) -> pd.DatetimeIndex:
    """Convert HistData's EST-no-DST stamps to true UTC.

    ``already_mislabelled_utc=True`` handles the V1 store, where naive EST
    timestamps were given ``tz="UTC"`` without being shifted. Both paths end at
    the same answer: add five hours.
    """
    if already_mislabelled_utc:
        if index.tz is None:
            raise ProviderError(
                "index claimed to be mislabelled-UTC but is tz-naive; pass "
                "already_mislabelled_utc=False"
            )
        naive = index.tz_localize(None)
    else:
        if index.tz is not None:
            raise ProviderError(
                "raw HistData timestamps must be tz-naive; they carry no zone"
            )
        naive = index
    shifted = naive - HISTDATA_UTC_OFFSET  # naive - (-5h) = naive + 5h
    return pd.DatetimeIndex(shifted).tz_localize("UTC")


@dataclass(frozen=True, slots=True)
class ConventionEvidence:
    """Empirical check of which timezone convention a bar series is on.

    Cheap, and it would have caught the V1 bug on day one.
    """

    weekly_open_hours: dict[int, int]
    dominant_open_hour: int
    distinct_open_hours: int
    looks_fixed_offset: bool
    verdict: str

    def summary(self) -> str:
        return (
            f"{self.verdict} (weekly open hour {self.dominant_open_hour:02d}:00 UTC, "
            f"{self.distinct_open_hours} distinct open hours observed)"
        )


def detect_timestamp_convention(index: pd.DatetimeIndex) -> ConventionEvidence:
    """Infer whether a series is on a fixed offset or a DST-observing clock.

    FX reopens at 17:00 New York. Under a real DST-observing clock that is
    22:00 UTC in winter and 21:00 UTC in summer, so a UTC series shows two
    distinct weekly-open hours. A series showing exactly one is on a fixed
    offset, whatever its tz label claims.
    """
    if index.tz is None:
        raise ProviderError("convention detection needs a tz-aware index")
    idx = pd.DatetimeIndex(index).tz_convert("UTC").sort_values()
    if len(idx) < 100:
        raise ProviderError("not enough bars to infer a timezone convention")
    # The first bar after a gap of >= 24h is a weekly open.
    deltas = idx.to_series().diff()
    opens = idx[(deltas >= pd.Timedelta(hours=24)).to_numpy()]
    if len(opens) < 20:
        opens = idx[(deltas >= pd.Timedelta(hours=12)).to_numpy()]
    hours = pd.Series(opens.hour).value_counts().to_dict()
    hours = {int(k): int(v) for k, v in hours.items()}
    if not hours:
        raise ProviderError("no weekly opens found; cannot infer convention")
    dominant = max(hours, key=lambda h: hours[h])
    total = sum(hours.values())
    significant = sum(1 for h, n in hours.items() if n / total > 0.15)
    fixed = significant <= 1
    verdict = (
        "fixed-offset clock (source is NOT true UTC)"
        if fixed
        else "DST-observing clock consistent with true UTC"
    )
    return ConventionEvidence(
        weekly_open_hours=hours,
        dominant_open_hour=dominant,
        distinct_open_hours=significant,
        looks_fixed_offset=fixed,
        verdict=verdict,
    )


class HistDataParquetProvider(BarProvider):
    """Reads the V1 canonical HistData parquet store.

    The V1 store lives at ``<root>/<SYMBOL>/<symbol>_<tf>.parquet`` with columns
    ``open, high, low, close, volume`` and a ``timestamp`` index that is labelled
    UTC but actually holds EST-no-DST wall clock. This provider corrects that and
    declares both the basis and the adjustment.
    """

    def __init__(
        self,
        root: str | Path,
        *,
        assume_mislabelled_utc: bool = True,
        source_label: str = f"{HISTDATA_SOURCE}/v1-parquet",
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        if not self.root.exists():
            raise ProviderError(
                f"HistData root {self.root} does not exist. Pass the real path; "
                "this provider does not search for one."
            )
        self.assume_mislabelled_utc = assume_mislabelled_utc
        self.source_label = source_label

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            name="histdata-parquet",
            native_price_basis=PriceBasis.BID,
            native_timezone=HISTDATA_TZ_LABEL,
            supports_bid_ask=False,
            supports_tick_volume=False,
            supports_real_volume=False,
            timeframes=(
                Timeframe.M1, Timeframe.M5, Timeframe.M15,
                Timeframe.M30, Timeframe.H1, Timeframe.H4, Timeframe.D1,
            ),
            requires_credentials=False,
            can_emit_incomplete_bars=False,
            notes=(
                "Bid-only bars. No volume of any kind: the volume column is "
                "identically zero and is stored as the absent marker -1. "
                "Timestamps are EST with no DST and are shifted +5h to true UTC."
            ),
        )

    # -- discovery ---------------------------------------------------

    def available(self) -> list[tuple[str, Timeframe]]:
        found: list[tuple[str, Timeframe]] = []
        for path in sorted(self.root.glob("*/*.parquet")):
            parsed = self._parse_filename(path)
            if parsed:
                found.append(parsed)
        return found

    @staticmethod
    def _parse_filename(path: Path) -> tuple[str, Timeframe] | None:
        m = _FILENAME_RE.match(path.name)
        if not m:
            return None
        return m.group("symbol").upper(), Timeframe(m.group("timeframe").upper())

    def path_for(self, instrument: str, timeframe: Timeframe) -> Path:
        sym = instrument.upper()
        path = self.root / sym / f"{sym.lower()}_{timeframe.value.lower()}.parquet"
        if not path.exists():
            raise ProviderError(
                f"HistData file not found: {path}. Searched exactly one location, "
                "by design."
            )
        return path

    # -- fetch -------------------------------------------------------

    def fetch_bars(
        self,
        instrument: str,
        timeframe: Timeframe,
        *,
        start: pd.Timestamp | None = None,
        end: pd.Timestamp | None = None,
    ) -> BarBatch:
        self.capabilities.assert_timeframe(timeframe)
        path = self.path_for(instrument, timeframe)
        raw = pd.read_parquet(path)
        if not isinstance(raw.index, pd.DatetimeIndex):
            if "timestamp" in raw.columns:
                raw = raw.set_index("timestamp")
            else:
                raise ProviderError(f"{path} has no timestamp index or column")

        warnings: list[str] = []
        original_first = raw.index.min()

        # Evidence-based check of what we are actually looking at.
        evidence: ConventionEvidence | None = None
        try:
            evidence = detect_timestamp_convention(raw.index)
            if not evidence.looks_fixed_offset:
                warnings.append(
                    "timestamps look like a DST-observing clock, not HistData's "
                    "fixed EST; the +5h correction may be wrong for this file"
                )
        except ProviderError as exc:  # pragma: no cover - tiny fixtures
            warnings.append(f"convention detection skipped: {exc}")

        corrected = convert_histdata_index(
            raw.index, already_mislabelled_utc=self.assume_mislabelled_utc
        )
        raw = raw.set_axis(corrected, axis=0)

        frame = raw.rename(columns=str.lower)
        # HistData FX has no volume. Store absence explicitly, never as zero.
        if "volume" in frame.columns:
            vol = pd.to_numeric(frame["volume"], errors="coerce").fillna(0)
            if bool((vol == 0).all()):
                frame["volume"] = ABSENT_VOLUME
                warnings.append(
                    "volume column was identically zero and has been stored as the "
                    "absent marker (-1); volume-dependent strategies must be blocked "
                    "on this dataset rather than silently fed zeros"
                )
            else:
                frame["volume"] = vol.astype("int64")
        else:
            frame["volume"] = ABSENT_VOLUME

        frame = canonical_frame(
            frame,
            instrument=instrument,
            timeframe=timeframe,
            price_basis=PriceBasis.BID,
        )
        if start is not None:
            frame = frame[frame.index >= pd.Timestamp(start)]
        if end is not None:
            frame = frame[frame.index <= pd.Timestamp(end)]
        if frame.empty:
            raise ProviderError(
                f"no {instrument} {timeframe.value} bars in the requested range "
                f"[{start}, {end}]; the file covers "
                f"{corrected.min()} to {corrected.max()}"
            )

        adjustments = (
            Adjustment(
                kind="timezone_correction",
                description=(
                    "HistData stamps bars in EST with no daylight saving; the V1 store "
                    "additionally labelled those naive stamps as UTC without shifting "
                    "them. Both errors are corrected here by adding 5 hours."
                ),
                parameters={
                    "source_convention": HISTDATA_TZ_LABEL,
                    "offset_applied_hours": 5,
                    "input_was_mislabelled_utc": self.assume_mislabelled_utc,
                    "original_first_timestamp": str(original_first),
                    "corrected_first_timestamp": str(frame.index.min()),
                    "evidence": evidence.summary() if evidence else "unavailable",
                },
            ),
            Adjustment(
                kind="price_basis_declaration",
                description=(
                    "Bars are BID. They are not mid. Any consumer needing mid must "
                    "add an explicit half-spread and say so."
                ),
                parameters={"price_basis": PriceBasis.BID.value},
            ),
        )

        metadata = describe_frame(
            frame,
            source=self.source_label,
            source_identifier=str(path),
            timezone_of_origin=HISTDATA_TZ_LABEL,
            quality=DataQuality.RAW,
            kind=DatasetKind.RAW,
            adjustments=adjustments,
            notes="; ".join(warnings),
            extra={
                "provider": "histdata-parquet",
                "file_bytes": path.stat().st_size,
                "convention_evidence": (
                    {
                        "weekly_open_hours": evidence.weekly_open_hours,
                        "dominant_open_hour": evidence.dominant_open_hour,
                        "looks_fixed_offset": evidence.looks_fixed_offset,
                        "verdict": evidence.verdict,
                    }
                    if evidence
                    else None
                ),
            },
        )
        return BarBatch(
            frame=frame,
            metadata=metadata,
            adjustments=adjustments,
            warnings=tuple(warnings),
        )


# ------------------------------------------------------- CSV ingestion


def iter_histdata_csv(path: str | Path) -> Iterator[tuple[str, float, float, float, float, int]]:
    """Stream HistData's ASCII M1 format: ``YYYYMMDD HHMMSS;O;H;L;C;V``."""
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.reader(fh, delimiter=";"):
            if len(row) < 6 or not row[0].strip():
                continue
            yield (
                row[0].strip(),
                float(row[1]), float(row[2]), float(row[3]), float(row[4]),
                int(float(row[5])),
            )


def read_histdata_csv(
    path: str | Path, *, instrument: str, timeframe: Timeframe = Timeframe.M1
) -> pd.DataFrame:
    """Read HistData's native ASCII export into a canonical UTC bid frame.

    The same +5h correction applies: the file's stamps are EST-no-DST.
    """
    records = list(iter_histdata_csv(path))
    if not records:
        raise ProviderError(f"{path} contained no parsable rows")
    stamps = pd.to_datetime([r[0] for r in records], format="%Y%m%d %H%M%S")
    frame = pd.DataFrame(
        {
            "open": [r[1] for r in records],
            "high": [r[2] for r in records],
            "low": [r[3] for r in records],
            "close": [r[4] for r in records],
            "volume": [r[5] for r in records],
        },
        index=convert_histdata_index(
            pd.DatetimeIndex(stamps), already_mislabelled_utc=False
        ),
    )
    if bool((frame["volume"] == 0).all()):
        frame["volume"] = ABSENT_VOLUME
    return canonical_frame(
        frame,
        instrument=instrument,
        timeframe=timeframe,
        price_basis=PriceBasis.BID,
    )


def bid_to_mid(
    frame: pd.DataFrame, *, assumed_spread_pips: float, pip_size: float
) -> pd.DataFrame:
    """Convert BID bars to SYNTHETIC_MID by adding half an assumed spread.

    Deliberately awkward to call: both the spread and the pip size must be
    passed, so nobody can do this by accident. The result is stamped
    ``SYNTHETIC_MID``, never ``MID``, because no mid was ever quoted — and the
    assumption is recorded on the frame's own basis column so a reader can see
    that these prices are modelled, not observed.
    """
    if assumed_spread_pips < 0:
        raise ValueError("assumed_spread_pips must be non-negative")
    half = 0.5 * assumed_spread_pips * pip_size
    out = frame.copy()
    for col in ("open", "high", "low", "close"):
        if col in out.columns:
            out[f"bid_{col.split('_')[-1]}"] = out[col]
            out[col] = out[col] + half
    out["price_basis"] = PriceBasis.SYNTHETIC_MID.value
    ordered = [c for c in out.columns]
    del ordered
    from fiboki.data.schema import COLUMN_ORDER

    return out[[c for c in COLUMN_ORDER if c in out.columns]]


def zero_volume_fraction(frame: pd.DataFrame) -> float:
    """Fraction of bars whose volume is zero or absent. 1.0 means blind."""
    if "volume" not in frame.columns or frame.empty:
        return 1.0
    v = frame["volume"].to_numpy()
    return float(np.mean((v == 0) | (v == ABSENT_VOLUME)))
