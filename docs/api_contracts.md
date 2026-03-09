# Fiboki API Contracts

Base URL: `{BACKEND_HOST}/api/v1`

All endpoints except `/system/health` and `/health` require authentication via the `fibokei_token` cookie or a Bearer token. See [docs/auth_spec.md](auth_spec.md) for details.

---

## 1. Auth

### POST /auth/login

Login and receive an auth cookie.

**Request:** `application/x-www-form-urlencoded` (OAuth2 form)

| Field | Type | Required |
|-------|------|----------|
| `username` | string | Yes |
| `password` | string | Yes |

**Response:** `200 OK` + `Set-Cookie: fibokei_token=<jwt>`

```json
{
  "access_token": "eyJ...",
  "token_type": "bearer"
}
```

**Errors:** `401` -- Incorrect username or password.

### POST /auth/logout

Clear the auth cookie.

**Response:** `200 OK`

```json
{ "detail": "Logged out" }
```

### GET /auth/me

Get the currently authenticated user.

**Response:** `200 OK`

```json
{
  "user_id": 1,
  "username": "joe",
  "role": "admin"
}
```

**Errors:** `401` -- Not authenticated. `404` -- User not found.

---

## 2. Market Data

### GET /market-data/{instrument}/{timeframe}

Returns OHLCV candles with precomputed Ichimoku Cloud data.

**Path parameters:**

| Param | Example |
|-------|---------|
| `instrument` | `EURUSD` |
| `timeframe` | `H1` |

**Response:** `200 OK`

```json
{
  "instrument": "EURUSD",
  "timeframe": "H1",
  "candles": [
    {
      "timestamp": 1704067200000,
      "open": 1.1000,
      "high": 1.1050,
      "low": 1.0980,
      "close": 1.1030,
      "volume": 0.0
    }
  ],
  "ichimoku": [
    {
      "timestamp": 1704067200000,
      "tenkan": 1.1010,
      "kijun": 1.1005,
      "senkou_a": 1.1020,
      "senkou_b": 1.0990,
      "chikou": 1.1030
    }
  ]
}
```

**Errors:** `404` -- Unknown instrument or no data file. `400` -- Invalid timeframe.

---

## 3. Chart Annotations

### GET /charts/annotations/{backtest_id}

Returns trade markers and strategy annotations for rendering on KLineChart.

**Response:** `200 OK`

```json
{
  "trade_markers": [
    {
      "trade_id": "42",
      "strategy_id": "bot01_sanyaku",
      "direction": "LONG",
      "entry": { "timestamp": "2025-01-01T00:00:00", "price": 1.1000 },
      "exit": { "timestamp": "2025-01-01T06:00:00", "price": 1.1080 },
      "stop_loss": [],
      "take_profit": [],
      "partial_exits": [],
      "label": "LONG take_profit_hit",
      "outcome": "win"
    }
  ],
  "strategy_annotations": []
}
```

**Errors:** `404` -- Backtest run not found.

---

## 4. Backtests

### POST /backtests/run

Run a backtest synchronously and persist the results.

**Request:**

```json
{
  "strategy_id": "bot01_sanyaku",
  "instrument": "EURUSD",
  "timeframe": "H1",
  "data_path": null,
  "config_overrides": null
}
```

**Response:** `200 OK` -- `BacktestSummaryResponse`

```json
{
  "id": 1,
  "strategy_id": "bot01_sanyaku",
  "instrument": "EURUSD",
  "timeframe": "H1",
  "start_date": "2024-01-01T00:00:00",
  "end_date": "2024-12-31T00:00:00",
  "total_trades": 120,
  "net_profit": 2450.50,
  "sharpe_ratio": 1.85,
  "max_drawdown_pct": 8.2
}
```

**Errors:** `400` -- Unknown strategy, invalid timeframe, or data file not found.

### GET /backtests

List past backtest runs with optional filtering.

**Query parameters:**

| Param | Type | Default |
|-------|------|---------|
| `strategy_id` | string | (all) |
| `instrument` | string | (all) |
| `timeframe` | string | (all) |
| `limit` | int (1-1000) | 100 |

**Response:** `200 OK` -- `BacktestSummaryResponse[]`

### GET /backtests/{run_id}

Get full backtest result with all metrics.

**Response:** `200 OK` -- `BacktestDetailResponse`

