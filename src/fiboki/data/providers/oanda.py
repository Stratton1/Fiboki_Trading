"""OANDA v20 candles importer.

Implemented against the documented v20 response shape for
``GET /v3/instruments/{instrument}/candles``. No credentials exist in this
environment, so the network path raises :class:`AuthenticationRequired` and the
parsing path — which is where all the correctness lives — is fully implemented
and tested against constructed fixtures.

The documented response::

    {
      "instrument": "EUR_USD",
      "granularity": "H1",
      "candles": [
        {
          "complete": true,
          "volume": 1234,
          "time": "2026-01-05T09:00:00.000000000Z",
          "bid": {"o": "1.10400", "h": "1.10480", "l": "1.10380", "c": "1.10460"},
          "ask": {"o": "1.10412", "h": "1.10492", "l": "1.10392", "c": "1.10472"},
          "mid": {"o": "1.10406", "h": "1.10486", "l": "1.10386", "c": "1.10466"}
        }
      ]
    }

Three things this parser refuses to be casual about:

* **Incomplete candles.** ``complete: false`` marks the candle currently
  forming. Fiboki evaluates signals on closed candles only, so an incomplete
  candle is dropped by default and its presence is reported, never silently
  aggregated into a bar that will change next minute.
* **Prices are strings.** v20 sends decimal strings to avoid float ambiguity.
  Parsing them as floats is fine for our purposes but is done once, explicitly,
  rather than relying on whatever the JSON decoder felt like.
* **``volume`` is tick count, not traded size.** OANDA is a broker, not an
  exchange; its "volume" is the number of price updates it saw. It is recorded
  as ``tick_volume`` and the real ``volume`` column is left absent, so no
  strategy can mistake broker tick counts for market volume.

Candle alignment
----------------
v20 aligns H2-H12 and D candles to ``dailyAlignment=17`` in
``America/New_York`` by DEFAULT, so an unqualified H4 request returns bars
starting at 21:00/01:00/05:00 UTC (summer) or 22:00/02:00/06:00 UTC (winter)
and a D bar starting at New York 17:00. Every stored research frame is
epoch-anchored UTC (``data/resample.py``). A live H4 bar that starts at 21:00
UTC is a different bar from the one the strategy was researched on, so
:meth:`OandaCandlesProvider.request_params` asks for ``dailyAlignment=0`` and
``alignmentTimezone=UTC`` unless told otherwise. Minute and hourly candles are
unaffected by these parameters.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from fiboki.core import instruments as instrument_registry
from fiboki.core.enums import DataQuality, Timeframe
from fiboki.data.providers.base import (
    AuthenticationRequired,
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

OANDA_PRACTICE_HOST = "https://api-fxpractice.oanda.com"
OANDA_LIVE_HOST = "https://api-fxtrade.oanda.com"

#: v20 granularity strings, mapped to Fiboki timeframes.
GRANULARITY: dict[Timeframe, str] = {
    Timeframe.M1: "M1",
    Timeframe.M5: "M5",
    Timeframe.M15: "M15",
    Timeframe.M30: "M30",
    Timeframe.H1: "H1",
    Timeframe.H4: "H4",
    Timeframe.D1: "D",
}

#: v20 "price" component flags: B=bid, A=ask, M=mid.
PRICE_COMPONENT: dict[PriceBasis, str] = {
    PriceBasis.BID: "B",
    PriceBasis.ASK: "A",
    PriceBasis.MID: "M",
    PriceBasis.SYNTHETIC_MID: "BA",
}

ABSENT_VOLUME = -1


def to_oanda_instrument(symbol: str) -> str:
    """``EURUSD`` -> ``EUR_USD``, ``US500`` -> ``SPX500_USD``.

    Delegates to THE mapping, :func:`fiboki.core.instruments.oanda_name_for`
    (the committed OANDA table; ``fiboki.broker.oanda_instruments`` documents
    the rules that generate it). An exact OANDA name is accepted unchanged, so
    a caller holding a v20 name need not translate it back first.
    """
    if symbol in instrument_registry.oanda_names():
        return symbol
    try:
        return instrument_registry.oanda_name_for(symbol.replace("_", ""))
    except KeyError as exc:
        raise ProviderError(
            f"cannot map {symbol!r} to an OANDA instrument name: {exc.args[0]}"
        ) from exc


def from_oanda_instrument(name: str) -> str:
    """``EUR_USD`` -> ``EURUSD``, ``DE30_EUR`` -> ``DE40``. Unknown names raise."""
    try:
        return instrument_registry.symbol_for_oanda_name(name)
    except KeyError as exc:
        raise ProviderError(str(exc.args[0])) from exc


def _side(candle: dict[str, Any], key: str) -> dict[str, float] | None:
    block = candle.get(key)
    if not block:
        return None
    return {k: float(v) for k, v in block.items()}


def parse_candles_response(
    payload: dict[str, Any],
    *,
    instrument: str | None = None,
    timeframe: Timeframe | None = None,
    price_basis: PriceBasis = PriceBasis.MID,
    drop_incomplete: bool = True,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Parse a v20 candles response into a canonical frame plus a parse report.

    Returns ``(frame, report)`` where ``report`` records how many candles were
    dropped as incomplete — a number that must be visible, because a silently
    included forming candle is a look-ahead bug.
    """
    candles = payload.get("candles")
    if candles is None:
        raise ProviderError("v20 response has no 'candles' key")

    sym = instrument or from_oanda_instrument(str(payload.get("instrument", "")))
    if not sym:
        raise ProviderError("cannot determine the instrument for this response")

    tf = timeframe
    if tf is None:
        gran = str(payload.get("granularity", ""))
        inverse = {v: k for k, v in GRANULARITY.items()}
        if gran not in inverse:
            raise ProviderError(f"unknown v20 granularity {gran!r}")
        tf = inverse[gran]

    rows: list[dict[str, Any]] = []
    stamps: list[pd.Timestamp] = []
    incomplete = 0
    missing_side = 0

    for candle in candles:
        if not candle.get("complete", False):
            incomplete += 1
            if drop_incomplete:
                continue
        bid = _side(candle, "bid")
        ask = _side(candle, "ask")
        mid = _side(candle, "mid")

        if price_basis is PriceBasis.BID:
            chosen = bid
        elif price_basis is PriceBasis.ASK:
            chosen = ask
        elif price_basis is PriceBasis.MID:
            chosen = mid
        else:  # SYNTHETIC_MID: compute from the two quoted sides
            if bid is None or ask is None:
                chosen = None
            else:
                chosen = {k: (bid[k] + ask[k]) / 2.0 for k in ("o", "h", "l", "c")}

        if chosen is None:
            missing_side += 1
            raise ProviderError(
                f"candle at {candle.get('time')} has no {price_basis.value} prices. "
                "Request the matching price component (price=B/A/M) rather than "
                "letting a missing side become a NaN bar."
            )

        row: dict[str, Any] = {
            "open": chosen["o"],
            "high": chosen["h"],
            "low": chosen["l"],
            "close": chosen["c"],
            # Broker tick count, explicitly NOT market volume.
            "tick_volume": int(candle.get("volume", 0)),
            "volume": ABSENT_VOLUME,
        }
        if bid is not None:
            row.update(
                bid_open=bid["o"], bid_high=bid["h"], bid_low=bid["l"], bid_close=bid["c"]
            )
        if ask is not None:
            row.update(
                ask_open=ask["o"], ask_high=ask["h"], ask_low=ask["l"], ask_close=ask["c"]
            )
        rows.append(row)
        stamps.append(pd.Timestamp(candle["time"]))

    if not rows:
        raise ProviderError(
            "no complete candles in this response. Returning an empty frame here "
            "would look identical to 'the instrument does not trade', which is the "
            "confusion that wasted a V1 research batch."
        )

    index = pd.DatetimeIndex(stamps, name="timestamp")
    # v20 stamps are RFC3339 with a Z suffix, so they parse tz-aware; the naive
    # branch only fires for a hand-built fixture.
    index = index.tz_localize("UTC") if index.tz is None else index.tz_convert("UTC")
    frame = pd.DataFrame(rows, index=index)
    frame = canonical_frame(
        frame, instrument=sym, timeframe=tf, price_basis=price_basis
    )
    report = {
        "candles_received": len(candles),
        "candles_kept": len(frame),
        "incomplete_dropped": incomplete if drop_incomplete else 0,
        "incomplete_kept": 0 if drop_incomplete else incomplete,
        "missing_side": missing_side,
    }
    return frame, report


