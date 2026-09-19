"""Dukascopy importer interface.

Dukascopy is the upgrade path from HistData: it publishes free tick data with
*both* sides of the book, timestamped in genuine UTC. That fixes both HistData
defects at once — no bid-only assumption, no fixed-offset clock — which is why
the interface exists now even though nothing fetches over the network yet.

Wire format (documented and stable for years):

    https://datafeed.dukascopy.com/datafeed/{SYMBOL}/{YYYY}/{MM0}/{DD}/{HH}h_ticks.bi5

``MM0`` is a **zero-based month** (January is ``00``). Getting that wrong
silently returns the previous month's data, which is exactly the class of bug
this package exists to prevent, so :func:`tick_url` is the only sanctioned way
to build the path and it is unit-tested.

The payload is LZMA-compressed, holding fixed-width big-endian records::

    >IIff   ms_since_hour, ask_points, bid_points, ask_volume, bid_volume

Prices are integers in "points"; divide by the instrument's point factor
(10^5 for most FX, 10^3 for JPY pairs and metals).

What is implemented here: URL construction, record decoding, and tick-to-bar
aggregation — all pure functions, all tested against fixtures this module can
generate. What is not implemented: the HTTP fetch, which is a stub that raises
rather than a stub that returns nothing.
"""
from __future__ import annotations

import lzma
import struct
from dataclasses import dataclass
from datetime import UTC, datetime

import pandas as pd

from fiboki.core.enums import DataQuality, Timeframe
from fiboki.data.providers.base import (
    BarBatch,
    BarProvider,
    ProviderCapabilities,
    ProviderError,
    UnsupportedRequest,
)
from fiboki.data.schema import (
    Adjustment,
    DatasetKind,
    PriceBasis,
    canonical_frame,
    describe_frame,
)

DUKASCOPY_BASE_URL = "https://datafeed.dukascopy.com/datafeed"
TICK_RECORD = struct.Struct(">IIIff")
TICK_RECORD_SIZE = TICK_RECORD.size  # 20 bytes

#: Divisor turning integer "points" into a price. JPY pairs and metals quote
#: with three decimals, everything else with five.
POINT_FACTORS: dict[str, float] = {
    "default": 1e5,
    "jpy": 1e3,
    "metal": 1e3,
}


def point_factor(symbol: str) -> float:
    sym = symbol.upper()
    if sym.endswith("JPY"):
        return POINT_FACTORS["jpy"]
    if sym.startswith(("XAU", "XAG")):
        return POINT_FACTORS["metal"]
    return POINT_FACTORS["default"]


def tick_url(symbol: str, when: datetime, *, base_url: str = DUKASCOPY_BASE_URL) -> str:
    """Build the hourly tick URL. Month is zero-based — that is not a typo."""
    if when.tzinfo is None:
        raise ProviderError("Dukascopy hours are UTC; pass a tz-aware datetime")
    utc = when.astimezone(UTC)
    return (
        f"{base_url}/{symbol.upper()}/{utc.year:04d}/{utc.month - 1:02d}/"
        f"{utc.day:02d}/{utc.hour:02d}h_ticks.bi5"
    )


@dataclass(frozen=True, slots=True)
class DukascopyTick:
    timestamp: datetime
    bid: float
    ask: float
    bid_volume: float
    ask_volume: float

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0


def decode_ticks(
    payload: bytes, hour_start: datetime, *, factor: float, compressed: bool = True
) -> list[DukascopyTick]:
    """Decode one ``*h_ticks.bi5`` payload into ticks."""
    if hour_start.tzinfo is None:
        raise ProviderError("hour_start must be tz-aware UTC")
    raw = lzma.decompress(payload) if compressed else payload
    if len(raw) % TICK_RECORD_SIZE:
        raise ProviderError(
            f"tick payload is {len(raw)} bytes, not a multiple of {TICK_RECORD_SIZE}; "
            "this is a truncated or foreign file and will not be partially decoded"
        )
    base = hour_start.astimezone(UTC)
    ticks: list[DukascopyTick] = []
    for offset in range(0, len(raw), TICK_RECORD_SIZE):
        ms, ask_pts, bid_pts, ask_vol, bid_vol = TICK_RECORD.unpack_from(raw, offset)
        ticks.append(
            DukascopyTick(
                timestamp=base + pd.Timedelta(milliseconds=int(ms)).to_pytimedelta(),
                bid=bid_pts / factor,
                ask=ask_pts / factor,
                bid_volume=float(bid_vol),
                ask_volume=float(ask_vol),
            )
        )
    return ticks


def encode_ticks(ticks: list[DukascopyTick], hour_start: datetime, *, factor: float) -> bytes:
    """Encode ticks back into the wire format. Used to build test fixtures."""
    base = hour_start.astimezone(UTC)
    buf = bytearray()
    for t in ticks:
        ms = int((t.timestamp - base).total_seconds() * 1000)
        buf += TICK_RECORD.pack(
            ms,
            int(round(t.ask * factor)),
            int(round(t.bid * factor)),
            float(t.ask_volume),
            float(t.bid_volume),
        )
    return lzma.compress(bytes(buf))


