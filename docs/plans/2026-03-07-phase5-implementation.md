# Phase 5: FIBOKEI Web Platform — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build the FIBOKEI web frontend (Next.js + KLineChart + Plotly) with cookie-based auth, trading chart workspace, research analytics, paper bot controls, trade inspection, and backend execution adapter abstraction.

**Architecture:** Next.js 14 App Router frontend communicating with existing FastAPI backend via secure HTTP-only cookie auth. KLineChart renders financial charts with custom Ichimoku/Fibonacci overlays. Plotly.js handles research analytics. Backend returns normalized annotation payloads. SWR for server state. shadcn/ui for components.

**Tech Stack:** Next.js 14, TypeScript, Tailwind CSS, shadcn/ui, KLineChart (`klinecharts`), Plotly.js (`react-plotly.js`), SWR, FastAPI (backend changes)

**Design doc:** `docs/plans/2026-03-07-phase5-web-platform-design.md`

---

## Task 1: Backend — Cookie-Based Auth Migration

**Files:**
- Modify: `backend/src/fibokei/api/auth.py`
- Modify: `backend/src/fibokei/api/routes/auth.py`
- Modify: `backend/tests/conftest.py`
- Create: `backend/tests/test_api_auth_cookies.py`

**Step 1: Write failing tests for cookie auth**

Create `backend/tests/test_api_auth_cookies.py`:

```python
"""Tests for cookie-based auth flow."""


def test_login_sets_cookie(api_client):
    response = api_client.post(
        "/api/v1/auth/login",
        data={"username": "joe", "password": "testpass123"},
    )
    assert response.status_code == 200
    assert "fibokei_token" in response.cookies


def test_cookie_auth_protects_routes(api_client):
    # No cookie = 401
    response = api_client.get("/api/v1/instruments/")
    assert response.status_code == 401


def test_cookie_auth_allows_access(api_client):
    # Login to get cookie
    api_client.post(
        "/api/v1/auth/login",
        data={"username": "joe", "password": "testpass123"},
    )
    # Cookie is stored on the test client automatically
    response = api_client.get("/api/v1/instruments/")
    assert response.status_code == 200


def test_logout_clears_cookie(api_client):
    api_client.post(
        "/api/v1/auth/login",
        data={"username": "joe", "password": "testpass123"},
    )
    response = api_client.post("/api/v1/auth/logout")
    assert response.status_code == 200
    # Cookie should be cleared (max_age=0)
    assert response.cookies.get("fibokei_token") is not None  # Set with expiry


def test_auth_me(api_client):
    api_client.post(
        "/api/v1/auth/login",
        data={"username": "joe", "password": "testpass123"},
    )
    response = api_client.get("/api/v1/auth/me")
    assert response.status_code == 200
    data = response.json()
    assert data["username"] == "joe"
```

**Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_api_auth_cookies.py -v`
Expected: FAIL — no cookie set, no /logout endpoint, no /me endpoint

**Step 3: Update auth module to support cookie extraction**

Modify `backend/src/fibokei/api/auth.py` — update `get_current_user` to check cookie first, then fall back to Bearer header:

```python
from fastapi import Cookie, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)

def get_current_user(
    request: Request,
    bearer_token: str | None = Depends(oauth2_scheme),
) -> TokenData:
    """Extract JWT from cookie first, then Bearer header fallback."""
    token = request.cookies.get("fibokei_token") or bearer_token
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    return decode_token(token)
```

**Step 4: Update auth routes — login sets cookie, add logout and me**

Modify `backend/src/fibokei/api/routes/auth.py`:

```python
from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from fibokei.api.auth import TokenData, UserModel, create_access_token, get_current_user, verify_password
from fibokei.api.deps import get_db

router = APIRouter(tags=["auth"])


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    user_id: int
    username: str
    role: str


class HealthResponse(BaseModel):
    status: str
    version: str


@router.get("/health", response_model=HealthResponse)
def health_check():
    return {"status": "ok", "version": "1.0.0"}


