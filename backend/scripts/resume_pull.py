"""Resilient resume of the HistData pull for the remaining symbols.

The bulk pull hung on an exotic pair because the histdata downloader blocks on
a network call when a pair/year is unavailable instead of erroring. Setting a
global socket timeout turns those hangs into exceptions, which the provider's
per-year try/except already skips. Only the still-missing symbols are pulled;
the 46 already-ingested symbols and 861 raw zips are untouched.
"""

from __future__ import annotations

import socket
import sys

socket.setdefaulttimeout(45)  # hanging network calls now raise, not block

from fibokei.data.paths import get_canonical_dir  # noqa: E402
from fibokei.data.providers.base import ProviderID  # noqa: E402
from fibokei.data.providers.histdata import HistDataProvider  # noqa: E402
from fibokei.data.providers.symbol_map import list_mapped_symbols  # noqa: E402


def main() -> None:
    import os
    provider = HistDataProvider()
    data_dir = get_canonical_dir()
    mapped = list_mapped_symbols(ProviderID.HISTDATA)
    done = {d.lower() for d in os.listdir(data_dir / "histdata")} \
        if (data_dir / "histdata").exists() else set()
    remaining = [s for s in mapped if s.lower() not in done]
    years = list(range(2000, 2027))

    print(f"Resuming pull: {len(remaining)} remaining symbols: {remaining}",
          flush=True)

    for sym in remaining:
        print(f"\n{sym}: downloading...", flush=True)
        try:
            zips = provider.download(sym, years=years)
            print(f"  {len(zips)} zips", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"  download error: {e}", flush=True)
            zips = []
        if not zips:
            print(f"  {sym}: no data available on HistData, skipping", flush=True)
            continue
        try:
            res = provider.ingest_all_timeframes(sym, data_dir=data_dir)
            bars = sum(m.row_count for m in res.values())
            print(f"  ingested {bars:,} bars across {sorted(res)}", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"  ingest error: {e}", flush=True)

    print("\nRESUME DONE", flush=True)
    sys.stdout.flush()


if __name__ == "__main__":
    main()
