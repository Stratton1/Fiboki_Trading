"""Import broker-executed trades (IG transaction history) into Fiboki.

Wave 1 — broker reconciliation. Pulls IG's ``/history/transactions`` and
upserts each closed deal into the ``broker_trades`` ledger so Fiboki trade
history can show broker-executed trades with the real IG ``reference`` (e.g.
``SBQLDCAC``) and broker PnL. Idempotent: keyed on (source, reference).

Never raises on bad rows — malformed transactions are skipped and counted.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# IG transaction types that represent an actual trade (not cash/financing).
_TRADE_TYPES = {"DEAL", "TRADE"}


def parse_ig_pnl(value) -> float | None:
    """Parse IG's profitAndLoss string into a float.

    Handles forms like '£554.00', 'GBP554.00', '-£10.50', '(£10.50)',
    'E554.0', '1,234.50', or a plain number. Returns None if unparseable.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s:
        return None
    negative = s.startswith("(") and s.endswith(")")
    # Strip everything except digits, sign, and decimal point.
    cleaned = re.sub(r"[^0-9.\-]", "", s.replace(",", ""))
    if cleaned in ("", "-", "."):
        return None
    try:
        val = float(cleaned)
    except ValueError:
        return None
    return -abs(val) if negative else val


def _parse_size(value) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).replace("+", "").replace(",", ""))
    except ValueError:
        return None


def _parse_dt(value) -> datetime | None:
    if not value:
        return None
    s = str(value)
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y/%m/%d %H:%M:%S",
                "%Y-%m-%d", "%d/%m/%y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def normalize_ig_transaction(tx: dict, source: str = "ig_demo") -> dict | None:
    """Map one IG transaction dict to BrokerTrade fields, or None to skip."""
    if not isinstance(tx, dict):
        return None
    if tx.get("cashTransaction") is True:
        return None  # deposit/withdrawal/financing — not a trade
    ttype = str(tx.get("transactionType", "")).upper()
    reference = tx.get("reference") or tx.get("dealReference")
    if not reference:
        return None
    # Keep trade-like rows; if type is unknown but it has levels, treat as deal.
    if ttype and ttype not in _TRADE_TYPES and not (
        tx.get("openLevel") or tx.get("closeLevel")
    ):
        return None

    size = _parse_size(tx.get("size"))
    direction = None
    if size is not None:
        direction = "SELL" if size < 0 else "BUY"

    env = "live" if source == "ig_live" else "demo"
    return {
        "source": source,
        "broker": "ig",
        "environment": env,
        "reference": str(reference),
        "deal_id": tx.get("dealId"),
        "broker_transaction_id": tx.get("transactionId"),
        "instrument_name": tx.get("instrumentName"),
        "instrument": None,  # name→symbol mapping is best-effort, deferred
        "direction": direction,
        "size": abs(size) if size is not None else None,
        "open_level": _parse_size(tx.get("openLevel")),
        "close_level": _parse_size(tx.get("closeLevel")),
        "pnl": parse_ig_pnl(tx.get("profitAndLoss")),
        "currency": tx.get("currency"),
        "transaction_type": ttype or None,
        "opened_at": _parse_dt(tx.get("openDateUtc") or tx.get("openDate")),
        "closed_at": _parse_dt(tx.get("dateUtc") or tx.get("date")),
        "raw_json": tx,
    }


def import_ig_transactions(
    session, client, from_date: str, to_date: str | None = None,
    source: str = "ig_demo",
) -> dict:
    """Pull IG transactions and upsert them into broker_trades.

    Returns counts: {total, imported, updated, skipped}. Idempotent — re-running
    over the same window updates rows in place rather than duplicating.
    """
    from fibokei.db.repository import upsert_broker_trade

    txns = client.get_transactions(from_date, to_date)
    imported = updated = skipped = 0
    for tx in txns:
        data = normalize_ig_transaction(tx, source=source)
        if data is None:
            skipped += 1
            continue
        _, created = upsert_broker_trade(session, data)
        if created:
            imported += 1
        else:
            updated += 1
    logger.info(
        "IG transaction import (%s, from=%s): total=%d imported=%d updated=%d skipped=%d",
        source, from_date, len(txns), imported, updated, skipped,
    )
    return {
        "total": len(txns), "imported": imported,
        "updated": updated, "skipped": skipped,
    }
