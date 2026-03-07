# FIBOKEI Coding Standards

## Strategy Rules
- All strategies must use the common Strategy base class
- Signals evaluated on closed candles only — no intrabar triggering
- No repainting logic
- No use of future data (except valid Ichimoku projected cloud)
- Every strategy outputs normalized Signal and TradePlan objects

## Data Rules
- All timestamps stored in UTC
- Canonical OHLCV schema: timestamp, open, high, low, close, volume
- Data validation before use (no gaps, no impossible values)

## Security Rules
- No plaintext passwords in code or repo
- All secrets via environment variables
- Live execution behind feature flags, disabled by default

## Testing Rules
- Deterministic backtest results (same input = same output, always)
- Every indicator has known-value unit tests
- Every strategy has signal generation tests
- Regression tests on fixed datasets

## Risk Rules
- Default risk per trade: 1.0%
- Max portfolio risk: 5%
- Max simultaneous trades: 8
- Drawdown hard stop: -4% daily, -8% weekly
