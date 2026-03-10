# Phase 14.2: Drawing Tools Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add interactive drawing tools to the trading chart with server-side persistence.

**Architecture:** Frontend DrawingToolbar selects a tool type, TradingChart activates klinecharts built-in overlays for drawing. On completion/move/delete, changes are persisted to a `chart_drawings` DB table via CRUD API. Drawings auto-load when the chart mounts for the current instrument/timeframe.

**Tech Stack:** klinecharts v10 overlays (built-in), FastAPI CRUD, SQLAlchemy model, React state management

---

### Task 1: ChartDrawingModel + repository functions

**Files:**
- Modify: `backend/src/fibokei/db/models.py`
- Modify: `backend/src/fibokei/db/repository.py`
- Test: `backend/tests/test_drawing_repository.py`

Add `ChartDrawingModel` to the DB models and CRUD repository functions.

**Model fields:**
- `id` (int, PK, autoincrement)
- `user_id` (int, nullable=False, index=True)
- `instrument` (String(20), nullable=False, index=True)
- `timeframe` (String(10), nullable=False)
- `tool_type` (String(30), nullable=False) — e.g. "straightLine", "horizontalStraightLine", "rayLine", "fibonacciLine", "parallelStraightLine"
- `points_json` (JSON, nullable=False) — array of {timestamp, value} point objects
- `styles_json` (JSON, nullable=True) — optional style overrides
- `lock` (bool, default=False)
- `visible` (bool, default=True)
- `created_at` (DateTime, UTC default)
- `updated_at` (DateTime, UTC default + onupdate)

**Repository functions:**
- `save_drawing(session, drawing_data) -> ChartDrawingModel`
- `get_drawings(session, user_id, instrument, timeframe) -> list[ChartDrawingModel]`
- `update_drawing(session, drawing_id, user_id, updates) -> ChartDrawingModel | None`
- `delete_drawing(session, drawing_id, user_id) -> bool`
- `delete_all_drawings(session, user_id, instrument, timeframe) -> int`

---

### Task 2: Drawing CRUD API routes + schemas

**Files:**
- Create: `backend/src/fibokei/api/routes/drawings.py`
- Create: `backend/src/fibokei/api/schemas/drawings.py`
- Modify: `backend/src/fibokei/api/app.py` (register router)

**Schemas (Pydantic):**

```python
class PointSchema(BaseModel):
    timestamp: int  # Unix ms
    value: float

class DrawingCreate(BaseModel):
    instrument: str = Field(max_length=20)
    timeframe: str = Field(max_length=10)
    tool_type: str = Field(max_length=30)
    points: list[PointSchema]
    styles: dict | None = None
    lock: bool = False
    visible: bool = True

class DrawingUpdate(BaseModel):
    points: list[PointSchema] | None = None
    styles: dict | None = None
    lock: bool | None = None
    visible: bool | None = None

class DrawingResponse(BaseModel):
    id: int
    instrument: str
    timeframe: str
    tool_type: str
    points: list[PointSchema]
    styles: dict | None
    lock: bool
    visible: bool
    created_at: str
    updated_at: str
```

**Routes:**
- `GET /drawings?instrument=X&timeframe=Y` → list drawings for chart
- `POST /drawings` → create drawing
- `PUT /drawings/{id}` → update drawing points/styles
- `DELETE /drawings/{id}` → delete single drawing
- `DELETE /drawings?instrument=X&timeframe=Y` → clear all drawings for chart

All routes require auth (`Depends(get_current_user)`).

---

### Task 3: Frontend drawing types + API client methods

**Files:**
- Create: `frontend/src/types/contracts/drawings.ts`
- Modify: `frontend/src/lib/api.ts`

**Types:**
```typescript
export interface DrawingPoint {
  timestamp: number;
  value: number;
}

export interface ChartDrawing {
  id: number;
  instrument: string;
  timeframe: string;
  tool_type: string;
  points: DrawingPoint[];
  styles: Record<string, unknown> | null;
  lock: boolean;
  visible: boolean;
  created_at: string;
  updated_at: string;
}
```

