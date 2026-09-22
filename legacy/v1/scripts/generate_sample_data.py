"""Generate deterministic sample OHLCV data for testing.

Produces realistic EURUSD H1 data using a seeded random walk.
"""

import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path
import random


def generate_eurusd_h1(num_bars: int = 750, seed: int = 42) -> list[dict]:
    """Generate realistic EURUSD H1 OHLCV data."""
    rng = random.Random(seed)

    bars = []
    price = 1.0850  # Starting price
    start_time = datetime(2024, 1, 2, 0, 0, 0, tzinfo=timezone.utc)
    current_time = start_time

    for _ in range(num_bars):
        # Skip weekends (Sat=5, Sun=6)
        while current_time.weekday() >= 5:
            current_time += timedelta(hours=1)

        # Random walk with mean reversion toward 1.10
        drift = (1.10 - price) * 0.001  # Mean reversion
        volatility = 0.0008  # ~80 pips per candle max range
        change = drift + rng.gauss(0, volatility)

        open_price = price
        # Simulate intra-bar movement
        moves = [rng.gauss(0, volatility * 0.4) for _ in range(4)]
        intra_prices = [open_price]
        for m in moves:
            intra_prices.append(intra_prices[-1] + m)

        high_price = max(intra_prices) + abs(rng.gauss(0, volatility * 0.2))
        low_price = min(intra_prices) - abs(rng.gauss(0, volatility * 0.2))
        close_price = open_price + change

        # Ensure OHLC consistency
        high_price = max(high_price, open_price, close_price)
        low_price = min(low_price, open_price, close_price)

        # Volume (realistic range)
        volume = max(100, int(rng.gauss(5000, 2000)))

        bars.append({
            "timestamp": current_time.strftime("%Y-%m-%d %H:%M:%S+00:00"),
            "open": round(open_price, 5),
            "high": round(high_price, 5),
            "low": round(low_price, 5),
            "close": round(close_price, 5),
            "volume": volume,
        })

        price = close_price
        current_time += timedelta(hours=1)

    return bars


def write_csv(bars: list[dict], output_path: Path) -> None:
    """Write bars to CSV file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["timestamp", "open", "high", "low", "close", "volume"])
        writer.writeheader()
        writer.writerows(bars)
    print(f"Generated {len(bars)} bars -> {output_path}")


if __name__ == "__main__":
    project_root = Path(__file__).parent.parent
    output = project_root / "data" / "fixtures" / "sample_eurusd_h1.csv"
    bars = generate_eurusd_h1(num_bars=750)
    write_csv(bars, output)