def ticks_to_bars(
    ticks: list[DukascopyTick],
    *,
    instrument: str,
    timeframe: Timeframe,
    price_basis: PriceBasis = PriceBasis.SYNTHETIC_MID,
) -> pd.DataFrame:
    """Aggregate ticks into canonical bars, carrying both sides of the book.

    The default basis is SYNTHETIC_MID because a mid built from bid and ask was
    computed by us, not quoted by anyone — and the bid_*/ask_* columns are kept
    so an execution model can price a buy and a sell differently instead of
    guessing a symmetric spread.
    """
    if not ticks:
        raise ProviderError("no ticks to aggregate")
    frame = pd.DataFrame(
        {
            "bid": [t.bid for t in ticks],
            "ask": [t.ask for t in ticks],
            "mid": [t.mid for t in ticks],
            "volume": [t.bid_volume + t.ask_volume for t in ticks],
        },
        index=pd.DatetimeIndex([t.timestamp for t in ticks], tz="UTC", name="timestamp"),
    ).sort_index(kind="stable")

    rule = f"{timeframe.minutes}min"
    side = {"bid": "bid", "ask": "ask", "mid": "mid"}
    chosen = (
        "mid"
        if price_basis in (PriceBasis.MID, PriceBasis.SYNTHETIC_MID)
        else side[price_basis.value]
    )

    def _ohlc(col: str, prefix: str) -> dict[str, pd.Series]:
        g = frame[col].resample(rule, origin="epoch", label="left", closed="left")
        return {
            f"{prefix}open": g.first(),
            f"{prefix}high": g.max(),
            f"{prefix}low": g.min(),
            f"{prefix}close": g.last(),
        }

    data: dict[str, pd.Series] = {}
    data.update(_ohlc(chosen, ""))
    data.update(_ohlc("bid", "bid_"))
    data.update(_ohlc("ask", "ask_"))
    grouped = frame["volume"].resample(rule, origin="epoch", label="left", closed="left")
    data["volume"] = grouped.sum().round().astype("int64")
    data["tick_volume"] = frame["mid"].resample(
        rule, origin="epoch", label="left", closed="left"
    ).count().astype("int64")

    bars = pd.DataFrame(data)
    bars = bars[bars["open"].notna()]
    bars.index.name = "timestamp"
    return canonical_frame(
        bars, instrument=instrument, timeframe=timeframe, price_basis=price_basis
    )


class DukascopyProvider(BarProvider):
    """Interface + offline decoding. Network fetch is deliberately unimplemented."""

    def __init__(
        self,
        *,
        base_url: str = DUKASCOPY_BASE_URL,
        http_client: object | None = None,
        price_basis: PriceBasis = PriceBasis.SYNTHETIC_MID,
    ) -> None:
        self.base_url = base_url
        self.http_client = http_client
        self.price_basis = price_basis

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            name="dukascopy",
            native_price_basis=PriceBasis.SYNTHETIC_MID,
            native_timezone="UTC",
            supports_bid_ask=True,
            supports_tick_volume=True,
            supports_real_volume=False,
            timeframes=(
                Timeframe.M1, Timeframe.M5, Timeframe.M15,
                Timeframe.M30, Timeframe.H1, Timeframe.H4, Timeframe.D1,
            ),
            requires_credentials=False,
            can_emit_incomplete_bars=True,
            max_bars_per_request=None,
            notes=(
                "Tick source with both sides of the book, genuine UTC. The final "
                "bar of a fetch may be incomplete and must be dropped by the caller. "
                "Volume is broker-side, not exchange volume."
            ),
        )

    def available(self) -> list[tuple[str, Timeframe]]:
        raise UnsupportedRequest(
            "Dukascopy availability requires a network probe, which is not "
            "implemented. Use bars_from_tick_payloads with downloaded files."
        )

    def fetch_bars(
        self,
        instrument: str,
        timeframe: Timeframe,
        *,
        start: pd.Timestamp | None = None,
        end: pd.Timestamp | None = None,
    ) -> BarBatch:
        raise UnsupportedRequest(
            "DukascopyProvider.fetch_bars needs an HTTP client and is not wired up. "
            "This raises rather than returning an empty frame on purpose: an "
            "unimplemented fetch must never look like 'there was no data'."
        )

    # -- offline path ------------------------------------------------

    def bars_from_tick_payloads(
        self,
        payloads: list[tuple[datetime, bytes]],
        *,
        instrument: str,
        timeframe: Timeframe,
        compressed: bool = True,
    ) -> BarBatch:
        """Build bars from already-downloaded hourly ``.bi5`` payloads."""
        factor = point_factor(instrument)
        ticks: list[DukascopyTick] = []
        for hour_start, payload in sorted(payloads, key=lambda p: p[0]):
            ticks.extend(
                decode_ticks(payload, hour_start, factor=factor, compressed=compressed)
            )
        frame = ticks_to_bars(
            ticks,
            instrument=instrument,
            timeframe=timeframe,
            price_basis=self.price_basis,
        )
        adjustments = (
            Adjustment(
                kind="price_basis_declaration",
                description=(
                    "OHLC is a mid computed from quoted bid and ask; bid_* and ask_* "
                    "columns carry the quoted sides so execution need not assume a "
                    "symmetric spread."
                ),
                parameters={"price_basis": self.price_basis.value},
            ),
            Adjustment(
                kind="tick_aggregation",
                description="bars aggregated from ticks, epoch-anchored UTC buckets",
                parameters={"timeframe": timeframe.value, "origin": "epoch"},
            ),
        )
        metadata = describe_frame(
            frame,
            source="dukascopy",
            source_identifier=f"{instrument.upper()}/{len(payloads)}-hourly-payloads",
            timezone_of_origin="UTC",
            quality=DataQuality.RAW,
            kind=DatasetKind.RAW,
            adjustments=adjustments,
            notes="tick-derived",
            extra={"tick_count": len(ticks), "point_factor": factor},
        )
        return BarBatch(frame=frame, metadata=metadata, adjustments=adjustments)
