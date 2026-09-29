"""Named macro dataset packs: several series fetched as one point-in-time dataset.

A pack is a declared list of series from ONE macro provider, stored as one
content-addressed :class:`~fiboki.data.providers.macro_base.MacroDataset`
(``dataset_key`` = the pack name) so a research run can cite a single
version id for its whole cross-asset context.

``fred_cross_asset_daily`` goes through the existing ALFRED provider, so
every row is VINTAGE-stamped (``available_at`` = 00:00 America/Chicago on the
day after the vintage began; see ``alfred.py``). For these daily market
series that is up to a day later than the true print, deliberately.

Terms, per series (FRED series pages read 2026-09-29; FRED API terms apply to
all, https://fred.stlouisfed.org/docs/api/terms_of_use.html):

* DGS2, DGS10 (H.15) and DTWEXBGS (H.10): Board of Governors of the Federal
  Reserve System; the Board's site states its information "is in the public
  domain and may be copied and distributed without permission"
  (https://www.federalreserve.gov/disclaimer.htm).
* DCOILWTICO: U.S. Energy Information Administration; "U.S. government
  publications are in the public domain" (https://www.eia.gov/about/copyrights_reuse.php).
* VIXCLS: "Copyright, 2016, Chicago Board Options Exchange, Inc. Reprinted with
  permission." Third-party copyright: personal research only, never
  redistributed. This is the one ``personal_only`` series in the pack.

FRED's series pages carry a generic "Data in this graph are copyrighted"
banner on every series; the per-series notes above are what decide.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pandas as pd

from fiboki.data.providers.base import ProviderError
from fiboki.data.providers.macro_base import MacroDataset, macro_frame

__all__ = [
    "FRED_CROSS_ASSET_DAILY",
    "MACRO_DATASET_PACKS",
    "MacroDatasetPack",
    "PackSeries",
    "fetch_pack",
]

PACK_PARSER_VERSION = "fiboki.data.providers.fred_pack/1"


@dataclass(frozen=True, slots=True)
class PackSeries:
    series_id: str
    description: str
    publisher: str
    terms_url: str
    terms_status: str


@dataclass(frozen=True, slots=True)
class MacroDatasetPack:
    name: str
    provider: str
    description: str
    cadence: str
    series: tuple[PackSeries, ...]

    @property
    def series_ids(self) -> tuple[str, ...]:
        return tuple(s.series_id for s in self.series)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "provider": self.provider, "description": self.description,
            "cadence": self.cadence,
            "series": [{"series_id": s.series_id, "description": s.description,
                        "publisher": s.publisher, "terms_url": s.terms_url,
                        "terms_status": s.terms_status} for s in self.series],
        }


_FED = "https://www.federalreserve.gov/disclaimer.htm"

FRED_CROSS_ASSET_DAILY = MacroDatasetPack(
    name="fred_cross_asset_daily",
    provider="alfred",
    description="Daily cross-asset context for FX, gold and indices: US 2y and 10y yields, "
                "broad trade-weighted dollar, VIX, WTI crude. Vintage-aware via ALFRED.",
    cadence="daily (US business days); fetch after 00:00 America/Chicago",
    series=(
        PackSeries("DGS2", "2-Year Treasury constant maturity yield (H.15)",
                   "Board of Governors of the Federal Reserve System", _FED, "permitted"),
        PackSeries("DGS10", "10-Year Treasury constant maturity yield (H.15)",
                   "Board of Governors of the Federal Reserve System", _FED, "permitted"),
        PackSeries("DTWEXBGS", "Nominal broad US dollar index (H.10)",
                   "Board of Governors of the Federal Reserve System", _FED, "permitted"),
        PackSeries("VIXCLS", "CBOE Volatility Index, daily close",
                   "Chicago Board Options Exchange (copyright, reprinted by FRED with permission)",
                   "https://fred.stlouisfed.org/series/VIXCLS", "personal_only"),
        PackSeries("DCOILWTICO", "WTI crude oil spot, Cushing OK",
                   "U.S. Energy Information Administration",
                   "https://www.eia.gov/about/copyrights_reuse.php", "permitted"),
    ),
)

#: name -> pack, for listings and the source registry.
MACRO_DATASET_PACKS: dict[str, MacroDatasetPack] = {FRED_CROSS_ASSET_DAILY.name: FRED_CROSS_ASSET_DAILY}


def fetch_pack(pack: MacroDatasetPack, provider: Any, *, now: pd.Timestamp | None = None) -> MacroDataset:
    """Fetch every series of ``pack`` through ``provider`` and combine them.

    All or nothing: a series that fails raises, because a pack missing one
    series silently would be a different dataset under the same name.
    """
    if getattr(provider, "descriptor", None) is None or provider.descriptor.name != pack.provider:
        raise ProviderError(f"pack {pack.name} needs the {pack.provider!r} provider")
    fetched_at = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now).tz_convert("UTC")
    frames = []
    reports: dict[str, Any] = {}
    for sid in pack.series_ids:
        ds = provider.fetch(sid, now=fetched_at)
        frames.append(ds.frame)
        reports[sid] = ds.report
    rows = pd.concat(frames, ignore_index=True).to_dict(orient="records")
    for r in rows:  # macro_frame re-validates and re-sorts deterministically
        r["attributes"] = json.loads(r["attributes"])
        for col in ("available_at", "superseded_at"):
            if pd.isna(r[col]):
                r[col] = None
    frame = macro_frame(rows)
    descriptor = provider.descriptor
    return MacroDataset(
        provider=pack.provider,
        dataset_key=pack.name,
        frame=frame,
        descriptor=descriptor,
        request={"pack": pack.name, "series": list(pack.series_ids)},
        parser_version=PACK_PARSER_VERSION,
        fetched_at=fetched_at,
        report={"series": reports, "rows": len(frame), "pack": pack.to_dict(),
                "attribution": descriptor.attribution},
    )