@router.post("/auth/login", response_model=TokenResponse)
def login(
    response: Response,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    user = db.scalar(
        select(UserModel).where(UserModel.username == form_data.username)
    )
    if not user or not verify_password(form_data.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )
    token = create_access_token(user.id, user.username)
    response.set_cookie(
        key="fibokei_token",
        value=token,
        httponly=True,
        samesite="lax",
        secure=False,  # Set True in production via env var
        max_age=24 * 3600,
    )
    return {"access_token": token, "token_type": "bearer"}


@router.post("/auth/logout")
def logout(response: Response):
    response.delete_cookie(key="fibokei_token")
    return {"status": "logged_out"}


@router.get("/auth/me", response_model=UserResponse)
def get_me(
    user: TokenData = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    db_user = db.scalar(
        select(UserModel).where(UserModel.id == user.user_id)
    )
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")
    return {"user_id": db_user.id, "username": db_user.username, "role": db_user.role}
```

**Step 5: Update conftest.py — add cookie-based auth fixture**

Add to `backend/tests/conftest.py`:

```python
@pytest.fixture
def auth_client(api_client):
    """Return api_client with auth cookie already set."""
    api_client.post(
        "/api/v1/auth/login",
        data={"username": "joe", "password": "testpass123"},
    )
    return api_client
```

**Step 6: Run all tests**

Run: `cd backend && pytest -v --tb=short`
Expected: All pass (cookie tests + existing Bearer tests via fallback)

**Step 7: Lint**

Run: `cd backend && ruff check src/`
Expected: All checks passed

**Step 8: Commit**

```bash
git add backend/src/fibokei/api/auth.py backend/src/fibokei/api/routes/auth.py backend/tests/conftest.py backend/tests/test_api_auth_cookies.py
git commit -m "feat: cookie-based auth with logout and /me endpoint"
```

---

## Task 2: Backend — Market Data and Chart Annotation Endpoints

**Files:**
- Create: `backend/src/fibokei/api/routes/market_data.py`
- Create: `backend/src/fibokei/api/routes/charts.py`
- Create: `backend/src/fibokei/api/schemas/charts.py`
- Modify: `backend/src/fibokei/api/app.py`
- Create: `backend/tests/test_api_market_data.py`
- Create: `backend/tests/test_api_charts.py`

**Step 1: Write failing test for market-data endpoint**

Create `backend/tests/test_api_market_data.py`:

```python
"""Tests for market data API."""


def test_get_market_data(api_client, auth_headers):
    response = api_client.get(
        "/api/v1/market-data/EURUSD/H1",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert "candles" in data
    assert "ichimoku" in data
    assert len(data["candles"]) > 0
    candle = data["candles"][0]
    assert all(k in candle for k in ["timestamp", "open", "high", "low", "close"])


def test_get_market_data_invalid_instrument(api_client, auth_headers):
    response = api_client.get(
        "/api/v1/market-data/INVALID/H1",
        headers=auth_headers,
    )
    assert response.status_code == 404


def test_get_market_data_invalid_timeframe(api_client, auth_headers):
    response = api_client.get(
        "/api/v1/market-data/EURUSD/INVALID",
        headers=auth_headers,
    )
    assert response.status_code == 400
```

**Step 2: Run to verify failure**

Run: `cd backend && pytest tests/test_api_market_data.py -v`
Expected: FAIL — 404, route doesn't exist

**Step 3: Create chart schemas**

Create `backend/src/fibokei/api/schemas/charts.py`:

```python
"""Pydantic schemas for chart data and annotations."""

from pydantic import BaseModel


class CandleBar(BaseModel):
    timestamp: int  # Unix ms
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


class IchimokuSeries(BaseModel):
    timestamp: int
    tenkan: float | None = None
    kijun: float | None = None
    senkou_a: float | None = None
    senkou_b: float | None = None
    chikou: float | None = None


class MarketDataResponse(BaseModel):
    instrument: str
    timeframe: str
    candles: list[CandleBar]
    ichimoku: list[IchimokuSeries]


class PricePoint(BaseModel):
    timestamp: str
    price: float


class TradeMarker(BaseModel):
    trade_id: str
    strategy_id: str
    direction: str
    entry: PricePoint
    exit: PricePoint | None = None
    stop_loss: list[PricePoint] = []
    take_profit: list[PricePoint] = []
    partial_exits: list[PricePoint] = []
    label: str = ""
    outcome: str = ""


class StrategyAnnotation(BaseModel):
    type: str
    timestamp: str
    price: float
    label: str = ""
    metadata: dict | None = None


class ChartAnnotationsResponse(BaseModel):
    trade_markers: list[TradeMarker]
    strategy_annotations: list[StrategyAnnotation]
```

**Step 4: Create market-data route**

Create `backend/src/fibokei/api/routes/market_data.py`:

```python
"""Market data API endpoints."""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from fibokei.api.auth import TokenData, get_current_user
from fibokei.api.schemas.charts import CandleBar, IchimokuSeries, MarketDataResponse
from fibokei.core.instruments import get_instrument
from fibokei.core.models import Timeframe
from fibokei.data.loader import load_ohlcv_csv
from fibokei.indicators.ichimoku import IchimokuCloud

router = APIRouter(tags=["market-data"])

_DATA_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data" / "fixtures"


@router.get("/market-data/{instrument}/{timeframe}", response_model=MarketDataResponse)
def get_market_data(
    instrument: str,
    timeframe: str,
    user: TokenData = Depends(get_current_user),
):
    """Return OHLCV candles with precomputed Ichimoku series."""
    inst = get_instrument(instrument.upper())
    if not inst:
        raise HTTPException(status_code=404, detail=f"Unknown instrument: {instrument}")

    try:
        tf_enum = Timeframe(timeframe.upper())
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid timeframe: {timeframe}")

    data_path = _DATA_DIR / f"sample_{instrument.lower()}_{timeframe.lower()}.csv"
    if not data_path.exists():
        raise HTTPException(status_code=404, detail="No data available for this combination")

    df = load_ohlcv_csv(str(data_path), instrument.upper(), tf_enum)

    # Compute Ichimoku
    ichimoku = IchimokuCloud()
    df = ichimoku.compute(df)

    candles = []
    ichimoku_series = []
    for _, row in df.iterrows():
        ts = int(row.name.timestamp() * 1000) if hasattr(row.name, "timestamp") else 0
        candles.append(CandleBar(
            timestamp=ts,
            open=row["open"],
            high=row["high"],
            low=row["low"],
            close=row["close"],
            volume=row.get("volume", 0.0),
        ))
        ichimoku_series.append(IchimokuSeries(
            timestamp=ts,
            tenkan=None if row.get("tenkan_sen") != row.get("tenkan_sen") else row.get("tenkan_sen"),
            kijun=None if row.get("kijun_sen") != row.get("kijun_sen") else row.get("kijun_sen"),
            senkou_a=None if row.get("senkou_span_a") != row.get("senkou_span_a") else row.get("senkou_span_a"),
            senkou_b=None if row.get("senkou_span_b") != row.get("senkou_span_b") else row.get("senkou_span_b"),
            chikou=None if row.get("chikou_span") != row.get("chikou_span") else row.get("chikou_span"),
        ))

    return MarketDataResponse(
        instrument=instrument.upper(),
        timeframe=timeframe.upper(),
        candles=candles,
        ichimoku=ichimoku_series,
    )
```

**Step 5: Create chart annotations route**

Create `backend/src/fibokei/api/routes/charts.py`:

```python
"""Chart annotation endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from fibokei.api.auth import TokenData, get_current_user
from fibokei.api.deps import get_db
from fibokei.api.schemas.charts import (
    ChartAnnotationsResponse,
    PricePoint,
    TradeMarker,
)
from fibokei.db.models import BacktestRunModel, TradeModel

router = APIRouter(tags=["charts"])


@router.get("/charts/annotations/{backtest_id}", response_model=ChartAnnotationsResponse)
def get_backtest_annotations(
    backtest_id: int,
    db: Session = Depends(get_db),
    user: TokenData = Depends(get_current_user),
):
    """Return normalized annotation payloads for a backtest run."""
    run = db.query(BacktestRunModel).filter(BacktestRunModel.id == backtest_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Backtest run not found")

    trades = (
        db.query(TradeModel)
        .filter(TradeModel.backtest_run_id == backtest_id)
        .order_by(TradeModel.entry_time.asc())
        .all()
    )

    trade_markers = []
    for t in trades:
        entry_ts = t.entry_time.isoformat() + "Z" if t.entry_time else ""
        exit_ts = t.exit_time.isoformat() + "Z" if t.exit_time else ""
        trade_markers.append(TradeMarker(
            trade_id=str(t.id),
            strategy_id=t.strategy_id,
            direction=t.direction,
            entry=PricePoint(timestamp=entry_ts, price=t.entry_price),
            exit=PricePoint(timestamp=exit_ts, price=t.exit_price),
            stop_loss=[],
            take_profit=[],
            partial_exits=[],
            label=f"{t.strategy_id} {t.direction}",
            outcome=t.exit_reason,
        ))

    return ChartAnnotationsResponse(
        trade_markers=trade_markers,
        strategy_annotations=[],
    )
```

**Step 6: Register routes in app.py**

Add to `backend/src/fibokei/api/app.py` in `create_app()`:

```python
    from fibokei.api.routes.market_data import router as market_data_router
    application.include_router(market_data_router, prefix="/api/v1")
    from fibokei.api.routes.charts import router as charts_router
    application.include_router(charts_router, prefix="/api/v1")
```

**Step 7: Add `get_instrument` helper if missing**

Check `backend/src/fibokei/core/instruments.py` for `get_instrument()`. If it doesn't exist, add:

```python
def get_instrument(symbol: str) -> Instrument | None:
    """Look up an instrument by symbol."""
    for inst in INSTRUMENTS:
        if inst.symbol == symbol:
            return inst
    return None
```

**Step 8: Run tests**

Run: `cd backend && pytest tests/test_api_market_data.py -v`
Expected: PASS

**Step 9: Write chart annotations test**

Create `backend/tests/test_api_charts.py`:

```python
"""Tests for chart annotation endpoints."""


def test_backtest_annotations_not_found(api_client, auth_headers):
    response = api_client.get("/api/v1/charts/annotations/9999", headers=auth_headers)
    assert response.status_code == 404


def test_backtest_annotations_empty(api_client, auth_headers):
    # Run a backtest first to get a valid ID
    req = {
        "strategy_id": "bot01_sanyaku",
        "instrument": "EURUSD",
        "timeframe": "H1",
    }
    run_resp = api_client.post("/api/v1/backtests/run", json=req, headers=auth_headers)
    if run_resp.status_code != 200:
        return  # Skip if no fixture data
    run_id = run_resp.json()["id"]

    response = api_client.get(f"/api/v1/charts/annotations/{run_id}", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert "trade_markers" in data
    assert "strategy_annotations" in data
```

**Step 10: Run all backend tests and lint**

Run: `cd backend && pytest -v --tb=short && ruff check src/`
Expected: All pass

**Step 11: Commit**

```bash
git add backend/src/fibokei/api/routes/market_data.py backend/src/fibokei/api/routes/charts.py backend/src/fibokei/api/schemas/charts.py backend/src/fibokei/api/app.py backend/src/fibokei/core/instruments.py backend/tests/test_api_market_data.py backend/tests/test_api_charts.py
git commit -m "feat: market-data and chart annotation API endpoints"
```

---

## Task 3: Backend — Trade History and System Endpoints

**Files:**
- Create: `backend/src/fibokei/api/routes/trades.py`
- Create: `backend/src/fibokei/api/routes/system.py`
- Modify: `backend/src/fibokei/api/app.py`
- Create: `backend/tests/test_api_trades.py`
- Create: `backend/tests/test_api_system.py`

**Step 1: Write failing tests**

Create `backend/tests/test_api_trades.py`:

```python
"""Tests for trade history endpoints."""


def test_list_trades_empty(api_client, auth_headers):
    response = api_client.get("/api/v1/trades/", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert "total" in data


def test_trade_not_found(api_client, auth_headers):
    response = api_client.get("/api/v1/trades/99999", headers=auth_headers)
    assert response.status_code == 404
```

Create `backend/tests/test_api_system.py`:

```python
"""Tests for system endpoints."""


def test_system_health(api_client):
    response = api_client.get("/api/v1/system/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_system_status(api_client, auth_headers):
    response = api_client.get("/api/v1/system/status", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert "api_version" in data
    assert "database" in data
```

**Step 2: Run to verify failure**

Run: `cd backend && pytest tests/test_api_trades.py tests/test_api_system.py -v`
Expected: FAIL

**Step 3: Implement trade history route**

Create `backend/src/fibokei/api/routes/trades.py`:

```python
"""Trade history API endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from fibokei.api.auth import TokenData, get_current_user
from fibokei.api.deps import get_db
from fibokei.db.models import TradeModel


router = APIRouter(tags=["trades"])


class TradeResponse(BaseModel):
    id: int
    strategy_id: str
    instrument: str
    direction: str
    entry_time: str | None
    entry_price: float
    exit_time: str | None
    exit_price: float
    exit_reason: str
    pnl: float
    bars_in_trade: int
    backtest_run_id: int


class TradeListResponse(BaseModel):
    items: list[TradeResponse]
    total: int
    page: int
    size: int


@router.get("/trades/", response_model=TradeListResponse)
def list_trades(
    strategy_id: str | None = None,
    instrument: str | None = None,
    direction: str | None = None,
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    user: TokenData = Depends(get_current_user),
):
    """List all trades with optional filtering and pagination."""
    query = db.query(TradeModel)
    if strategy_id:
        query = query.filter(TradeModel.strategy_id == strategy_id)
    if instrument:
        query = query.filter(TradeModel.instrument == instrument.upper())
    if direction:
        query = query.filter(TradeModel.direction == direction.upper())

    total = query.count()
    offset = (page - 1) * size
    trades = query.order_by(TradeModel.entry_time.desc()).offset(offset).limit(size).all()

    return TradeListResponse(
        items=[
            TradeResponse(
                id=t.id,
                strategy_id=t.strategy_id,
                instrument=t.instrument,
                direction=t.direction,
                entry_time=t.entry_time.isoformat() if t.entry_time else None,
                entry_price=t.entry_price,
                exit_time=t.exit_time.isoformat() if t.exit_time else None,
                exit_price=t.exit_price,
                exit_reason=t.exit_reason,
                pnl=t.pnl,
                bars_in_trade=t.bars_in_trade,
                backtest_run_id=t.backtest_run_id,
            )
            for t in trades
        ],
        total=total,
        page=page,
        size=size,
    )


@router.get("/trades/{trade_id}", response_model=TradeResponse)
def get_trade(
    trade_id: int,
    db: Session = Depends(get_db),
    user: TokenData = Depends(get_current_user),
):
    """Get single trade detail."""
    trade = db.query(TradeModel).filter(TradeModel.id == trade_id).first()
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")

    return TradeResponse(
        id=trade.id,
        strategy_id=trade.strategy_id,
        instrument=trade.instrument,
        direction=trade.direction,
        entry_time=trade.entry_time.isoformat() if trade.entry_time else None,
        entry_price=trade.entry_price,
        exit_time=trade.exit_time.isoformat() if trade.exit_time else None,
        exit_price=trade.exit_price,
        exit_reason=trade.exit_reason,
        pnl=trade.pnl,
        bars_in_trade=trade.bars_in_trade,
        backtest_run_id=trade.backtest_run_id,
    )
```

**Step 4: Implement system route**

Create `backend/src/fibokei/api/routes/system.py`:

```python
"""System diagnostics endpoints."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from fibokei.api.auth import TokenData, get_current_user

router = APIRouter(tags=["system"])


class SystemHealthResponse(BaseModel):
    status: str
    version: str


class SystemStatusResponse(BaseModel):
    api_version: str
    database: str
    paper_engine: str
    strategies_loaded: int


@router.get("/system/health", response_model=SystemHealthResponse)
def system_health():
    """Public health check endpoint."""
    return {"status": "ok", "version": "1.0.0"}


@router.get("/system/status", response_model=SystemStatusResponse)
def system_status(user: TokenData = Depends(get_current_user)):
    """Authenticated system status."""
    from fibokei.strategies.registry import strategy_registry

    return {
        "api_version": "1.0.0",
        "database": "connected",
        "paper_engine": "idle",
        "strategies_loaded": len(strategy_registry.list_available()),
    }
```

**Step 5: Register in app.py**

Add to `create_app()`:

```python
    from fibokei.api.routes.trades import router as trades_router
    application.include_router(trades_router, prefix="/api/v1")
    from fibokei.api.routes.system import router as system_router
    application.include_router(system_router, prefix="/api/v1")
```

**Step 6: Run all tests and lint**

Run: `cd backend && pytest -v --tb=short && ruff check src/`
Expected: All pass

**Step 7: Commit**

```bash
git add backend/src/fibokei/api/routes/trades.py backend/src/fibokei/api/routes/system.py backend/src/fibokei/api/app.py backend/tests/test_api_trades.py backend/tests/test_api_system.py
git commit -m "feat: trade history and system status API endpoints"
```

---

## Task 4: Backend — Execution Adapter Abstraction (Subphase 5.6)

**Files:**
- Create: `backend/src/fibokei/execution/adapter.py`
- Create: `backend/src/fibokei/execution/paper_adapter.py`
- Create: `backend/src/fibokei/execution/ig_adapter.py`
- Create: `backend/src/fibokei/core/feature_flags.py`
- Create: `backend/tests/test_execution_adapter.py`

**Step 1: Write failing tests**

Create `backend/tests/test_execution_adapter.py`:

```python
"""Tests for execution adapter abstraction."""

import pytest

from fibokei.core.feature_flags import FeatureFlags, get_execution_adapter
from fibokei.execution.adapter import ExecutionAdapter
from fibokei.execution.ig_adapter import IGExecutionAdapter
from fibokei.execution.paper_adapter import PaperExecutionAdapter


def test_cannot_instantiate_base_adapter():
    with pytest.raises(TypeError):
        ExecutionAdapter()


def test_paper_adapter_implements_interface():
    adapter = PaperExecutionAdapter()
    assert isinstance(adapter, ExecutionAdapter)


def test_paper_adapter_get_account_info():
    adapter = PaperExecutionAdapter()
    info = adapter.get_account_info()
    assert "balance" in info
    assert "equity" in info


def test_paper_adapter_get_positions_empty():
    adapter = PaperExecutionAdapter()
    positions = adapter.get_positions()
    assert positions == []


def test_ig_adapter_raises_not_implemented():
    adapter = IGExecutionAdapter.__new__(IGExecutionAdapter)
    with pytest.raises(NotImplementedError, match="not enabled in V1"):
        adapter.place_order({})


def test_feature_flags_default_paper():
    flags = FeatureFlags()
    assert flags.live_execution_enabled is False


def test_get_execution_adapter_returns_paper():
    adapter = get_execution_adapter()
    assert isinstance(adapter, PaperExecutionAdapter)
```

**Step 2: Run to verify failure**

Run: `cd backend && pytest tests/test_execution_adapter.py -v`
Expected: FAIL — modules don't exist

**Step 3: Create adapter ABC**

Create `backend/src/fibokei/execution/adapter.py`:

```python
"""Abstract execution adapter interface."""

from abc import ABC, abstractmethod


class ExecutionAdapter(ABC):
    """Interface for order execution — paper and live implementations share this contract."""

    @abstractmethod
    def place_order(self, order: dict) -> dict:
        """Place an order. Returns order result."""

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order."""

    @abstractmethod
    def modify_order(self, order_id: str, changes: dict) -> dict:
        """Modify an existing order."""

    @abstractmethod
    def get_positions(self) -> list[dict]:
        """Get all open positions."""

    @abstractmethod
    def get_account_info(self) -> dict:
        """Get account balance and equity info."""

    @abstractmethod
    def close_position(self, position_id: str) -> dict:
        """Close a position entirely."""

    @abstractmethod
    def partial_close(self, position_id: str, pct: float) -> dict:
        """Partially close a position."""
```

**Step 4: Create paper adapter**

Create `backend/src/fibokei/execution/paper_adapter.py`:

```python
"""Paper trading execution adapter."""

from fibokei.execution.adapter import ExecutionAdapter
from fibokei.paper.account import PaperAccount


class PaperExecutionAdapter(ExecutionAdapter):
    """Routes all execution through the paper trading engine."""

    def __init__(self, initial_balance: float = 10000.0):
        self._account = PaperAccount(initial_balance=initial_balance)

    def place_order(self, order: dict) -> dict:
        return {"status": "filled", "order": order}

    def cancel_order(self, order_id: str) -> bool:
        return True

    def modify_order(self, order_id: str, changes: dict) -> dict:
        return {"status": "modified", "order_id": order_id, "changes": changes}

    def get_positions(self) -> list[dict]:
        return []

    def get_account_info(self) -> dict:
        return self._account.get_status()

    def close_position(self, position_id: str) -> dict:
        return {"status": "closed", "position_id": position_id}

    def partial_close(self, position_id: str, pct: float) -> dict:
        return {"status": "partially_closed", "position_id": position_id, "pct": pct}
```

**Step 5: Create IG stub**

Create `backend/src/fibokei/execution/ig_adapter.py`:

```python
"""IG Markets execution adapter — V1 stub."""

from fibokei.execution.adapter import ExecutionAdapter


class IGExecutionAdapter(ExecutionAdapter):
    """Stub for IG Markets live trading. Not enabled in V1."""

    def place_order(self, order: dict) -> dict:
        raise NotImplementedError("IG live trading not enabled in V1")

    def cancel_order(self, order_id: str) -> bool:
        raise NotImplementedError("IG live trading not enabled in V1")

    def modify_order(self, order_id: str, changes: dict) -> dict:
        raise NotImplementedError("IG live trading not enabled in V1")

    def get_positions(self) -> list[dict]:
        raise NotImplementedError("IG live trading not enabled in V1")

    def get_account_info(self) -> dict:
        raise NotImplementedError("IG live trading not enabled in V1")

    def close_position(self, position_id: str) -> dict:
        raise NotImplementedError("IG live trading not enabled in V1")

    def partial_close(self, position_id: str, pct: float) -> dict:
        raise NotImplementedError("IG live trading not enabled in V1")
```

**Step 6: Create feature flags**

Create `backend/src/fibokei/core/feature_flags.py`:

```python
"""Feature flags for FIBOKEI platform."""

import os


class FeatureFlags:
    """Read feature flags from environment variables."""

    @property
    def live_execution_enabled(self) -> bool:
        return os.environ.get("FIBOKEI_LIVE_EXECUTION_ENABLED", "false").lower() == "true"

    @property
    def ig_paper_mode(self) -> bool:
        return os.environ.get("FIBOKEI_IG_PAPER_MODE", "true").lower() == "true"


def get_execution_adapter():
    """Return the appropriate execution adapter based on feature flags."""
    from fibokei.execution.paper_adapter import PaperExecutionAdapter

    flags = FeatureFlags()
    if flags.live_execution_enabled:
        from fibokei.execution.ig_adapter import IGExecutionAdapter
        return IGExecutionAdapter()
    return PaperExecutionAdapter()
```

**Step 7: Run tests and lint**

Run: `cd backend && pytest tests/test_execution_adapter.py -v && ruff check src/`
Expected: All pass

**Step 8: Commit**

```bash
git add backend/src/fibokei/execution/ backend/src/fibokei/core/feature_flags.py backend/tests/test_execution_adapter.py
git commit -m "feat: execution adapter abstraction with paper and IG stub"
```

---

## Task 5: Frontend — Next.js Scaffold and Configuration (Subphase 5.1)

**Files:**
- Create: `frontend/` — entire Next.js project
- Create: `frontend/.env.local.example`
- Create: `frontend/tailwind.config.ts` (modify generated)
- Create: `frontend/src/lib/api.ts`
- Create: `frontend/src/lib/auth.tsx`
- Create: `frontend/src/types/contracts/chart.ts`
- Create: `frontend/src/types/contracts/analytics.ts`
- Create: `frontend/src/types/contracts/trades.ts`
- Create: `frontend/src/types/contracts/research.ts`

**Step 1: Create Next.js project**

```bash
cd /Users/joseph/Projects/Fiboki_Trading
npx create-next-app@latest frontend --typescript --tailwind --app --src-dir --no-import-alias --use-npm
```

Accept defaults. This creates the full scaffold.

**Step 2: Clean boilerplate**

Remove default content from `frontend/src/app/page.tsx`, `frontend/src/app/globals.css` (keep Tailwind directives only), delete `public/` default SVGs.

**Step 3: Install dependencies**

```bash
cd frontend
npm install klinecharts react-plotly.js plotly.js swr lucide-react clsx tailwind-merge
npm install -D @types/react-plotly.js
```

**Step 4: Create .env.local.example**

Create `frontend/.env.local.example`:

```
NEXT_PUBLIC_API_URL=http://localhost:8000
```

**Step 5: Configure Tailwind theme**

Update `frontend/tailwind.config.ts` — add FIBOKEI brand colours:

```typescript
import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        background: {
          DEFAULT: "#FAFAF9",
          card: "#FFFFFF",
          muted: "#F5F5F4",
        },
        foreground: {
          DEFAULT: "#1C1917",
          muted: "#78716C",
        },
        primary: {
          DEFAULT: "#16A34A",
          dark: "#15803D",
          light: "#22C55E",
          accent: "#86EFAC",
        },
        danger: "#EF4444",
        warning: "#F59E0B",
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "sans-serif"],
      },
    },
  },
  plugins: [],
};
export default config;
```

**Step 6: Create contract types**

Create `frontend/src/types/contracts/chart.ts`:

```typescript
export interface CandleBar {
  timestamp: number; // Unix ms
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface IchimokuPoint {
  timestamp: number;
  tenkan: number | null;
  kijun: number | null;
  senkou_a: number | null;
  senkou_b: number | null;
  chikou: number | null;
}

export interface FibLevel {
  level: number; // 0.236, 0.382, 0.5, 0.618, 0.786, 1.0
  price: number;
  label: string;
}

export interface MarketDataResponse {
  instrument: string;
  timeframe: string;
  candles: CandleBar[];
  ichimoku: IchimokuPoint[];
}
```

Create `frontend/src/types/contracts/trades.ts`:

```typescript
export interface PricePoint {
  timestamp: string;
  price: number;
}

export interface TradeMarker {
  trade_id: string;
  strategy_id: string;
  direction: "LONG" | "SHORT";
  entry: PricePoint;
  exit: PricePoint | null;
  stop_loss: PricePoint[];
  take_profit: PricePoint[];
  partial_exits: PricePoint[];
  label: string;
  outcome: string;
}

export interface StrategyAnnotation {
  type: string;
  timestamp: string;
  price: number;
  label: string;
  metadata?: Record<string, unknown>;
}

export interface ChartAnnotationsResponse {
  trade_markers: TradeMarker[];
  strategy_annotations: StrategyAnnotation[];
}

export interface Trade {
  id: number;
  strategy_id: string;
  instrument: string;
  direction: string;
  entry_time: string | null;
  entry_price: number;
  exit_time: string | null;
  exit_price: number;
  exit_reason: string;
  pnl: number;
  bars_in_trade: number;
  backtest_run_id: number;
}

export interface TradeListResponse {
  items: Trade[];
  total: number;
  page: number;
  size: number;
}
```

Create `frontend/src/types/contracts/analytics.ts`:

```typescript
export interface EquityCurvePoint {
  bar_index: number;
  equity: number;
}

export interface BacktestSummary {
  id: number;
  strategy_id: string;
  instrument: string;
  timeframe: string;
  start_date: string | null;
  end_date: string | null;
  total_trades: number;
  net_profit: number;
  sharpe_ratio: number | null;
  max_drawdown_pct: number | null;
}

export interface BacktestDetail extends BacktestSummary {
  config_json: Record<string, unknown> | null;
  metrics_json: Record<string, unknown> | null;
}
```

Create `frontend/src/types/contracts/research.ts`:

```typescript
export interface ResearchResult {
  id: number;
  run_id: string;
  strategy_id: string;
  instrument: string;
  timeframe: string;
  composite_score: number;
  rank: number;
  metrics_json: Record<string, unknown> | null;
  created_at: string | null;
}

export interface ResearchRunSummary {
  run_id: string;
  total_combinations: number;
  completed: number;
  top_result: ResearchResult | null;
}
```

**Step 7: Create API client**

Create `frontend/src/lib/api.ts`:

```typescript
const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function apiFetch<T>(path: string, options: RequestInit = {}): Promise<T> {
  const res = await fetch(`${API_URL}/api/v1${path}`, {
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...options.headers,
    },
    ...options,
  });