class OandaCandlesProvider(BarProvider):
    """v20 candles. Parsing is real; fetching needs credentials that do not exist."""

    def __init__(
        self,
        *,
        api_token: str | None = None,
        account_id: str | None = None,
        host: str = OANDA_PRACTICE_HOST,
        price_basis: PriceBasis = PriceBasis.MID,
        http_client: object | None = None,
    ) -> None:
        self.api_token = api_token
        self.account_id = account_id
        self.host = host
        self.price_basis = price_basis
        self.http_client = http_client

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            name="oanda-v20",
            native_price_basis=self.price_basis,
            native_timezone="UTC",
            supports_bid_ask=True,
            supports_tick_volume=True,
            supports_real_volume=False,
            timeframes=tuple(GRANULARITY),
            requires_credentials=True,
            can_emit_incomplete_bars=True,
            max_bars_per_request=5000,
            notes=(
                "Broker feed. 'volume' in the v20 payload is a tick count and is "
                "stored as tick_volume; real traded volume is not available and is "
                "stored as absent (-1). The newest candle is usually incomplete and "
                "is dropped by default."
            ),
        )

    def request_params(
        self,
        instrument: str,
        timeframe: Timeframe,
        *,
        start: pd.Timestamp | None = None,
        end: pd.Timestamp | None = None,
        count: int | None = None,
        align_utc: bool = True,
    ) -> tuple[str, dict[str, Any]]:
        """The exact URL and query the fetch would issue. Testable without a network.

        ``align_utc`` (default True) pins daily-aligned granularities to UTC
        midnight so they match the epoch-anchored research frames. See the
        module docstring; passing False reproduces v20's New York alignment.
        """
        self.capabilities.assert_timeframe(timeframe)
        name = to_oanda_instrument(instrument)
        url = f"{self.host}/v3/instruments/{name}/candles"
        params: dict[str, Any] = {
            "granularity": GRANULARITY[timeframe],
            "price": PRICE_COMPONENT[self.price_basis],
        }
        if align_utc:
            params["dailyAlignment"] = 0
            params["alignmentTimezone"] = "UTC"
        if count is not None:
            params["count"] = int(count)
        if start is not None:
            params["from"] = pd.Timestamp(start).tz_convert("UTC").isoformat()
        if end is not None:
            params["to"] = pd.Timestamp(end).tz_convert("UTC").isoformat()
        if start is not None and end is not None and count is not None:
            raise ProviderError("v20 rejects from+to+count together; pass at most two")
        return url, params

    def available(self) -> list[tuple[str, Timeframe]]:
        raise AuthenticationRequired(
            "listing OANDA instruments requires an API token and account id"
        )

    def fetch_bars(
        self,
        instrument: str,
        timeframe: Timeframe,
        *,
        start: pd.Timestamp | None = None,
        end: pd.Timestamp | None = None,
        count: int | None = None,
    ) -> BarBatch:
        """Fetch candles. ``count`` asks for the newest N (v20 ``count``).

        This is an idempotent GET. It is deliberately NOT retried here: the
        data layer sits below ``broker`` and cannot import
        :mod:`fiboki.broker.retry`. The live feed wraps this method with
        ``retry_idempotent_read`` at composition time instead
        (``workers/feeds.py``).
        """
        if not self.api_token:
            raise AuthenticationRequired(
                "OANDA v20 needs OANDA_API_TOKEN. No credentials are configured, and "
                "this raises rather than returning nothing: an unauthenticated fetch "
                "must never be mistaken for an instrument with no history."
            )
        if self.http_client is None:  # pragma: no cover - needs a live client
            raise ProviderError("no HTTP client configured")
        url, params = self.request_params(
            instrument, timeframe, start=start, end=end, count=count
        )
        response = self.http_client.get(  # type: ignore[attr-defined]
            url,
            params=params,
            headers={"Authorization": f"Bearer {self.api_token}"},
        )
        response.raise_for_status()
        return self.batch_from_payload(
            response.json(), instrument=instrument, timeframe=timeframe,
            source_identifier=f"{url}?{params}",
        )

    def batch_from_payload(
        self,
        payload: dict[str, Any],
        *,
        instrument: str | None = None,
        timeframe: Timeframe | None = None,
        source_identifier: str = "recorded-fixture",
        drop_incomplete: bool = True,
    ) -> BarBatch:
        """Turn a v20 payload (live or recorded fixture) into a described batch."""
        frame, report = parse_candles_response(
            payload,
            instrument=instrument,
            timeframe=timeframe,
            price_basis=self.price_basis,
            drop_incomplete=drop_incomplete,
        )
        warnings: list[str] = []
        if report["incomplete_dropped"]:
            warnings.append(
                f"dropped {report['incomplete_dropped']} incomplete candle(s); "
                "signals are evaluated on closed candles only"
            )
        if report["incomplete_kept"]:
            warnings.append(
                f"KEPT {report['incomplete_kept']} INCOMPLETE candle(s) at the "
                "caller's explicit request; these bars will change and must not "
                "reach a backtest"
            )
        adjustments = (
            Adjustment(
                kind="price_basis_declaration",
                description=f"OHLC taken from the v20 {self.price_basis.value} component",
                parameters={"price_basis": self.price_basis.value},
            ),
            Adjustment(
                kind="volume_semantics",
                description=(
                    "v20 'volume' is a broker tick count, stored as tick_volume. "
                    "Traded volume is unavailable and stored as absent (-1)."
                ),
                parameters={"volume": "absent", "tick_volume": "v20_volume"},
            ),
        )
        metadata = describe_frame(
            frame,
            source="oanda-v20",
            source_identifier=source_identifier,
            timezone_of_origin="UTC",
            quality=DataQuality.RAW,
            kind=DatasetKind.RAW,
            adjustments=adjustments,
            notes="; ".join(warnings),
            extra={"parse_report": report, "host": self.host},
        )
        return BarBatch(
            frame=frame,
            metadata=metadata,
            adjustments=adjustments,
            warnings=tuple(warnings),
            extra=report,
        )