```json
{
  "id": 1,
  "strategy_id": "bot01_sanyaku",
  "instrument": "EURUSD",
  "timeframe": "H1",
  "start_date": "2024-01-01T00:00:00",
  "end_date": "2024-12-31T00:00:00",
  "total_trades": 120,
  "net_profit": 2450.50,
  "sharpe_ratio": 1.85,
  "max_drawdown_pct": 8.2,
  "config_json": { "initial_capital": 10000 },
  "metrics_json": { "win_rate": 0.62, "profit_factor": 1.95 }
}
```

**Errors:** `404` -- Backtest run not found.

### GET /backtests/{run_id}/trades

Get paginated trade list for a backtest.

**Query parameters:**

| Param | Type | Default |
|-------|------|---------|
| `page` | int (>= 1) | 1 |
| `size` | int (1-500) | 50 |

**Response:** `200 OK` -- `TradeListResponse`

```json
{
  "items": [
    {
      "id": 1,
      "strategy_id": "bot01_sanyaku",
      "instrument": "EURUSD",
      "direction": "LONG",
      "entry_time": "2024-01-15T09:00:00",
      "entry_price": 1.0950,
      "exit_time": "2024-01-15T15:00:00",
      "exit_price": 1.1010,
      "exit_reason": "take_profit_hit",
      "pnl": 60.0,
      "bars_in_trade": 6
    }
  ],
  "total": 120,
  "page": 1,
  "size": 50
}
```

### GET /backtests/{run_id}/equity-curve

Get equity curve data points for charting.

**Response:** `200 OK`

```json
{
  "equity_curve": [10000.0, 10060.0, 10020.0, 10150.0]
}
```

---

## 5. Research

### POST /research/run

Run research matrix across strategy x instrument x timeframe combinations.

**Request:**

```json
{
  "strategy_ids": ["bot01_sanyaku", "bot02_kijun_pullback"],
  "instruments": ["EURUSD", "GBPUSD"],
  "timeframes": ["H1", "H4"],
  "data_dir": null,
  "initial_capital": 10000.0,
  "risk_per_trade_pct": 1.0
}
```

**Response:** `200 OK` -- `ResearchRunSummary`

```json
{
  "run_id": "a1b2c3d4",
  "total_combinations": 8,
  "completed": 6,
  "top_result": {
    "id": 1,
    "run_id": "a1b2c3d4",
    "strategy_id": "bot01_sanyaku",
    "instrument": "EURUSD",
    "timeframe": "H1",
    "composite_score": 0.87,
    "rank": 1,
    "metrics_json": {},
    "created_at": "2026-03-07T12:00:00"
  }
}
```

### GET /research/rankings

Get ranked research results.

**Query parameters:**

| Param | Type | Default |
|-------|------|---------|
| `sort_by` | `composite_score` or `rank` | `composite_score` |
| `limit` | int (1-500) | 50 |

**Response:** `200 OK` -- `ResearchResultResponse[]`

### POST /research/compare

Compare specific strategy-instrument-timeframe combinations.

**Request:**

```json
{
  "combos": ["bot01_sanyaku:EURUSD:H1", "bot02_kijun_pullback:GBPUSD:H4"]
}
```

**Response:** `200 OK` -- `ResearchResultResponse[]`

**Errors:** `404` -- No matching research results found.

---

## 6. Paper Trading

### POST /paper/bots

Create and start a paper trading bot.

**Request:**

```json
{
  "strategy_id": "bot01_sanyaku",
  "instrument": "EURUSD",
  "timeframe": "H1",
  "risk_pct": 1.0
}
```

**Response:** `200 OK`

```json
{
  "bot_id": "uuid-string",
  "strategy_id": "bot01_sanyaku",
  "instrument": "EURUSD",
  "timeframe": "H1",
  "state": "running"
}
```

### GET /paper/bots

List all paper trading bots.

**Response:** `200 OK` -- `BotStatusResponse[]`

```json
[
  {
    "bot_id": "uuid-string",
    "strategy_id": "bot01_sanyaku",
    "instrument": "EURUSD",
    "timeframe": "H1",
    "state": "running",
    "bars_seen": 150,
    "has_position": true,
    "position": {}
  }
]
```

### GET /paper/bots/{bot_id}

Get bot detail.

**Response:** `200 OK` -- `BotStatusResponse`

**Errors:** `404` -- Bot not found.

### POST /paper/bots/{bot_id}/stop

Stop a paper trading bot.

**Response:** `200 OK`

```json
{ "bot_id": "uuid-string", "state": "stopped" }
```

### POST /paper/bots/{bot_id}/pause

Pause a paper trading bot.