  if (res.status === 401) {
    // Try refresh or redirect to login
    if (typeof window !== "undefined" && !path.includes("/auth/")) {
      window.location.href = "/login";
    }
    throw new ApiError(401, "Unauthorized");
  }

  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: "Request failed" }));
    throw new ApiError(res.status, body.detail || "Request failed");
  }

  return res.json();
}

export const api = {
  // Auth
  login: (username: string, password: string) => {
    const formData = new URLSearchParams();
    formData.append("username", username);
    formData.append("password", password);
    return fetch(`${API_URL}/api/v1/auth/login`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: formData,
    });
  },
  logout: () => apiFetch("/auth/logout", { method: "POST" }),
  me: () => apiFetch<{ user_id: number; username: string; role: string }>("/auth/me"),

  // Market data
  marketData: (instrument: string, timeframe: string) =>
    apiFetch<import("@/types/contracts/chart").MarketDataResponse>(
      `/market-data/${instrument}/${timeframe}`
    ),

  // Charts
  annotations: (backtestId: number) =>
    apiFetch<import("@/types/contracts/trades").ChartAnnotationsResponse>(
      `/charts/annotations/${backtestId}`
    ),

  // Backtests
  runBacktest: (body: Record<string, unknown>) =>
    apiFetch("/backtests/run", { method: "POST", body: JSON.stringify(body) }),
  listBacktests: (params?: string) =>
    apiFetch<import("@/types/contracts/analytics").BacktestSummary[]>(
      `/backtests${params ? `?${params}` : ""}`
    ),
  getBacktest: (id: number) =>
    apiFetch<import("@/types/contracts/analytics").BacktestDetail>(`/backtests/${id}`),
  getEquityCurve: (id: number) =>
    apiFetch<{ equity_curve: number[] }>(`/backtests/${id}/equity-curve`),

  // Research
  runResearch: (body: Record<string, unknown>) =>
    apiFetch("/research/run", { method: "POST", body: JSON.stringify(body) }),
  rankings: (params?: string) =>
    apiFetch<import("@/types/contracts/research").ResearchResult[]>(
      `/research/rankings${params ? `?${params}` : ""}`
    ),

  // Paper
  createBot: (body: Record<string, unknown>) =>
    apiFetch("/paper/bots", { method: "POST", body: JSON.stringify(body) }),
  listBots: () => apiFetch("/paper/bots"),
  getBot: (id: string) => apiFetch(`/paper/bots/${id}`),
  stopBot: (id: string) => apiFetch(`/paper/bots/${id}/stop`, { method: "POST" }),
  pauseBot: (id: string) => apiFetch(`/paper/bots/${id}/pause`, { method: "POST" }),
  account: () => apiFetch("/paper/account"),

  // Trades
  listTrades: (params?: string) =>
    apiFetch<import("@/types/contracts/trades").TradeListResponse>(
      `/trades/${params ? `?${params}` : ""}`
    ),
  getTrade: (id: number) =>
    apiFetch<import("@/types/contracts/trades").Trade>(`/trades/${id}`),

  // Instruments & strategies
  instruments: () => apiFetch<Array<Record<string, unknown>>>("/instruments/"),
  strategies: () => apiFetch<Array<Record<string, unknown>>>("/strategies/"),

  // System
  systemHealth: () => apiFetch<{ status: string; version: string }>("/system/health"),
  systemStatus: () => apiFetch<Record<string, unknown>>("/system/status"),
};

