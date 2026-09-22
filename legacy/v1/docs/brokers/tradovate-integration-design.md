# Tradovate integration — design notes (Phase 1)

This document records the deliberate design decisions and known approximations
for the Tradovate broker adapter that ships in Phase 1 of Fiboki's multi-broker
fan-out execution architecture. It complements `docs/brokers/README.md` (the
multi-broker overview) and is the source of truth for what is shipped vs.
stubbed for Tradovate specifically.

## Status at end of Phase 1

| Concern | State |
| --- | --- |
| `TradovateExecutionAdapter` implements `ExecutionAdapter` | Shipped |
| Demo / sandbox URL default | Shipped |
| Live URL hard-block (multi-flag gate) | Shipped |
| Auth, session, request envelope, error class | Shipped |
| Symbol → contract resolver | Stub (env-driven, no API call) |
| Real `/contract/find` front-month resolution | Stub — flagged `TODO_VERIFY` |
| `place_order` happy path against placeholder endpoints | Shipped |
| `cancel_order`, `close_position`, `partial_close` | Shipped (close uses opposite-side market order) |
| `modify_order` | Returns `NOT_IMPLEMENTED` |
| Streaming socket integration | Out of scope |
| Bracket/OCO server-side stops | Out of scope |
| Any real Tradovate demo smoke test | Pending operator credentials + endpoint verification |

The adapter is **demo-ready in shape**: the router can dispatch to it, the
bot can hand it normalised plans, and the audit log captures everything. It
is not yet **demo-verified** — the placeholder URLs and payload shapes need
a single round of confirmation against the current Tradovate REST docs once
operator credentials are available. Every such site is marked `TODO_VERIFY`
in the source so the verification scope is explicit.

## Environments and base URLs

```
Demo / sandbox: https://demo.tradovateapi.com/v1   (default)
Live           : https://live.tradovateapi.com/v1   (hard-blocked — see below)
Override       : FIBOKEI_TRADOVATE_BASE_URL=...      (used by tests / stubs)
```

**Live is blocked unless all of the following are true at request time:**

1. `FIBOKEI_TRADOVATE_ENV=live`
2. `FIBOKEI_TRADOVATE_LIVE_ALLOWED=true`
3. `FIBOKEI_LIVE_EXECUTION_ENABLED=true`
4. The router-side `live_allowed` field on the `ResolvedTarget` is True
5. The global kill switch is not active

If any one of these fails, `TradovateClient._ensure_env_allowed()` raises
`TradovateClientError(error_code="LIVE_BLOCKED")` before any HTTP call.
This mirrors the production block in `IGClient._ensure_demo_only()`.

## Authentication

```
POST {base}/auth/accessTokenRequest
{
  "name": "<username>",
  "password": "<password>",
  "appId": "<app id>",
  "appVersion": "<app version>",
  "cid": <numeric cid>,
  "sec": "<api secret>",
  "deviceId": "<stable device id>"
}
```

Returns an `accessToken` (REST) and `mdAccessToken` (market-data socket — not
used in Phase 1). The session is considered valid for 75 minutes; we
re-authenticate one minute before the broker-reported `expirationTime`.

Credentials are read from environment variables only and are never logged
or echoed in any error message. `test_tradovate_safety.py::test_credentials_not_in_str_or_repr`
asserts this.

`TODO_VERIFY` for the first real demo run:

* exact path (`/auth/accessTokenRequest`)
* exact payload field names (`name`, `cid`, `sec` vs. alternatives)
* exact response field names (`accessToken`, `expirationTime`, `userId`)

These are written to match the public Tradovate REST documentation but
have not been exercised against a live demo account in this codebase.

## Endpoints used

| Concern | Path | Method |
| --- | --- | --- |
| Auth | `/auth/accessTokenRequest` | POST |
| List accounts | `/account/list` | GET |
| Cash balance | `/cashBalance/getCashBalanceSnapshot` | POST |
| List positions | `/position/list` | GET |
| Place order | `/order/placeOrder` | POST |
| Cancel order | `/order/cancelOrder` | POST |

