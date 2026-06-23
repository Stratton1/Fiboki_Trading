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
from fibokei.data.providers.resampler import resample_ohlcv

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


def _download_one(dukas_id: str, tf: str, frm: str, to: str, out_dir: Path,
                  retries: int = 1) -> Path | None:
    out_dir.mkdir(parents=True, exist_ok=True)
    # Clear stale CSVs for this id/tf so we never pick up a previous run's file.
    for old in out_dir.glob(f"{dukas_id}-{tf}-*.csv"):
        old.unlink(missing_ok=True)
    cmd = ["npx", "--yes", "dukascopy-node@latest", "-i", dukas_id,
           "-from", frm, "-to", to, "-t", tf, "-f", "csv", "-v", "true",
           "-dir", str(out_dir)]
    for attempt in range(retries + 1):
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=600)
            files = sorted(out_dir.glob(f"{dukas_id}-{tf}-*.csv"))
            if files:
                return files[-1]
        except subprocess.CalledProcessError as e:
            if attempt >= retries:
                print(f"    ! {dukas_id} {tf}: failed ({(e.stderr or '')[:100]})",
                      flush=True)
        except subprocess.TimeoutExpired:
            if attempt >= retries:
                print(f"    ! {dukas_id} {tf}: timed out", flush=True)
    return None


def _read_csv(csv_path: Path) -> pd.DataFrame | None:
    df = pd.read_csv(csv_path)
    if "timestamp" not in df.columns or df.empty:
        return None
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    if "volume" not in df.columns:
        df["volume"] = 0.0
    df = df.set_index("timestamp").sort_index()
    df = df[["open", "high", "low", "close", "volume"]].dropna(subset=["open"])
    return df[~df.index.duplicated(keep="first")]


def _write(df: pd.DataFrame, symbol: str, tf: str) -> int:
    out = get_canonical_dir() / "dukascopy" / symbol.lower()
    out.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out / f"{symbol.lower()}_{tf}.parquet")
    return len(df)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="all")
    ap.add_argument("--from", dest="frm", default="2003-01-01",
                    help="start for H1 (H4 is derived from H1)")
    ap.add_argument("--m30-from", default="2015-01-01",
                    help="start for M30 (shorter window — M30 over 20y times out)")
    ap.add_argument("--to", default=str(date.today()))
    ap.add_argument("--raw-dir", default="data/raw/dukascopy")
    args = ap.parse_args()

    symbols = (list(DUKASCOPY_IDS) if args.symbols == "all"
               else [s.strip().upper() for s in args.symbols.split(",")])
    raw = Path(args.raw_dir)

    print(f"Dukascopy pull: {len(symbols)} symbols | H1+H4(derived) from "
          f"{args.frm}, M30 from {args.m30_from}", flush=True)
    ok_syms, total_bars = 0, 0
    for sym in symbols:
        dukas_id = DUKASCOPY_IDS.get(sym)
        if not dukas_id:
            print(f"{sym}: no dukascopy id, skip", flush=True)
            continue
        # Resume: skip symbols already pulled (h1 parquet present with real depth).
        existing = get_canonical_dir() / "dukascopy" / sym.lower() / f"{sym.lower()}_h1.parquet"
        if existing.exists():
            try:
                if len(pd.read_parquet(existing, columns=["close"])) > 5000:
                    print(f"{sym}: already pulled, skip", flush=True)
                    ok_syms += 1
                    continue
            except Exception:
                pass
        print(f"{sym} ({dukas_id}):", flush=True)
        sym_bars = 0
        # H1 (reliable) → write H1 + derive H4
        h1_csv = _download_one(dukas_id, "h1", args.frm, args.to, raw / sym.lower())
        if h1_csv is not None:
            h1 = _read_csv(h1_csv)
            if h1 is not None and len(h1):
                sym_bars += _write(h1, sym, "h1")
                print(f"    h1: {len(h1):,} bars", flush=True)
                h4 = resample_ohlcv(h1, "H4")
                sym_bars += _write(h4, sym, "h4")
                print(f"    h4: {len(h4):,} bars (derived)", flush=True)
        # M30 (recent window only)
        m30_csv = _download_one(dukas_id, "m30", args.m30_from, args.to, raw / sym.lower())
        if m30_csv is not None:
            m30 = _read_csv(m30_csv)
            if m30 is not None and len(m30):
                sym_bars += _write(m30, sym, "m30")
                print(f"    m30: {len(m30):,} bars", flush=True)
        if sym_bars:
            ok_syms += 1
            total_bars += sym_bars
    print(f"\nDone. {ok_syms}/{len(symbols)} symbols, {total_bars:,} bars total.",
          flush=True)
    sys.stdout.flush()


if __name__ == "__main__":
    main()