export { ApiError };
```

**Step 8: Create auth context**

Create `frontend/src/lib/auth.tsx`:

```typescript
"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { api } from "./api";

interface User {
  user_id: number;
  username: string;
  role: string;
}

interface AuthContextType {
  user: User | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  login: (username: string, password: string) => Promise<boolean>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    api.me().then(setUser).catch(() => setUser(null)).finally(() => setIsLoading(false));
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    const res = await api.login(username, password);
    if (!res.ok) return false;
    const me = await api.me();
    setUser(me);
    return true;
  }, []);

  const logout = useCallback(async () => {
    await api.logout().catch(() => {});
    setUser(null);
  }, []);

  return (
    <AuthContext.Provider value={{ user, isAuthenticated: !!user, isLoading, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
```

**Step 9: Create middleware for route protection**

Create `frontend/src/middleware.ts`:

```typescript
import { NextRequest, NextResponse } from "next/server";

export function middleware(request: NextRequest) {
  const token = request.cookies.get("fibokei_token");
  const isLoginPage = request.nextUrl.pathname === "/login";

  if (!token && !isLoginPage) {
    return NextResponse.redirect(new URL("/login", request.url));
  }

  if (token && isLoginPage) {
    return NextResponse.redirect(new URL("/", request.url));
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|api).*)"],
};
```

**Step 10: Build to verify no errors**

```bash
cd frontend && npm run build
```

Expected: Build succeeds

**Step 11: Commit**

```bash
git add frontend/
git commit -m "feat: Next.js scaffold with auth, API client, contracts, middleware"
```

---

## Task 6: Frontend — Login Page and Dashboard Layout (Subphase 5.1 cont.)

**Files:**
- Create: `frontend/src/app/login/page.tsx`
- Create: `frontend/src/app/(dashboard)/layout.tsx`
- Create: `frontend/src/app/(dashboard)/page.tsx`
- Modify: `frontend/src/app/layout.tsx`
- Modify: `frontend/src/app/globals.css`

**Step 1: Update globals.css**

Replace `frontend/src/app/globals.css` with Tailwind directives only:

```css
@tailwind base;
@tailwind components;
@tailwind utilities;

body {
  background-color: #FAFAF9;
  color: #1C1917;
}
```

**Step 2: Update root layout**

Modify `frontend/src/app/layout.tsx`:

```tsx
import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
import { AuthProvider } from "@/lib/auth";

const inter = Inter({ subsets: ["latin"] });

export const metadata: Metadata = {
  title: "FIBOKEI",
  description: "Multi-strategy automated trading platform",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className={inter.className}>
        <AuthProvider>{children}</AuthProvider>
      </body>
    </html>
  );
}
```

**Step 3: Create login page**

Create `frontend/src/app/login/page.tsx`:

```tsx
"use client";

import { useAuth } from "@/lib/auth";
import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";

export default function LoginPage() {
  const { login } = useAuth();
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    const ok = await login(username, password);
    setLoading(false);
    if (ok) {
      router.push("/");
    } else {
      setError("Invalid username or password");
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-background">
      <div className="w-full max-w-sm bg-background-card rounded-lg shadow-md p-8">
        <h1 className="text-2xl font-bold text-center mb-2">FIBOKEI</h1>
        <p className="text-foreground-muted text-center text-sm mb-6">
          Trading Research Platform
        </p>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label htmlFor="username" className="block text-sm font-medium mb-1">
              Username
            </label>
            <input
              id="username"
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-primary"
              required
            />
          </div>
          <div>
            <label htmlFor="password" className="block text-sm font-medium mb-1">
              Password
            </label>
            <input
              id="password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-primary"
              required
            />
          </div>
          {error && <p className="text-danger text-sm">{error}</p>}
          <button
            type="submit"
            disabled={loading}
            className="w-full py-2 bg-primary text-white rounded-md hover:bg-primary-dark transition disabled:opacity-50"
          >
            {loading ? "Signing in..." : "Sign In"}
          </button>
        </form>
      </div>
    </div>
  );
}
```

**Step 4: Create dashboard layout with sidebar**

Create `frontend/src/app/(dashboard)/layout.tsx`:

```tsx
"use client";

import { useAuth } from "@/lib/auth";
import {
  BarChart3,
  Bot,
  CandlestickChart,
  History,
  LayoutDashboard,
  LogOut,
  Search,
  Settings,
  Server,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";

const NAV_ITEMS = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard },
  { href: "/charts", label: "Charts", icon: CandlestickChart },
  { href: "/backtests", label: "Backtests", icon: BarChart3 },
  { href: "/research", label: "Research", icon: Search },
  { href: "/bots", label: "Paper Bots", icon: Bot },
  { href: "/trades", label: "Trade History", icon: History },
  { href: "/settings", label: "Settings", icon: Settings },
  { href: "/system", label: "System", icon: Server },
];

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  const { user, logout, isLoading } = useAuth();
  const pathname = usePathname();
  const router = useRouter();

  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background">
        <p className="text-foreground-muted">Loading...</p>
      </div>
    );
  }

  async function handleLogout() {
    await logout();
    router.push("/login");
  }

  return (
    <div className="min-h-screen flex bg-background">
      {/* Sidebar */}
      <aside className="w-56 bg-background-card border-r border-gray-200 flex flex-col">
        <div className="px-4 py-5 border-b border-gray-200">
          <h1 className="text-lg font-bold text-primary">FIBOKEI</h1>
        </div>
        <nav className="flex-1 py-4 space-y-1">
          {NAV_ITEMS.map(({ href, label, icon: Icon }) => {
            const active = pathname === href;
            return (
              <Link
                key={href}
                href={href}
                className={`flex items-center gap-3 px-4 py-2 text-sm transition ${
                  active
                    ? "bg-primary/10 text-primary font-medium border-r-2 border-primary"
                    : "text-foreground-muted hover:text-foreground hover:bg-background-muted"
                }`}
              >
                <Icon size={18} />
                {label}
              </Link>
            );
          })}
        </nav>
        <div className="border-t border-gray-200 p-4">
          <div className="flex items-center justify-between">
            <span className="text-sm text-foreground-muted">{user?.username}</span>
            <button onClick={handleLogout} className="text-foreground-muted hover:text-danger">
              <LogOut size={16} />
            </button>
          </div>
        </div>
      </aside>

      {/* Main content */}
      <main className="flex-1 p-6 overflow-auto">{children}</main>
    </div>
  );
}
```

**Step 5: Create dashboard page placeholder**

Create `frontend/src/app/(dashboard)/page.tsx`:

```tsx
"use client";

import { useAuth } from "@/lib/auth";

export default function DashboardPage() {
  const { user } = useAuth();

  return (
    <div>
      <h2 className="text-xl font-semibold mb-4">
        Welcome to FIBOKEI{user ? `, ${user.username}` : ""}
      </h2>
      <p className="text-foreground-muted">Dashboard with KPIs and analytics coming next.</p>
    </div>
  );
}
```

**Step 6: Build and verify**

```bash
cd frontend && npm run build
```

Expected: Build succeeds

**Step 7: Commit**

```bash
git add frontend/
git commit -m "feat: login page and dashboard layout with sidebar navigation"
```

---

## Task 7: Frontend — KLineChart Trading Workspace (Subphase 5.2)

**Files:**
- Create: `frontend/src/components/charts/core/TradingChart.tsx`
- Create: `frontend/src/components/charts/overlays/IchimokuOverlay.ts`
- Create: `frontend/src/components/charts/annotations/TradeMarkers.ts`
- Create: `frontend/src/components/charts/panels/ChartToolbar.tsx`
- Create: `frontend/src/components/charts/panels/OverlayControls.tsx`
- Create: `frontend/src/lib/chart-mappers/candle-mapper.ts`
- Create: `frontend/src/lib/chart-mappers/ichimoku-mapper.ts`
- Create: `frontend/src/lib/hooks/use-market-data.ts`
- Create: `frontend/src/app/(dashboard)/charts/page.tsx`

**Step 1: Create candle mapper**

Create `frontend/src/lib/chart-mappers/candle-mapper.ts`:

```typescript
import type { CandleBar } from "@/types/contracts/chart";

/**
 * Convert backend CandleBar[] to KLineChart data format.
 * KLineChart expects: { timestamp, open, high, low, close, volume }
 */
export function mapCandlesToKLine(candles: CandleBar[]) {
  return candles.map((c) => ({
    timestamp: c.timestamp,
    open: c.open,
    high: c.high,
    low: c.low,
    close: c.close,
    volume: c.volume,
  }));
}
```

**Step 2: Create Ichimoku overlay registration**

Create `frontend/src/components/charts/overlays/IchimokuOverlay.ts`:

```typescript
import { registerIndicator } from "klinecharts";
import type { IchimokuPoint } from "@/types/contracts/chart";

let _ichimokuData: IchimokuPoint[] = [];

export function setIchimokuData(data: IchimokuPoint[]) {
  _ichimokuData = data;
}

export function registerIchimokuIndicator() {
  registerIndicator({
    name: "ICHIMOKU",
    figures: [
      { key: "tenkan", title: "Tenkan: ", type: "line" },
      { key: "kijun", title: "Kijun: ", type: "line" },
      { key: "senkou_a", title: "Senkou A: ", type: "line" },
      { key: "senkou_b", title: "Senkou B: ", type: "line" },
      { key: "chikou", title: "Chikou: ", type: "line" },
    ],
    styles: {
      lines: [
        { color: "#2196F3", size: 1 },  // tenkan - blue
        { color: "#F44336", size: 1 },  // kijun - red
        { color: "#4CAF50", size: 1 },  // senkou_a - green
        { color: "#FF9800", size: 1 },  // senkou_b - orange
        { color: "#9C27B0", size: 1 },  // chikou - purple
      ],
    },
    calc: (kLineDataList) => {
      return kLineDataList.map((kLineData, index) => {
        const point = _ichimokuData[index];
        if (!point) return {};
        return {
          tenkan: point.tenkan,
          kijun: point.kijun,
          senkou_a: point.senkou_a,
          senkou_b: point.senkou_b,
          chikou: point.chikou,
        };
      });
    },
  });
}
```

**Step 3: Create trade marker annotations**

Create `frontend/src/components/charts/annotations/TradeMarkers.ts`:

```typescript
import type { Chart } from "klinecharts";
import type { TradeMarker } from "@/types/contracts/trades";