Each call is wrapped in `TradovateClientError` with a stable `error_code`
suitable for audit-log filtering.

## Instrument / contract mapping

The brief is explicit: **never guess contract mapping.** The Phase 1
default mapping is therefore empty, and the resolver returns
`UnsupportedSymbol(code="UNSUPPORTED_INSTRUMENT_TRADOVATE")` for every
Fiboki symbol unless the operator has explicitly mapped it.

### Operator-controlled mapping

```
FIBOKEI_TRADOVATE_SYMBOL_MAP="US500:ES,US100:NQ,XAUUSD:MGC"
FIBOKEI_TRADOVATE_FRONT_MONTH="M6"
```

- `FIBOKEI_TRADOVATE_SYMBOL_MAP` is a comma-separated list of
  `FIBOKI_SYMBOL:PRODUCT_CODE` pairs.
- `FIBOKEI_TRADOVATE_FRONT_MONTH` is the contract-month suffix appended to
  the product code (e.g. `M6` for June 2026 → `ESM6`).

The resolver will only produce a contract if both the symbol mapping and
the front-month suffix are configured. Otherwise the trade is rejected
with `MISSING_FRONT_MONTH` or `UNSUPPORTED_INSTRUMENT_TRADOVATE` and the
fan-out attempt is recorded as `skipped`.

### Candidate mappings — DOCUMENTED ONLY, NOT ACTIVATED

| Fiboki symbol | Standard | Micro | Notes |
| --- | --- | --- | --- |
| US500 | ES | MES | E-mini / Micro E-mini S&P 500 |
| US100 | NQ | MNQ | E-mini / Micro Nasdaq-100 |
| US30 | YM | MYM | E-mini / Micro Dow |
| XAUUSD | GC | MGC | Gold futures (CME) |
| WTIUSD | CL | MCL | Crude Oil |
| NATGAS | NG | MNG | Natural Gas |

These are listed for operator reference. They are **not** added to the
default map because:

- Each requires individual capital sizing and risk review.
- Standard vs. micro selection is a deliberate operator choice
  (different point values, different margin).
- Front-month rotation (e.g. quarterly for indices) requires
  operational discipline this design does not yet automate.

### FX pairs are explicitly NOT mapped

EURUSD, GBPUSD, USDJPY, etc. are forex pairs on IG but, on Tradovate,
would map to CME currency futures (6E, 6B, 6J) with completely different
lot economics. Until an operator deliberately configures sizing for
those products, the resolver returns `UNSUPPORTED_INSTRUMENT_TRADOVATE`
and the audit log records a clean skip.

### Front-month rotation

Phase 1 uses a static `FIBOKEI_TRADOVATE_FRONT_MONTH` suffix. This is
fine for short-term verification but will need to be replaced before
serious use. A future `LiveContractResolver` should call
`/contract/find` to pull the active front-month based on volume and
expiry. This is a stub in `broker_symbols.py` flagged `TODO_VERIFY`.

## Sizing

The router computes size per target using `calculate_target_size` in
`execution/sizing.py`. For Tradovate targets specifically:

1. Compute the standard sizing via the canonical `calculate_position_size`
   (which already enforces IG-aligned leverage caps and per-instrument
   pip adjustments).
2. **Round down to whole contracts** (`int(raw)`).
3. **Reject if the result is zero contracts.** No silent approximation.

This mirrors the brief's explicit instruction:

> Futures size rounds down to whole contracts and rejects if zero.

Per-target capital is taken from the operator-set `allocated_capital`,
**not** from the broker-reported balance. This is deliberate (decision #1
in the Phase 1 sign-off). Broker balance/equity is exposed only as
informational health-check metadata.

## Order semantics

### Direction codes

The router builds an order with `direction` ∈ `{BUY, SELL}` (mapped from
strategy LONG/SHORT inside the bot). Inside the Tradovate adapter we
re-map to literal `Buy` / `Sell` because the Tradovate API is
case-sensitive on this field.