**API methods:**
- `api.listDrawings(instrument, timeframe)` → GET
- `api.createDrawing(body)` → POST
- `api.updateDrawing(id, body)` → PUT
- `api.deleteDrawing(id)` → DELETE
- `api.clearDrawings(instrument, timeframe)` → DELETE with query params

---

### Task 4: DrawingToolbar component

**Files:**
- Create: `frontend/src/components/charts/panels/DrawingToolbar.tsx`

Vertical or horizontal toolbar with drawing tool buttons. Each button selects an `activeDrawingTool` or deselects if already active (toggle behavior).

**Tools to include:**
| Tool | klinecharts overlay name | Icon |
|------|--------------------------|------|
| Trend Line | `straightLine` | TrendingUp |
| Horizontal Line | `horizontalStraightLine` | Minus |
| Ray | `rayLine` | MoveRight |
| Fib Retracement | `fibonacciLine` | GitBranch |
| Parallel Channel | `parallelStraightLine` | Columns |
| Pointer (default) | `null` | MousePointer |

Props: `activeTool: string | null`, `onToolChange: (tool: string | null) => void`

Also include a "Clear All" button (Trash2 icon) that calls `onClearAll`.

---

### Task 5: Wire drawing tools through TradingChart

**Files:**
- Modify: `frontend/src/components/charts/core/TradingChart.tsx`
- Modify: `frontend/src/app/(dashboard)/charts/page.tsx`

**TradingChart changes:**
- Accept new props: `activeDrawingTool`, `instrument`, `timeframe`, `onDrawingCreated`, `onDrawingUpdated`, `onDrawingRemoved`
- When `activeDrawingTool` changes from null to a tool name: call `chart.createOverlay({ name: toolName })` to enter drawing mode
- Register overlay event handlers:
  - `onDrawEnd`: fires when user finishes drawing → call `onDrawingCreated` with overlay data (name, points, id)
  - `onPressedMoving` end: fires when user moves a completed drawing → call `onDrawingUpdated`
  - `onRemoved`: fires when overlay is deleted → call `onDrawingRemoved`
- Add method to load saved drawings: iterate saved drawings and call `chart.createOverlay()` for each with `lock`, `visible`, `points`

**Charts page changes:**
- Add `activeDrawingTool` state
- Add `useDrawings` hook or inline SWR for loading/saving drawings
- Pass DrawingToolbar + callbacks to TradingChart
- Wire create/update/delete to API calls

---

### Task 6: Auto-load drawings on mount + persist on change

**Files:**
- Create: `frontend/src/lib/hooks/use-drawings.ts`
- Modify: `frontend/src/app/(dashboard)/charts/page.tsx`

**useDrawings hook:**
- Fetches drawings for current instrument+timeframe via SWR
- Provides `createDrawing`, `updateDrawing`, `deleteDrawing`, `clearDrawings` mutation functions
- Returns `{ drawings, isLoading, createDrawing, updateDrawing, deleteDrawing, clearDrawings }`

**Auto-load flow:**
1. Chart mounts or instrument/timeframe changes
2. `useDrawings` fetches saved drawings from API
3. TradingChart receives drawings as prop and renders them via `chart.createOverlay()`
4. When user draws/moves/deletes, callbacks persist to API

---

### Task 7: Tests for backend drawing CRUD

**Files:**
- Create: `backend/tests/test_drawing_repository.py`
- Create: `backend/tests/test_drawing_api.py`

**Repository tests:**
- test_save_and_get_drawings
- test_update_drawing
- test_delete_drawing
- test_delete_all_drawings
- test_get_drawings_filters_by_user

**API tests:**
- test_create_drawing
- test_list_drawings
- test_update_drawing_points
- test_delete_drawing
- test_clear_drawings_for_chart
- test_unauthorized_access

---

### Task 8: Frontend build verification

Run `npx next build` to verify no TypeScript errors in frontend changes.

---