export function renderTradeMarkers(chart: Chart, markers: TradeMarker[]) {
  // Clear existing trade overlays
  chart.removeOverlay({ name: "tradeEntry" });
  chart.removeOverlay({ name: "tradeExit" });
  chart.removeOverlay({ name: "tradeSL" });
  chart.removeOverlay({ name: "tradeTP" });

  for (const marker of markers) {
    const entryTs = new Date(marker.entry.timestamp).getTime();
    const isLong = marker.direction === "LONG";

    // Entry marker
    chart.createOverlay({
      name: "simpleAnnotation",
      extendData: `${isLong ? "BUY" : "SELL"} ${marker.strategy_id}`,
      points: [{ timestamp: entryTs, value: marker.entry.price }],
      styles: {
        symbol: { color: isLong ? "#16A34A" : "#EF4444" },
      },
    });

    // Exit marker
    if (marker.exit) {
      const exitTs = new Date(marker.exit.timestamp).getTime();
      chart.createOverlay({
        name: "simpleAnnotation",
        extendData: `EXIT: ${marker.outcome}`,
        points: [{ timestamp: exitTs, value: marker.exit.price }],
      });
    }

    // SL lines
    for (const sl of marker.stop_loss) {
      chart.createOverlay({
        name: "priceLine",
        points: [{ value: sl.price }],
        styles: { line: { color: "#EF4444", size: 1, style: "dashed" } },
      });
    }

    // TP lines
    for (const tp of marker.take_profit) {
      chart.createOverlay({
        name: "priceLine",
        points: [{ value: tp.price }],
        styles: { line: { color: "#16A34A", size: 1, style: "dashed" } },
      });
    }
  }
}
```

**Step 4: Create SWR hook for market data**

Create `frontend/src/lib/hooks/use-market-data.ts`:

```typescript
import useSWR from "swr";
import { api } from "@/lib/api";

export function useMarketData(instrument: string, timeframe: string) {
  return useSWR(
    instrument && timeframe ? `/market-data/${instrument}/${timeframe}` : null,
    () => api.marketData(instrument, timeframe)
  );
}
```

**Step 5: Create chart toolbar**

Create `frontend/src/components/charts/panels/ChartToolbar.tsx`:

```tsx
"use client";

interface ChartToolbarProps {
  instrument: string;
  timeframe: string;
  instruments: string[];
  timeframes: string[];
  onInstrumentChange: (v: string) => void;
  onTimeframeChange: (v: string) => void;
}

export function ChartToolbar({
  instrument,
  timeframe,
  instruments,
  timeframes,
  onInstrumentChange,
  onTimeframeChange,
}: ChartToolbarProps) {
  return (
    <div className="flex items-center gap-4 p-3 bg-background-card border-b border-gray-200">
      <select
        value={instrument}
        onChange={(e) => onInstrumentChange(e.target.value)}
        className="px-3 py-1.5 text-sm border border-gray-300 rounded-md bg-white focus:ring-2 focus:ring-primary"
      >
        {instruments.map((i) => (
          <option key={i} value={i}>{i}</option>
        ))}
      </select>
      <div className="flex gap-1">
        {timeframes.map((tf) => (
          <button
            key={tf}
            onClick={() => onTimeframeChange(tf)}
            className={`px-3 py-1.5 text-xs rounded transition ${
              timeframe === tf
                ? "bg-primary text-white"
                : "bg-background-muted text-foreground-muted hover:bg-gray-200"
            }`}
          >
            {tf}
          </button>
        ))}
      </div>
    </div>
  );
}
```

**Step 6: Create overlay controls**

Create `frontend/src/components/charts/panels/OverlayControls.tsx`:

```tsx
"use client";

interface OverlayControlsProps {
  ichimokuVisible: boolean;
  onToggleIchimoku: () => void;
}

export function OverlayControls({ ichimokuVisible, onToggleIchimoku }: OverlayControlsProps) {
  return (
    <div className="flex items-center gap-4 px-3 py-2 bg-background-muted border-b border-gray-200">
      <label className="flex items-center gap-2 text-sm cursor-pointer">
        <input
          type="checkbox"
          checked={ichimokuVisible}
          onChange={onToggleIchimoku}
          className="accent-primary"
        />
        Ichimoku Cloud
      </label>
    </div>
  );
}
```

**Step 7: Create TradingChart component**

Create `frontend/src/components/charts/core/TradingChart.tsx`:

```tsx
"use client";

import { useEffect, useRef } from "react";
import { init, dispose, type Chart } from "klinecharts";
import type { CandleBar, IchimokuPoint } from "@/types/contracts/chart";
import { mapCandlesToKLine } from "@/lib/chart-mappers/candle-mapper";
import { registerIchimokuIndicator, setIchimokuData } from "../overlays/IchimokuOverlay";

interface TradingChartProps {
  candles: CandleBar[];
  ichimoku?: IchimokuPoint[];
  showIchimoku?: boolean;
  height?: number;
}

let _ichimokuRegistered = false;

export function TradingChart({
  candles,
  ichimoku,
  showIchimoku = true,
  height = 500,
}: TradingChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<Chart | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;

    if (!_ichimokuRegistered) {
      registerIchimokuIndicator();
      _ichimokuRegistered = true;
    }

    const chart = init(containerRef.current, {
      styles: {
        grid: {
          horizontal: { color: "#E5E5E5" },
          vertical: { color: "#E5E5E5" },
        },
        candle: {
          bar: {
            upColor: "#16A34A",
            downColor: "#EF4444",
            upBorderColor: "#16A34A",
            downBorderColor: "#EF4444",
            upWickColor: "#16A34A",
            downWickColor: "#EF4444",
          },
        },
        yAxis: {
          axisLine: { color: "#D4D4D4" },
          tickText: { color: "#78716C" },
        },
        xAxis: {
          axisLine: { color: "#D4D4D4" },
          tickText: { color: "#78716C" },
        },
      },
    });

    chartRef.current = chart;

    return () => {
      if (containerRef.current) {
        dispose(containerRef.current);
      }
      chartRef.current = null;
    };
  }, []);

  // Update data
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || candles.length === 0) return;

    if (ichimoku) {
      setIchimokuData(ichimoku);
    }

    chart.applyNewData(mapCandlesToKLine(candles));

    if (showIchimoku && ichimoku && ichimoku.length > 0) {
      chart.createIndicator("ICHIMOKU", false, { id: "candle_pane" });
    } else {
      chart.removeIndicator("candle_pane", "ICHIMOKU");
    }
  }, [candles, ichimoku, showIchimoku]);

  return (
    <div
      ref={containerRef}
      style={{ width: "100%", height }}
      className="bg-background-card rounded-md border border-gray-200"
    />
  );
}
```

**Step 8: Create charts page**

Create `frontend/src/app/(dashboard)/charts/page.tsx`:

```tsx
"use client";

import { useState } from "react";
import { TradingChart } from "@/components/charts/core/TradingChart";
import { ChartToolbar } from "@/components/charts/panels/ChartToolbar";
import { OverlayControls } from "@/components/charts/panels/OverlayControls";
import { useMarketData } from "@/lib/hooks/use-market-data";

const INSTRUMENTS = ["EURUSD"];
const TIMEFRAMES = ["M15", "M30", "H1", "H4"];

export default function ChartsPage() {
  const [instrument, setInstrument] = useState("EURUSD");
  const [timeframe, setTimeframe] = useState("H1");
  const [ichimokuVisible, setIchimokuVisible] = useState(true);

  const { data, isLoading, error } = useMarketData(instrument, timeframe);

  return (
    <div className="space-y-0">
      <ChartToolbar
        instrument={instrument}
        timeframe={timeframe}
        instruments={INSTRUMENTS}
        timeframes={TIMEFRAMES}
        onInstrumentChange={setInstrument}
        onTimeframeChange={setTimeframe}
      />
      <OverlayControls
        ichimokuVisible={ichimokuVisible}
        onToggleIchimoku={() => setIchimokuVisible(!ichimokuVisible)}
      />
      {isLoading && (
        <div className="flex items-center justify-center h-[500px] bg-background-card">
          <p className="text-foreground-muted">Loading chart data...</p>
        </div>
      )}
      {error && (
        <div className="flex items-center justify-center h-[500px] bg-background-card">
          <p className="text-danger">Failed to load data: {error.message}</p>
        </div>
      )}
      {data && (
        <TradingChart
          candles={data.candles}
          ichimoku={data.ichimoku}
          showIchimoku={ichimokuVisible}
          height={600}
        />
      )}
    </div>
  );
}
```

**Step 9: Build**

```bash
cd frontend && npm run build
```

Expected: Build succeeds

**Step 10: Commit**

```bash
git add frontend/src/components/charts/ frontend/src/lib/chart-mappers/ frontend/src/lib/hooks/ frontend/src/app/\(dashboard\)/charts/
git commit -m "feat: KLineChart workspace with Ichimoku overlay and toolbar"
```

---

## Task 8: Frontend — Dashboard with KPI Cards and Plotly Mini-Charts (Subphase 5.2 cont.)

**Files:**
- Create: `frontend/src/components/analytics/MiniSummary.tsx`
- Create: `frontend/src/components/analytics/EquityCurve.tsx`
- Modify: `frontend/src/app/(dashboard)/page.tsx`
- Create: `frontend/src/lib/hooks/use-bots.ts`

**Step 1: Create Plotly wrapper for equity curve**

Create `frontend/src/components/analytics/EquityCurve.tsx`:

```tsx
"use client";

import dynamic from "next/dynamic";

const Plot = dynamic(() => import("react-plotly.js"), { ssr: false });

interface EquityCurveProps {
  data: number[];
  height?: number;
  title?: string;
}

export function EquityCurve({ data, height = 300, title = "Equity Curve" }: EquityCurveProps) {
  return (
    <Plot
      data={[
        {
          y: data,
          type: "scatter",
          mode: "lines",
          line: { color: "#16A34A", width: 2 },
          fill: "tozeroy",
          fillcolor: "rgba(22, 163, 74, 0.1)",
        },
      ]}
      layout={{
        title: { text: title, font: { size: 14, color: "#1C1917" } },
        height,
        margin: { t: 40, r: 20, b: 40, l: 50 },
        paper_bgcolor: "transparent",
        plot_bgcolor: "transparent",
        xaxis: { showgrid: false },
        yaxis: { gridcolor: "#E5E5E5" },
        font: { family: "Inter, system-ui, sans-serif" },
      }}
      config={{ displayModeBar: false, responsive: true }}
      style={{ width: "100%" }}
    />
  );
}
```

**Step 2: Create mini summary sparkline**

Create `frontend/src/components/analytics/MiniSummary.tsx`:

```tsx
"use client";

import dynamic from "next/dynamic";

const Plot = dynamic(() => import("react-plotly.js"), { ssr: false });

interface MiniSummaryProps {
  data: number[];
  color?: string;
  height?: number;
}

