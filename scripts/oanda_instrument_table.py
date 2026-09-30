#!/usr/bin/env python3
"""Generate ``src/fiboki/core/instruments_oanda_table.py`` from the recorded OANDA fixtures.

Usage::

    .venv/bin/python scripts/oanda_instrument_table.py            # rewrite the table
    .venv/bin/python scripts/oanda_instrument_table.py --check    # exit 1 if it would change

Reads two recorded practice-API responses (never the network):

* ``tests/fixtures/oanda/practice_instruments_2026-09-30.json``
  (``GET /v3/accounts/{id}/instruments``);
* ``tests/fixtures/oanda/practice_pricing_2026-09-29T2352Z.json``
  (``GET /v3/accounts/{id}/pricing`` for all instruments).

Every derived column is computed by :mod:`fiboki.broker.oanda_instruments`, the
one place the derivation rules live. The output is committed, and
``tests/unit/test_oanda_instruments.py`` calls :func:`render_table` on the same
fixtures and diffs against the committed file, so the table cannot drift from
the fixtures or from the rules.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
INSTRUMENTS_FIXTURE = "tests/fixtures/oanda/practice_instruments_2026-09-30.json"
PRICING_FIXTURE = "tests/fixtures/oanda/practice_pricing_2026-09-29T2352Z.json"
TABLE_PATH = REPO / "src" / "fiboki" / "core" / "instruments_oanda_table.py"

FIELDS: tuple[str, ...] = (
    # OANDA's own facts, verbatim (the citation)
    "oanda_name",
    "oanda_type",
    "margin_rate",
    "pip_location",
    "display_precision",
    "minimum_trade_size",
    "trade_units_precision",
    # derived by fiboki.broker.oanda_instruments
    "symbol",
    "asset_class",
    "base",
    "quote",
    "retail_leverage",
    "pip_size",
    "price_precision",
    "min_size",
    "size_step",
    "trading_hours",
    "annual_financing_bps",
    # the pricing snapshot
    "snapshot_spread_pips",
    "snapshot_closeout_spread_pips",
    "spread_provenance",
)


def _lit(value: Any) -> str:
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, bool):
        return repr(value)
    if isinstance(value, int | float):
        return repr(value)
    raise TypeError(f"cannot render {value!r}")


def build_rows(
    instruments_payload: dict[str, Any], pricing_payload: dict[str, Any]
) -> list[tuple[Any, ...]]:
    from decimal import ROUND_CEILING, Decimal

    from fiboki.broker.oanda_instruments import (
        instrument_from_oanda,
        parse_instruments_payload,
        parse_pricing_payload,
        spread_provenance_for,
    )

    specs = parse_instruments_payload(instruments_payload)
    snaps = parse_pricing_payload(pricing_payload)
    missing = sorted(s.name for s in specs if s.name not in snaps)
    if missing:
        raise SystemExit(f"pricing fixture has no quote for {missing}")
    rows: list[tuple[Any, ...]] = []
    for spec in specs:
        snap = snaps[spec.name]
        inst = instrument_from_oanda(spec, spread_snapshot=snap)
        closeout = snap.closeout_spread_pips(spec).quantize(
            Decimal("0.1"), rounding=ROUND_CEILING
        )
        values: dict[str, Any] = {
            "oanda_name": spec.name,
            "oanda_type": spec.type,
            "margin_rate": spec.margin_rate_text,
            "pip_location": spec.pip_location,
            "display_precision": spec.display_precision,
            "minimum_trade_size": str(spec.minimum_trade_size),
            "trade_units_precision": spec.trade_units_precision,
            "symbol": inst.symbol,
            "asset_class": inst.asset_class.value,
            "base": inst.base,
            "quote": inst.quote,
            "retail_leverage": inst.retail_leverage,
            "pip_size": inst.pip_size,
            "price_precision": inst.price_precision,
            "min_size": inst.min_size,
            "size_step": inst.size_step,
            "trading_hours": inst.trading_hours,
            "annual_financing_bps": inst.annual_financing_bps,
            "snapshot_spread_pips": inst.typical_spread_pips,
            "snapshot_closeout_spread_pips": float(closeout),
            "spread_provenance": spread_provenance_for(snap),
        }
        rows.append(tuple(values[f] for f in FIELDS))
    return rows


def render_table(instruments_payload: dict[str, Any], pricing_payload: dict[str, Any]) -> str:
    rows = build_rows(instruments_payload, pricing_payload)
    out: list[str] = [
        '"""OANDA practice instruments, 2026-09-30. GENERATED -- DO NOT EDIT.',
        "",
        "Regenerate with ``.venv/bin/python scripts/oanda_instrument_table.py``.",
        "``tests/unit/test_oanda_instruments.py`` regenerates this file from the",
        "fixtures below and fails on any difference.",
        "",
        "Columns up to ``trade_units_precision`` are OANDA's own values, verbatim",
        "(``margin_rate`` and ``minimum_trade_size`` are OANDA's decimal strings).",
        "The rest are derived by ``fiboki.broker.oanda_instruments``. The",
        "``snapshot_*`` spreads are ONE pricing snapshot at 23:52 UTC on a Tuesday",
        "(late New York / early Asia), top of book, rounded up to 0.1 pip; the",
        "closeout column is shown for comparison only and is not a trading spread.",
        '"""',
        "from __future__ import annotations",
        "",
        "OANDA_TABLE_SOURCES: tuple[str, ...] = (",
        f"    {json.dumps(INSTRUMENTS_FIXTURE)},",
        f"    {json.dumps(PRICING_FIXTURE)},",
        ")",
        "",
        "OANDA_TABLE_FIELDS: tuple[str, ...] = (",
        *[f"    {json.dumps(f)}," for f in FIELDS],
        ")",
        "",
        "# fmt: off",
        "OANDA_INSTRUMENTS_2026_09_30: tuple[tuple[object, ...], ...] = (",
        *[f"    ({', '.join(_lit(v) for v in row)})," for row in rows],
        ")",
        "# fmt: on",
        "",
    ]
    return "\n".join(out)


def load_fixtures(root: Path = REPO) -> tuple[dict[str, Any], dict[str, Any]]:
    with (root / INSTRUMENTS_FIXTURE).open(encoding="utf-8") as fh:
        instruments = json.load(fh)
    with (root / PRICING_FIXTURE).open(encoding="utf-8") as fh:
        pricing = json.load(fh)
    return instruments, pricing


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit 1 if the table is stale")
    args = parser.parse_args(argv)
    text = render_table(*load_fixtures())
    current = TABLE_PATH.read_text(encoding="utf-8") if TABLE_PATH.exists() else ""
    if args.check:
        if current != text:
            print(f"{TABLE_PATH} is stale; regenerate it", file=sys.stderr)
            return 1
        print(f"{TABLE_PATH} is current")
        return 0
    TABLE_PATH.write_text(text, encoding="utf-8")
    print(f"wrote {TABLE_PATH} ({text.count(chr(10))} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