**Response:** `200 OK`

```json
{ "bot_id": "uuid-string", "state": "paused" }
```

### GET /paper/account

Get paper trading account overview.

**Response:** `200 OK`

```json
{
  "balance": 10250.00,
  "equity": 10300.00,
  "initial_balance": 10000.00,
  "total_pnl": 250.00,
  "total_pnl_pct": 2.5,
  "daily_pnl": 50.00,
  "weekly_pnl": 150.00,
  "open_positions": 1,
  "total_trades": 15
}
```

---

## 7. Trades

### GET /trades/

List trades with optional filters and pagination.

**Query parameters:**

| Param | Type | Default |
|-------|------|---------|
| `strategy_id` | string | (all) |
| `instrument` | string | (all) |
| `direction` | string | (all) |
| `page` | int (>= 1) | 1 |
| `size` | int (1-200) | 50 |

**Response:** `200 OK` -- `TradeListResponse`

```json
{
  "items": [
    {
      "id": 1,
      "strategy_id": "bot01_sanyaku",
      "instrument": "EURUSD",
      "direction": "LONG",
      "entry_time": "2024-01-15T09:00:00",
      "entry_price": 1.0950,
      "exit_time": "2024-01-15T15:00:00",
      "exit_price": 1.1010,
      "exit_reason": "take_profit_hit",
      "pnl": 60.0,
      "bars_in_trade": 6,
      "backtest_run_id": 1
    }
  ],
  "total": 200,
  "page": 1,
  "size": 50
}
```

### GET /trades/{trade_id}

Get a single trade by ID.

**Response:** `200 OK` -- `TradeResponse`

**Errors:** `404` -- Trade not found.

---

## 8. System

### GET /system/health

Public health check (no auth required).

**Response:** `200 OK`

```json
{ "status": "ok", "version": "1.0.0" }
```

### GET /system/status

Protected system status.

**Response:** `200 OK`

```json
{
  "api_version": "1.0.0",
  "database": "connected",
  "paper_engine": "standby",
  "strategies_loaded": 12
}
```

---

## 9. Instruments

### GET /instruments

List all available instruments. Supports optional `asset_class` query parameter for filtering.

**Query parameters:**

| Param | Type | Default |
|-------|------|---------|
| `asset_class` | string | (all) |

**Response:** `200 OK`

```json
[
  {
    "symbol": "EURUSD",
    "name": "Euro / US Dollar",
    "asset_class": "forex_major",
    "has_canonical_data": true
  },
  {
    "symbol": "BTCUSD",
    "name": "Bitcoin / US Dollar",
    "asset_class": "crypto",
    "has_canonical_data": false
  }
]
```

Valid `asset_class` values: `forex_major`, `forex_cross`, `forex_g10_cross`, `forex_scandinavian`, `forex_em`, `commodity_metal`, `commodity_energy`, `index`, `crypto`.

`has_canonical_data` indicates whether the instrument has HistData canonical datasets available. Instruments with `false` require alternate data providers.

### GET /instruments/{symbol}

Get a single instrument.

**Response:** `200 OK` -- `InstrumentResponse`

**Errors:** `404` -- Instrument not found.

---

## 10. Strategies

### GET /strategies

List all available strategies.

**Response:** `200 OK`

```json
[
  {
    "id": "bot01_sanyaku",
    "name": "Sanyaku",
    "family": "ichimoku",
    "complexity": "basic",
    "supports_long": true,
    "supports_short": true,
    "requires_fibonacci": false,
    "requires_mtfa": false
  }
]
```

### GET /strategies/{strategy_id}

Get strategy detail with required indicators and valid regimes.

**Response:** `200 OK`

```json
{
  "id": "bot01_sanyaku",
  "name": "Sanyaku",
  "family": "ichimoku",
  "complexity": "basic",
  "supports_long": true,
  "supports_short": true,
  "requires_fibonacci": false,
  "requires_mtfa": false,
  "valid_market_regimes": ["trending"],
  "required_indicators": ["ichimoku", "atr"]
}
```

**Errors:** `404` -- Strategy not found.

---

## 11. Legacy Health Check

### GET /health

Duplicate health check on the auth router (retained for backward compatibility).

**Response:** `200 OK`

```json
{ "status": "ok", "version": "1.0.0" }
```

---

## 12. Frontend Contract Types

The TypeScript types in `frontend/src/types/contracts/` mirror these schemas. See [docs/frontend_architecture.md](frontend_architecture.md) for the type file listing.