export function MiniSummary({ data, color = "#16A34A", height = 60 }: MiniSummaryProps) {
  return (
    <Plot
      data={[
        {
          y: data,
          type: "scatter",
          mode: "lines",
          line: { color, width: 1.5 },
          fill: "tozeroy",
          fillcolor: `${color}15`,
        },
      ]}
      layout={{
        height,
        margin: { t: 5, r: 5, b: 5, l: 5 },
        paper_bgcolor: "transparent",
        plot_bgcolor: "transparent",
        xaxis: { visible: false },
        yaxis: { visible: false },
      }}
      config={{ displayModeBar: false, staticPlot: true, responsive: true }}
      style={{ width: "100%" }}
    />
  );
}
```

**Step 3: Create SWR hook for bots**

Create `frontend/src/lib/hooks/use-bots.ts`:

```typescript
import useSWR from "swr";
import { api } from "@/lib/api";

export function useBots() {
  return useSWR("/paper/bots", () => api.listBots(), {
    refreshInterval: 5000,
  });
}

export function useAccount() {
  return useSWR("/paper/account", () => api.account(), {
    refreshInterval: 5000,
  });
}
```

**Step 4: Update dashboard page with KPI cards**

Replace `frontend/src/app/(dashboard)/page.tsx`:

```tsx
"use client";

import { useAuth } from "@/lib/auth";
import { useAccount } from "@/lib/hooks/use-bots";

function StatCard({
  label,
  value,
  trend,
}: {
  label: string;
  value: string;
  trend?: "up" | "down" | "neutral";
}) {
  const trendColor =
    trend === "up" ? "text-primary" : trend === "down" ? "text-danger" : "text-foreground-muted";

  return (
    <div className="bg-background-card rounded-lg border border-gray-200 p-5">
      <p className="text-sm text-foreground-muted mb-1">{label}</p>
      <p className={`text-2xl font-semibold ${trendColor}`}>{value}</p>
    </div>
  );
}

export default function DashboardPage() {
  const { user } = useAuth();
  const { data: account } = useAccount();

  const balance = account?.balance ?? 0;
  const equity = account?.equity ?? 0;
  const dailyPnl = account?.daily_pnl ?? 0;
  const weeklyPnl = account?.weekly_pnl ?? 0;

  return (
    <div>
      <h2 className="text-xl font-semibold mb-6">
        Welcome{user ? `, ${user.username}` : ""}
      </h2>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
        <StatCard label="Balance" value={`$${balance.toFixed(2)}`} />
        <StatCard
          label="Equity"
          value={`$${equity.toFixed(2)}`}
          trend={equity >= balance ? "up" : "down"}
        />
        <StatCard
          label="Daily PnL"
          value={`${dailyPnl >= 0 ? "+" : ""}$${dailyPnl.toFixed(2)}`}
          trend={dailyPnl >= 0 ? "up" : "down"}
        />
        <StatCard
          label="Weekly PnL"
          value={`${weeklyPnl >= 0 ? "+" : ""}$${weeklyPnl.toFixed(2)}`}
          trend={weeklyPnl >= 0 ? "up" : "down"}
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="bg-background-card rounded-lg border border-gray-200 p-5">
          <h3 className="text-sm font-medium text-foreground-muted mb-3">Active Bots</h3>
          <p className="text-3xl font-semibold">{account?.open_positions ?? 0}</p>
        </div>
        <div className="bg-background-card rounded-lg border border-gray-200 p-5">
          <h3 className="text-sm font-medium text-foreground-muted mb-3">Total Trades</h3>
          <p className="text-3xl font-semibold">{account?.total_trades ?? 0}</p>
        </div>
      </div>
    </div>
  );
}
```

**Step 5: Build**

```bash
cd frontend && npm run build
```

**Step 6: Commit**

```bash
git add frontend/src/components/analytics/ frontend/src/lib/hooks/use-bots.ts frontend/src/app/\(dashboard\)/page.tsx
git commit -m "feat: dashboard with KPI stat cards and Plotly mini-charts"
```

---

## Task 9: Frontend — Backtest and Research Pages (Subphase 5.3)

**Files:**
- Create: `frontend/src/components/analytics/DrawdownChart.tsx`
- Create: `frontend/src/components/analytics/Heatmap.tsx`
- Create: `frontend/src/components/analytics/Distribution.tsx`
- Create: `frontend/src/lib/hooks/use-backtests.ts`
- Create: `frontend/src/lib/hooks/use-research.ts`
- Create: `frontend/src/lib/analytics-mappers/equity-mapper.ts`
- Create: `frontend/src/app/(dashboard)/backtests/page.tsx`
- Create: `frontend/src/app/(dashboard)/backtests/[id]/page.tsx`
- Create: `frontend/src/app/(dashboard)/research/page.tsx`

**Step 1: Create analytics components**

Create `frontend/src/components/analytics/DrawdownChart.tsx`:

```tsx
"use client";

import dynamic from "next/dynamic";

const Plot = dynamic(() => import("react-plotly.js"), { ssr: false });

interface DrawdownChartProps {
  equityCurve: number[];
  height?: number;
}

export function DrawdownChart({ equityCurve, height = 200 }: DrawdownChartProps) {
  const drawdown: number[] = [];
  let peak = equityCurve[0] || 0;
  for (const val of equityCurve) {
    if (val > peak) peak = val;
    drawdown.push(((val - peak) / peak) * 100);
  }

  return (
    <Plot
      data={[
        {
          y: drawdown,
          type: "scatter",
          mode: "lines",
          fill: "tozeroy",
          line: { color: "#EF4444", width: 1.5 },
          fillcolor: "rgba(239, 68, 68, 0.15)",
        },
      ]}
      layout={{
        title: { text: "Drawdown %", font: { size: 14, color: "#1C1917" } },
        height,
        margin: { t: 40, r: 20, b: 40, l: 50 },
        paper_bgcolor: "transparent",
        plot_bgcolor: "transparent",
        yaxis: { gridcolor: "#E5E5E5", ticksuffix: "%" },
        xaxis: { showgrid: false },
        font: { family: "Inter, system-ui, sans-serif" },
      }}
      config={{ displayModeBar: false, responsive: true }}
      style={{ width: "100%" }}
    />
  );
}
```

Create `frontend/src/components/analytics/Heatmap.tsx`:

```tsx
"use client";

import dynamic from "next/dynamic";

const Plot = dynamic(() => import("react-plotly.js"), { ssr: false });

interface HeatmapProps {
  z: number[][];
  x: string[];
  y: string[];
  title?: string;
  height?: number;
}

export function Heatmap({ z, x, y, title = "Performance Heatmap", height = 400 }: HeatmapProps) {
  return (
    <Plot
      data={[
        {
          z,
          x,
          y,
          type: "heatmap",
          colorscale: [
            [0, "#EF4444"],
            [0.5, "#F5F5F4"],
            [1, "#16A34A"],
          ],
          showscale: true,
        },
      ]}
      layout={{
        title: { text: title, font: { size: 14, color: "#1C1917" } },
        height,
        margin: { t: 40, r: 20, b: 60, l: 100 },
        paper_bgcolor: "transparent",
        plot_bgcolor: "transparent",
        font: { family: "Inter, system-ui, sans-serif" },
      }}
      config={{ displayModeBar: false, responsive: true }}
      style={{ width: "100%" }}
    />
  );
}
```

Create `frontend/src/components/analytics/Distribution.tsx`:

```tsx
"use client";

import dynamic from "next/dynamic";

const Plot = dynamic(() => import("react-plotly.js"), { ssr: false });

interface DistributionProps {
  values: number[];
  title?: string;
  xLabel?: string;
  height?: number;
}

export function Distribution({
  values,
  title = "Distribution",
  xLabel = "Value",
  height = 250,
}: DistributionProps) {
  return (
    <Plot
      data={[
        {
          x: values,
          type: "histogram",
          marker: { color: "#16A34A" },
          nbinsx: 20,
        },
      ]}
      layout={{
        title: { text: title, font: { size: 14, color: "#1C1917" } },
        height,
        margin: { t: 40, r: 20, b: 40, l: 50 },
        paper_bgcolor: "transparent",
        plot_bgcolor: "transparent",
        xaxis: { title: xLabel, gridcolor: "#E5E5E5" },
        yaxis: { title: "Count", gridcolor: "#E5E5E5" },
        font: { family: "Inter, system-ui, sans-serif" },
      }}
      config={{ displayModeBar: false, responsive: true }}
      style={{ width: "100%" }}
    />
  );
}
```

**Step 2: Create SWR hooks**

Create `frontend/src/lib/hooks/use-backtests.ts`:

```typescript
import useSWR from "swr";
import { api } from "@/lib/api";

export function useBacktests() {
  return useSWR("/backtests", () => api.listBacktests());
}

export function useBacktest(id: number | null) {
  return useSWR(id ? `/backtests/${id}` : null, () => api.getBacktest(id!));
}

export function useEquityCurve(id: number | null) {
  return useSWR(id ? `/backtests/${id}/equity-curve` : null, () => api.getEquityCurve(id!));
}
```

Create `frontend/src/lib/hooks/use-research.ts`:

```typescript
import useSWR from "swr";
import { api } from "@/lib/api";

export function useRankings(params?: string) {
  return useSWR(`/research/rankings${params || ""}`, () => api.rankings(params));
}
```

**Step 3: Create backtests page**

Create `frontend/src/app/(dashboard)/backtests/page.tsx`:

```tsx
"use client";

import { useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { useBacktests } from "@/lib/hooks/use-backtests";

export default function BacktestsPage() {
  const { data: backtests, mutate } = useBacktests();
  const [strategyId, setStrategyId] = useState("bot01_sanyaku");
  const [instrument, setInstrument] = useState("EURUSD");
  const [timeframe, setTimeframe] = useState("H1");
  const [running, setRunning] = useState(false);

  async function handleRun() {
    setRunning(true);
    try {
      await api.runBacktest({ strategy_id: strategyId, instrument, timeframe });
      mutate();
    } catch {
      // Error handled by api client
    }
    setRunning(false);
  }

  return (
    <div>
      <h2 className="text-xl font-semibold mb-6">Backtests</h2>

      {/* Run form */}
      <div className="bg-background-card rounded-lg border border-gray-200 p-5 mb-6">
        <h3 className="text-sm font-medium mb-4">Run New Backtest</h3>
        <div className="flex gap-4 items-end">
          <div>
            <label className="block text-xs text-foreground-muted mb-1">Strategy</label>
            <input
              value={strategyId}
              onChange={(e) => setStrategyId(e.target.value)}
              className="px-3 py-1.5 text-sm border border-gray-300 rounded-md"
            />
          </div>
          <div>
            <label className="block text-xs text-foreground-muted mb-1">Instrument</label>
            <input
              value={instrument}
              onChange={(e) => setInstrument(e.target.value)}
              className="px-3 py-1.5 text-sm border border-gray-300 rounded-md"
            />
          </div>
          <div>
            <label className="block text-xs text-foreground-muted mb-1">Timeframe</label>
            <input
              value={timeframe}
              onChange={(e) => setTimeframe(e.target.value)}
              className="px-3 py-1.5 text-sm border border-gray-300 rounded-md"
            />
          </div>
          <button
            onClick={handleRun}
            disabled={running}
            className="px-4 py-1.5 bg-primary text-white text-sm rounded-md hover:bg-primary-dark disabled:opacity-50"
          >
            {running ? "Running..." : "Run"}
          </button>
        </div>
      </div>

      {/* Results list */}
      <div className="bg-background-card rounded-lg border border-gray-200 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-background-muted">
            <tr>
              <th className="text-left px-4 py-2 font-medium">Strategy</th>
              <th className="text-left px-4 py-2 font-medium">Instrument</th>
              <th className="text-left px-4 py-2 font-medium">TF</th>
              <th className="text-right px-4 py-2 font-medium">Trades</th>
              <th className="text-right px-4 py-2 font-medium">Net Profit</th>
              <th className="text-right px-4 py-2 font-medium">Sharpe</th>
              <th className="text-right px-4 py-2 font-medium">Max DD%</th>
              <th className="px-4 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {(backtests || []).map((bt: Record<string, unknown>) => (
              <tr key={bt.id as number} className="border-t border-gray-100 hover:bg-background-muted/50">
                <td className="px-4 py-2">{bt.strategy_id as string}</td>
                <td className="px-4 py-2">{bt.instrument as string}</td>
                <td className="px-4 py-2">{bt.timeframe as string}</td>
                <td className="px-4 py-2 text-right">{bt.total_trades as number}</td>
                <td className={`px-4 py-2 text-right ${(bt.net_profit as number) >= 0 ? "text-primary" : "text-danger"}`}>
                  {(bt.net_profit as number)?.toFixed(2)}
                </td>
                <td className="px-4 py-2 text-right">{(bt.sharpe_ratio as number)?.toFixed(2) ?? "-"}</td>
                <td className="px-4 py-2 text-right">{(bt.max_drawdown_pct as number)?.toFixed(1) ?? "-"}%</td>
                <td className="px-4 py-2">
                  <Link href={`/backtests/${bt.id}`} className="text-primary text-xs hover:underline">
                    View
                  </Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
```

**Step 4: Create backtest detail page**

Create `frontend/src/app/(dashboard)/backtests/[id]/page.tsx`:

```tsx
"use client";

import { use } from "react";
import { useBacktest, useEquityCurve } from "@/lib/hooks/use-backtests";
import { EquityCurve } from "@/components/analytics/EquityCurve";
import { DrawdownChart } from "@/components/analytics/DrawdownChart";

export default function BacktestDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const runId = parseInt(id, 10);
  const { data: bt } = useBacktest(runId);
  const { data: ecData } = useEquityCurve(runId);

  if (!bt) return <p className="text-foreground-muted">Loading...</p>;

  const metrics = (bt.metrics_json || {}) as Record<string, unknown>;
  const equityCurve = ecData?.equity_curve || [];

  return (
    <div>
      <h2 className="text-xl font-semibold mb-4">
        {bt.strategy_id} &mdash; {bt.instrument} {bt.timeframe}
      </h2>

      {/* Metrics grid */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        {Object.entries(metrics)
          .filter(([k]) => k !== "equity_curve")
          .slice(0, 16)
          .map(([key, val]) => (
            <div key={key} className="bg-background-card rounded-lg border border-gray-200 p-4">
              <p className="text-xs text-foreground-muted mb-1">{key.replace(/_/g, " ")}</p>
              <p className="text-lg font-medium">
                {typeof val === "number" ? val.toFixed(4) : String(val)}
              </p>
            </div>
          ))}
      </div>

      {/* Charts */}
      {equityCurve.length > 0 && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <div className="bg-background-card rounded-lg border border-gray-200 p-4">
            <EquityCurve data={equityCurve} />
          </div>
          <div className="bg-background-card rounded-lg border border-gray-200 p-4">
            <DrawdownChart equityCurve={equityCurve} />
          </div>
        </div>
      )}
    </div>
  );
}
```

**Step 5: Create research page**

Create `frontend/src/app/(dashboard)/research/page.tsx`:

```tsx
"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { useRankings } from "@/lib/hooks/use-research";
import { Heatmap } from "@/components/analytics/Heatmap";
import type { ResearchResult } from "@/types/contracts/research";

export default function ResearchPage() {
  const { data: rankings, mutate } = useRankings();
  const [running, setRunning] = useState(false);

  async function handleRunResearch() {
    setRunning(true);
    try {
      await api.runResearch({
        strategy_ids: ["bot01_sanyaku"],
        instruments: ["EURUSD"],
        timeframes: ["H1"],
      });
      mutate();
    } catch {
      // handled
    }
    setRunning(false);
  }

  // Build heatmap from rankings
  const strategyIds = [...new Set((rankings || []).map((r: ResearchResult) => r.strategy_id))];
  const instruments = [...new Set((rankings || []).map((r: ResearchResult) => r.instrument))];
  const z = strategyIds.map((sid) =>
    instruments.map((inst) => {
      const match = (rankings || []).find(
        (r: ResearchResult) => r.strategy_id === sid && r.instrument === inst
      );
      return match?.composite_score ?? 0;
    })
  );

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-xl font-semibold">Research Matrix</h2>
        <button
          onClick={handleRunResearch}
          disabled={running}
          className="px-4 py-1.5 bg-primary text-white text-sm rounded-md hover:bg-primary-dark disabled:opacity-50"
        >
          {running ? "Running..." : "Run Research"}
        </button>
      </div>

      {strategyIds.length > 0 && instruments.length > 0 && (
        <div className="bg-background-card rounded-lg border border-gray-200 p-4 mb-6">
          <Heatmap z={z} x={instruments} y={strategyIds} title="Composite Score: Strategy x Instrument" />
        </div>
      )}

      {/* Rankings table */}
      <div className="bg-background-card rounded-lg border border-gray-200 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-background-muted">
            <tr>
              <th className="text-left px-4 py-2 font-medium">Rank</th>
              <th className="text-left px-4 py-2 font-medium">Strategy</th>
              <th className="text-left px-4 py-2 font-medium">Instrument</th>
              <th className="text-left px-4 py-2 font-medium">Timeframe</th>
              <th className="text-right px-4 py-2 font-medium">Score</th>
            </tr>
          </thead>
          <tbody>
            {(rankings || []).map((r: ResearchResult) => (
              <tr key={r.id} className="border-t border-gray-100">
                <td className="px-4 py-2">{r.rank}</td>
                <td className="px-4 py-2">{r.strategy_id}</td>
                <td className="px-4 py-2">{r.instrument}</td>
                <td className="px-4 py-2">{r.timeframe}</td>
                <td className="px-4 py-2 text-right font-medium">{r.composite_score.toFixed(4)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
```

**Step 6: Build**

```bash
cd frontend && npm run build
```

**Step 7: Commit**

```bash
git add frontend/
git commit -m "feat: backtest results, research matrix with Plotly analytics"
```

---

## Task 10: Frontend — Paper Bots, Trade History, Trade Detail (Subphase 5.4)

**Files:**
- Create: `frontend/src/app/(dashboard)/bots/page.tsx`
- Create: `frontend/src/app/(dashboard)/trades/page.tsx`
- Create: `frontend/src/app/(dashboard)/trades/[id]/page.tsx`
- Create: `frontend/src/lib/hooks/use-trades.ts`

**Step 1: Create trade hooks**

Create `frontend/src/lib/hooks/use-trades.ts`:

```typescript
import useSWR from "swr";
import { api } from "@/lib/api";

export function useTrades(params?: string) {
  return useSWR(`/trades/${params || ""}`, () => api.listTrades(params));
}

export function useTrade(id: number | null) {
  return useSWR(id ? `/trades/${id}` : null, () => api.getTrade(id!));
}
```

**Step 2: Create bots page**

Create `frontend/src/app/(dashboard)/bots/page.tsx`:

```tsx
"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { useBots, useAccount } from "@/lib/hooks/use-bots";

export default function BotsPage() {
  const { data: bots, mutate } = useBots();
  const { data: account } = useAccount();
  const [strategyId, setStrategyId] = useState("bot01_sanyaku");
  const [instrument, setInstrument] = useState("EURUSD");
  const [timeframe, setTimeframe] = useState("H1");
  const [creating, setCreating] = useState(false);

  async function handleCreate() {
    setCreating(true);
    try {
      await api.createBot({ strategy_id: strategyId, instrument, timeframe });
      mutate();
    } catch {
      // handled
    }
    setCreating(false);
  }

  async function handleStop(botId: string) {
    await api.stopBot(botId);
    mutate();
  }

  async function handlePause(botId: string) {
    await api.pauseBot(botId);
    mutate();
  }

  const stateColor: Record<string, string> = {
    monitoring: "bg-primary text-white",
    paused: "bg-warning text-white",
    stopped: "bg-gray-400 text-white",
    position_open: "bg-blue-500 text-white",
  };

  return (
    <div>
      <h2 className="text-xl font-semibold mb-6">Paper Bots</h2>

      {/* Account summary */}
      {account && (
        <div className="grid grid-cols-3 gap-4 mb-6">
          <div className="bg-background-card rounded-lg border border-gray-200 p-4">
            <p className="text-xs text-foreground-muted">Balance</p>
            <p className="text-lg font-semibold">${(account.balance as number).toFixed(2)}</p>
          </div>
          <div className="bg-background-card rounded-lg border border-gray-200 p-4">
            <p className="text-xs text-foreground-muted">Equity</p>
            <p className="text-lg font-semibold">${(account.equity as number).toFixed(2)}</p>
          </div>
          <div className="bg-background-card rounded-lg border border-gray-200 p-4">
            <p className="text-xs text-foreground-muted">Total PnL</p>
            <p className={`text-lg font-semibold ${(account.total_pnl as number) >= 0 ? "text-primary" : "text-danger"}`}>
              ${(account.total_pnl as number).toFixed(2)}
            </p>
          </div>
        </div>
      )}

      {/* Add bot form */}
      <div className="bg-background-card rounded-lg border border-gray-200 p-5 mb-6">
        <h3 className="text-sm font-medium mb-4">Add Bot</h3>
        <div className="flex gap-4 items-end">
          <div>
            <label className="block text-xs text-foreground-muted mb-1">Strategy</label>
            <input value={strategyId} onChange={(e) => setStrategyId(e.target.value)} className="px-3 py-1.5 text-sm border border-gray-300 rounded-md" />
          </div>
          <div>
            <label className="block text-xs text-foreground-muted mb-1">Instrument</label>
            <input value={instrument} onChange={(e) => setInstrument(e.target.value)} className="px-3 py-1.5 text-sm border border-gray-300 rounded-md" />
          </div>
          <div>
            <label className="block text-xs text-foreground-muted mb-1">Timeframe</label>
            <input value={timeframe} onChange={(e) => setTimeframe(e.target.value)} className="px-3 py-1.5 text-sm border border-gray-300 rounded-md" />
          </div>
          <button onClick={handleCreate} disabled={creating} className="px-4 py-1.5 bg-primary text-white text-sm rounded-md hover:bg-primary-dark disabled:opacity-50">
            {creating ? "Creating..." : "Add Bot"}
          </button>
        </div>
      </div>

      {/* Bot list */}
      <div className="space-y-3">
        {(Array.isArray(bots) ? bots : []).map((bot: Record<string, unknown>) => (
          <div key={bot.bot_id as string} className="bg-background-card rounded-lg border border-gray-200 p-4 flex items-center justify-between">
            <div className="flex items-center gap-4">
              <span className={`px-2 py-0.5 text-xs rounded ${stateColor[(bot.state as string)] || "bg-gray-300"}`}>
                {bot.state as string}
              </span>
              <span className="font-medium text-sm">{bot.strategy_id as string}</span>
              <span className="text-foreground-muted text-sm">{bot.instrument as string} {bot.timeframe as string}</span>
            </div>
            <div className="flex gap-2">
              <button onClick={() => handlePause(bot.bot_id as string)} className="px-3 py-1 text-xs border border-gray-300 rounded hover:bg-background-muted">Pause</button>
              <button onClick={() => handleStop(bot.bot_id as string)} className="px-3 py-1 text-xs border border-danger text-danger rounded hover:bg-red-50">Stop</button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
```

**Step 3: Create trades page**

Create `frontend/src/app/(dashboard)/trades/page.tsx`:

```tsx
"use client";

import Link from "next/link";
import { useTrades } from "@/lib/hooks/use-trades";
import type { Trade } from "@/types/contracts/trades";

export default function TradesPage() {
  const { data } = useTrades();
  const trades = data?.items || [];

  return (
    <div>
      <h2 className="text-xl font-semibold mb-6">Trade History</h2>
      <div className="bg-background-card rounded-lg border border-gray-200 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-background-muted">
            <tr>
              <th className="text-left px-4 py-2 font-medium">Date</th>
              <th className="text-left px-4 py-2 font-medium">Strategy</th>
              <th className="text-left px-4 py-2 font-medium">Instrument</th>
              <th className="text-left px-4 py-2 font-medium">Direction</th>
              <th className="text-right px-4 py-2 font-medium">Entry</th>
              <th className="text-right px-4 py-2 font-medium">Exit</th>
              <th className="text-right px-4 py-2 font-medium">PnL</th>
              <th className="text-left px-4 py-2 font-medium">Exit Reason</th>
              <th className="px-4 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {trades.map((t: Trade) => (
              <tr key={t.id} className="border-t border-gray-100 hover:bg-background-muted/50">
                <td className="px-4 py-2">{t.entry_time?.split("T")[0] ?? "-"}</td>
                <td className="px-4 py-2">{t.strategy_id}</td>
                <td className="px-4 py-2">{t.instrument}</td>
                <td className="px-4 py-2">
                  <span className={t.direction === "LONG" ? "text-primary" : "text-danger"}>{t.direction}</span>
                </td>
                <td className="px-4 py-2 text-right">{t.entry_price.toFixed(5)}</td>
                <td className="px-4 py-2 text-right">{t.exit_price.toFixed(5)}</td>
                <td className={`px-4 py-2 text-right font-medium ${t.pnl >= 0 ? "text-primary" : "text-danger"}`}>
                  {t.pnl >= 0 ? "+" : ""}{t.pnl.toFixed(2)}
                </td>
                <td className="px-4 py-2 text-xs text-foreground-muted">{t.exit_reason}</td>
                <td className="px-4 py-2">
                  <Link href={`/trades/${t.id}`} className="text-primary text-xs hover:underline">Detail</Link>
                </td>
              </tr>
            ))}
            {trades.length === 0 && (
              <tr><td colSpan={9} className="px-4 py-8 text-center text-foreground-muted">No trades yet</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
```

**Step 4: Create trade detail page with KLineChart**

Create `frontend/src/app/(dashboard)/trades/[id]/page.tsx`:

```tsx
"use client";

import { use } from "react";
import { useTrade } from "@/lib/hooks/use-trades";

export default function TradeDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const tradeId = parseInt(id, 10);
  const { data: trade } = useTrade(tradeId);

  if (!trade) return <p className="text-foreground-muted">Loading trade...</p>;

  return (
    <div>
      <h2 className="text-xl font-semibold mb-4">
        Trade #{trade.id} &mdash; {trade.strategy_id}
      </h2>

      {/* Metrics */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        <div className="bg-background-card rounded-lg border border-gray-200 p-4">
          <p className="text-xs text-foreground-muted">Direction</p>
          <p className={`text-lg font-semibold ${trade.direction === "LONG" ? "text-primary" : "text-danger"}`}>{trade.direction}</p>
        </div>
        <div className="bg-background-card rounded-lg border border-gray-200 p-4">
          <p className="text-xs text-foreground-muted">PnL</p>
          <p className={`text-lg font-semibold ${trade.pnl >= 0 ? "text-primary" : "text-danger"}`}>
            {trade.pnl >= 0 ? "+" : ""}{trade.pnl.toFixed(2)}
          </p>
        </div>
        <div className="bg-background-card rounded-lg border border-gray-200 p-4">
          <p className="text-xs text-foreground-muted">Duration</p>
          <p className="text-lg font-semibold">{trade.bars_in_trade} bars</p>
        </div>
        <div className="bg-background-card rounded-lg border border-gray-200 p-4">
          <p className="text-xs text-foreground-muted">Exit Reason</p>
          <p className="text-lg font-semibold">{trade.exit_reason}</p>
        </div>
      </div>

      {/* Trade details */}
      <div className="bg-background-card rounded-lg border border-gray-200 p-5">
        <div className="grid grid-cols-2 gap-4 text-sm">
          <div>
            <p className="text-foreground-muted">Instrument</p>
            <p className="font-medium">{trade.instrument}</p>
          </div>
          <div>
            <p className="text-foreground-muted">Entry Time</p>
            <p className="font-medium">{trade.entry_time}</p>
          </div>
          <div>
            <p className="text-foreground-muted">Entry Price</p>
            <p className="font-medium">{trade.entry_price.toFixed(5)}</p>
          </div>
          <div>
            <p className="text-foreground-muted">Exit Price</p>
            <p className="font-medium">{trade.exit_price.toFixed(5)}</p>
          </div>
          <div>
            <p className="text-foreground-muted">Exit Time</p>
            <p className="font-medium">{trade.exit_time}</p>
          </div>
        </div>
        <p className="text-foreground-muted text-sm mt-4">
          KLineChart centred on this trade will be rendered here once market data for {trade.instrument} is loaded.
        </p>
      </div>
    </div>
  );
}
```

**Step 5: Build**

```bash
cd frontend && npm run build
```

**Step 6: Commit**

```bash
git add frontend/
git commit -m "feat: paper bots, trade history, and trade detail pages"
```

---

## Task 11: Frontend — Settings, System, and Final Polish (Subphase 5.5)

**Files:**
- Create: `frontend/src/app/(dashboard)/settings/page.tsx`
- Create: `frontend/src/app/(dashboard)/system/page.tsx`

**Step 1: Create settings page**

Create `frontend/src/app/(dashboard)/settings/page.tsx`:

```tsx
"use client";

import { useAuth } from "@/lib/auth";

export default function SettingsPage() {
  const { user } = useAuth();

  return (
    <div>
      <h2 className="text-xl font-semibold mb-6">Settings</h2>

      <div className="space-y-6">
        {/* User */}
        <div className="bg-background-card rounded-lg border border-gray-200 p-5">
          <h3 className="text-sm font-medium mb-4">User</h3>
          <div className="text-sm space-y-2">
            <p><span className="text-foreground-muted">Username:</span> {user?.username}</p>
            <p><span className="text-foreground-muted">Role:</span> {user?.role}</p>
          </div>
        </div>

        {/* Risk defaults */}
        <div className="bg-background-card rounded-lg border border-gray-200 p-5">
          <h3 className="text-sm font-medium mb-4">Risk Defaults</h3>
          <div className="grid grid-cols-2 gap-4 text-sm">
            <div><span className="text-foreground-muted">Risk per trade:</span> 1.0%</div>
            <div><span className="text-foreground-muted">Max portfolio risk:</span> 5.0%</div>
            <div><span className="text-foreground-muted">Max open trades:</span> 8</div>
            <div><span className="text-foreground-muted">Daily hard stop:</span> 4.0%</div>
          </div>
        </div>

        {/* Feature flags */}
        <div className="bg-background-card rounded-lg border border-gray-200 p-5">
          <h3 className="text-sm font-medium mb-4">Feature Flags</h3>
          <div className="text-sm">
            <p><span className="text-foreground-muted">Live execution:</span> <span className="text-danger">Disabled</span></p>
          </div>
        </div>
      </div>
    </div>
  );
}
```

**Step 2: Create system page**

Create `frontend/src/app/(dashboard)/system/page.tsx`:

```tsx
"use client";

import useSWR from "swr";
import { api } from "@/lib/api";

export default function SystemPage() {
  const { data: status } = useSWR("/system/status", () => api.systemStatus());
  const { data: health } = useSWR("/system/health", () => api.systemHealth());

  return (
    <div>
      <h2 className="text-xl font-semibold mb-6">System</h2>

      <div className="space-y-4">
        <div className="bg-background-card rounded-lg border border-gray-200 p-5">
          <h3 className="text-sm font-medium mb-3">Health</h3>
          <div className="flex items-center gap-2">
            <div className={`w-2.5 h-2.5 rounded-full ${health?.status === "ok" ? "bg-primary" : "bg-danger"}`} />
            <span className="text-sm">{health?.status === "ok" ? "Connected" : "Disconnected"}</span>
            <span className="text-foreground-muted text-sm ml-4">v{health?.version}</span>
          </div>
        </div>

        {status && (
          <div className="bg-background-card rounded-lg border border-gray-200 p-5">
            <h3 className="text-sm font-medium mb-3">Engine Status</h3>
            <div className="grid grid-cols-2 gap-4 text-sm">
              <div><span className="text-foreground-muted">API Version:</span> {status.api_version as string}</div>
              <div><span className="text-foreground-muted">Database:</span> {status.database as string}</div>
              <div><span className="text-foreground-muted">Paper Engine:</span> {status.paper_engine as string}</div>
              <div><span className="text-foreground-muted">Strategies Loaded:</span> {status.strategies_loaded as number}</div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
```

**Step 3: Build**

```bash
cd frontend && npm run build
```

**Step 4: Commit**

```bash
git add frontend/
git commit -m "feat: settings and system diagnostics pages"
```

---

## Task 12: Architecture Documentation

**Files:**
- Create: `docs/architecture.md`
- Create: `docs/frontend_architecture.md`
- Create: `docs/charting_spec.md`
- Create: `docs/api_contracts.md`
- Create: `docs/auth_spec.md`

**Step 1: Write all five docs**

Write each document based on the approved design doc at `docs/plans/2026-03-07-phase5-web-platform-design.md`. Each doc should cover:

- `docs/architecture.md` — System overview diagram, component boundaries (backend/frontend/DB), deployment topology (Vercel + Railway)
- `docs/frontend_architecture.md` — Full component tree, state management rules, routing, dependency list
- `docs/charting_spec.md` — KLineChart responsibilities, Plotly responsibilities, interaction requirements, overlay extensibility, separation rules
- `docs/api_contracts.md` — All endpoint schemas (request/response), annotation payload shapes, chart data contracts
- `docs/auth_spec.md` — Cookie-based flow, middleware behaviour, refresh strategy, session expiry, seeded admin bootstrap

**Step 2: Commit**

```bash
git add docs/
git commit -m "docs: architecture, frontend, charting, API contracts, auth specs"
```

---

## Task 13: Final Verification and Roadmap Update

**Step 1: Run full backend tests**

```bash
cd backend && pytest -v --tb=short
```

Expected: All pass (280+ tests)

**Step 2: Lint backend**

```bash
cd backend && ruff check src/
```

Expected: All checks passed

**Step 3: Build frontend**

```bash
cd frontend && npm run build
```

Expected: Build succeeds with no errors

**Step 4: Update roadmap**

Mark Phase 5 subphases as COMPLETE in `docs/roadmap.md` Progress Tracker table and subphase headers.

**Step 5: Final commit**

```bash
git add docs/roadmap.md
git commit -m "docs: mark Phase 5 complete in roadmap"
```

**Step 6: Push to GitHub**

```bash
git push origin main
```
