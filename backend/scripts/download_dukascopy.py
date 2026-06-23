"""Download Dukascopy candle data for IG-tradeable instruments → canonical store.

Uses dukascopy-node (via npx) to pull OHLC candles over the maximum available
history, per instrument and timeframe, and writes them as canonical parquet at
data/canonical/dukascopy/<symbol>/<symbol>_<tf>.parquet. The research engine's
load_canonical() prefers Dukascopy over HistData, so this upgrades the existing
60 instruments (higher fidelity + deeper history) and adds new ones.

Robust: an unknown/unavailable instrument or timeframe is logged and skipped, so
a broad instrument list can be passed safely.

Requires node + npx on PATH (the caller sets it).

Usage:
  python scripts/download_dukascopy.py --timeframes h4,h1,m30 --from 2003-01-01
  python scripts/download_dukascopy.py --symbols EURUSD,XAUUSD --timeframes h1
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import date
from pathlib import Path

import pandas as pd

from fibokei.data.paths import get_canonical_dir

# Fiboki symbol -> dukascopy-node instrument id. FX ids are the lowercase pair;
# indices/commodities use Dukascopy's own ids (best-effort — unknowns skip).
DUKASCOPY_IDS: dict[str, str] = {
    # FX majors
    "EURUSD": "eurusd", "GBPUSD": "gbpusd", "USDJPY": "usdjpy", "AUDUSD": "audusd",
    "USDCHF": "usdchf", "USDCAD": "usdcad", "NZDUSD": "nzdusd",
    # FX crosses / G10
    "EURJPY": "eurjpy", "GBPJPY": "gbpjpy", "EURGBP": "eurgbp", "AUDJPY": "audjpy",
    "EURAUD": "euraud", "AUDCAD": "audcad", "AUDCHF": "audchf", "AUDNZD": "audnzd",
    "CADCHF": "cadchf", "CADJPY": "cadjpy", "CHFJPY": "chfjpy", "EURCAD": "eurcad",
    "EURCHF": "eurchf", "EURNZD": "eurnzd", "GBPAUD": "gbpaud", "GBPCAD": "gbpcad",
    "GBPCHF": "gbpchf", "GBPNZD": "gbpnzd", "NZDCAD": "nzdcad", "NZDCHF": "nzdchf",
    "NZDJPY": "nzdjpy", "SGDJPY": "sgdjpy",
    # FX Scandi / EM
    "USDNOK": "usdnok", "USDSEK": "usdsek", "EURNOK": "eurnok", "EURSEK": "eursek",
    "USDSGD": "usdsgd", "USDHKD": "usdhkd", "USDTRY": "usdtry", "USDMXN": "usdmxn",
    "USDZAR": "usdzar", "USDPLN": "usdpln", "USDCZK": "usdczk", "USDHUF": "usdhuf",
    "ZARJPY": "zarjpy", "EURTRY": "eurtry", "EURPLN": "eurpln", "EURCZK": "eurczk",
    "EURHUF": "eurhuf", "EURDKK": "eurdkk",
    # Metals
    "XAUUSD": "xauusd", "XAGUSD": "xagusd",
    # Energy
    "WTIUSD": "lightcmdusd", "BCOUSD": "brentcmdusd", "NATGAS": "gascmdusd",
    # Indices
    "US500": "usa500idxusd", "US100": "usatechidxusd", "US30": "usa30idxusd",
    "UK100": "gbridxgbp", "DE40": "deuidxeur", "JP225": "jpnidxjpy",
    "CAC40": "fraidxeur", "AU200": "aus200idxaud", "HK50": "hkgidxhkd",
    # Extra IG-tradeable indices Dukascopy carries (best-effort ids)
    "EUSTX50": "eusidxeur", "ESP35": "espidxeur", "ITA40": "itaidxeur",
    "NLD25": "nldidxeur", "SWI20": "swiidxchf", "SE30": "swedishidxsek",
}


def _download_one(dukas_id: str, tf: str, frm: str, to: str, out_dir: Path) -> Path | None:
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = ["npx", "--yes", "dukascopy-node@latest", "-i", dukas_id,
           "-from", frm, "-to", to, "-t", tf, "-f", "csv", "-v", "true",
           "-dir", str(out_dir)]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=1800)
    except subprocess.CalledProcessError as e:
        print(f"    ! {dukas_id} {tf}: download failed ({(e.stderr or '')[:120]})",
              flush=True)
        return None
    except subprocess.TimeoutExpired:
        print(f"    ! {dukas_id} {tf}: timed out", flush=True)
        return None
    files = sorted(out_dir.glob(f"{dukas_id}-{tf}-*.csv"))
    return files[-1] if files else None


def _to_canonical(csv_path: Path, symbol: str, tf: str) -> int:
    df = pd.read_csv(csv_path)
    if "timestamp" not in df.columns or df.empty:
        return 0
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    if "volume" not in df.columns:
        df["volume"] = 0.0
    df = df.set_index("timestamp").sort_index()
    df = df[["open", "high", "low", "close", "volume"]].dropna(subset=["open"])
    df = df[~df.index.duplicated(keep="first")]
    out = get_canonical_dir() / "dukascopy" / symbol.lower()
    out.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out / f"{symbol.lower()}_{tf}.parquet")
    return len(df)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="all")
    ap.add_argument("--timeframes", default="h4,h1,m30")
    ap.add_argument("--from", dest="frm", default="2003-01-01")
    ap.add_argument("--to", default=str(date.today()))
    ap.add_argument("--raw-dir", default="data/raw/dukascopy")
    args = ap.parse_args()

    symbols = (list(DUKASCOPY_IDS) if args.symbols == "all"
               else [s.strip().upper() for s in args.symbols.split(",")])
    timeframes = [t.strip().lower() for t in args.timeframes.split(",")]
    raw = Path(args.raw_dir)

    print(f"Dukascopy pull: {len(symbols)} symbols x {timeframes} "
          f"from {args.frm} to {args.to}", flush=True)
    ok_syms, total_bars = 0, 0
    for sym in symbols:
        dukas_id = DUKASCOPY_IDS.get(sym)
        if not dukas_id:
            print(f"{sym}: no dukascopy id, skip", flush=True)
            continue
        print(f"{sym} ({dukas_id}):", flush=True)
        sym_bars = 0
        for tf in timeframes:
            csv_path = _download_one(dukas_id, tf, args.frm, args.to, raw / sym.lower())
            if csv_path is None:
                continue
            n = _to_canonical(csv_path, sym, tf)
            sym_bars += n
            print(f"    {tf}: {n:,} bars", flush=True)
        if sym_bars:
            ok_syms += 1
            total_bars += sym_bars
    print(f"\nDone. {ok_syms}/{len(symbols)} symbols, {total_bars:,} bars total.",
          flush=True)
    sys.stdout.flush()


if __name__ == "__main__":
    main()