### Order type

Phase 1 sends only `Market` orders. Stop-loss and take-profit are tracked
client-side via the bot's `Position` object. Broker-side OCO/OSO bracket
orders are out of scope for Phase 1.

This is an honest limitation: if Fiboki crashes between order placement
and exit, the broker has no stop-loss in place. Operators must rely on
Fiboki's automatic resume, the global kill switch, and per-account
position size limits to bound risk. A later phase will add bracket
orders so the broker enforces stops independently.

### Close-on-exit

When the bot decides to exit, the router calls
`adapter.close_position(deal_id)`. Tradovate has no native single-call
"close" — the adapter looks up the cached open record (action + size)
and submits an opposite-side market order with the same quantity.

If two brokers were filled and only one is still open at the broker
(e.g. one stopped out, one is still open), the bot's `_target_deal_ids`
map only contains the still-open deals. Closes are sent only to those.

## Audit shape

Every Tradovate execution attempt produces one row in the existing
`execution_audit` table with:

- `execution_mode = "tradovate_demo"` (or `"tradovate_live"` when live)
- `status` ∈ `{success, rejected, failed, paper_only, unknown}`
- `detail_json.parent_signal_id` — shared across all sibling attempts of
  the same fan-out
- `detail_json.target_id`, `detail_json.target_name`, `detail_json.broker`,
  `detail_json.environment`
- `detail_json.broker_symbol` — the resolved contract symbol (e.g. `ESM6`)
- `detail_json.account_capital`, `detail_json.risk_pct` — per-target
  capital basis used for sizing
- `detail_json.rejection_reason`, `detail_json.error_code` — when the
  attempt did not fill

Phase 3 will replace this with first-class `bot_signals` and
`execution_attempts` tables. Until then this `detail_json` shape is the
contract operators rely on.

## Safety controls — summary

| Control | Where | Trigger |
| --- | --- | --- |
| Live URL hard-block | `tradovate_client.py::_ensure_env_allowed` | Three-flag check |
| Live target gate at the router | `targets.py::ResolvedTarget.is_environment_allowed` | `live_allowed=False` blocks env=live |
| Kill switch | `router.py::ExecutionRouter._kill_switch_check` | DB-backed flag, all targets skip |
| Account disabled | `router.py::ExecutionRouter.dispatch_open` | `is_enabled=False` skips that target |
| Missing credentials | `tradovate_client.py::authenticate` | Typed `MISSING_CREDENTIALS` error |
| Unsupported instrument | `broker_symbols.py::TradovateContractResolver` | Typed `UNSUPPORTED_INSTRUMENT_TRADOVATE` skip |
| Zero-contract size | `sizing.py::calculate_target_size` | Returns `None`, router rejects with `SIZE_ZERO` |
| Adapter exception | `router.py::ExecutionRouter._dispatch_one_open` | Captures, marks `error`, never re-raises |

## Known limitations to address before live use

1. **Endpoint verification.** The exact REST paths and payload shapes are
   marked `TODO_VERIFY` in source. Confirm against the latest Tradovate
   documentation before the first real demo connection.
2. **Front-month rotation.** Static suffix in env. Replace with a
   `LiveContractResolver` that calls `/contract/find` for expiry-aware
   active-month selection.
3. **Server-side stops.** Phase 1 stops are client-side only. Add bracket
   orders before any meaningful live use.
4. **No streaming.** Position and order updates are pulled, not pushed.
   Acceptable for Phase 1 paper-equivalent demo. Add the websocket
   integration before live.
5. **No reconciliation cadence.** `reconciliation.py` already accepts any
   `ExecutionAdapter`, but no scheduled job runs Tradovate reconciliation
   yet. Wire one up in Phase 5.
6. **Auth lock granularity.** A single `_auth_lock` per `TradovateClient`
   instance is sufficient for Phase 1. If multiple worker processes
   share Tradovate access, evaluate per-process tokens.
