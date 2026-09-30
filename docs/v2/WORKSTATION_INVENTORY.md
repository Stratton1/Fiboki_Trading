# Fiboki V2 workstation inventory

**What this is:** every page, control, state and shortcut in `apps/web`, what each one reads or
changes on the platform, and which test proves it. Written for the operator, not for developers.

**Date:** 2026-09-30. **Scope:** `apps/web` as built at `.next/BUILD_ID` 17:47 BST on 2026-09-30
(no source file is newer than that build). Nothing in `apps/web` source or in any existing test was
changed to produce this document.

**How it was verified (this session):**

- Every route file in `apps/web/app/` and every component it renders was read. Endpoints below are
  quoted from the code, with the file that calls them.
- `apps/web/tests/e2e/inventory-walkthrough.spec.ts` (new) walked every route against a fully mocked
  API. Command, run at 17:47:39Z to 17:51:52Z:
  `NEXT_PUBLIC_FIBOKI_API=http://127.0.0.1:8000 npx playwright test tests/e2e/inventory-walkthrough.spec.ts --project=desktop --reporter=line`
  Result: **33 tests, 31 passed, 2 failed.** Both failures are real findings (F-1 and F-2 below).
  In that run the walk opened and closed 112 popovers, menus, selects and sheets, opened and
  cancelled 8 confirm dialogs and inline forms, toggled 8 disclosures, pressed 270 g-chords
  (0 wrong), and opened ⇧K on 30 routes (0 failures). No mutating request was sent.
- Screenshots (dark theme) are in `apps/web/test-results/inventory/<route>-1440x900.png` and
  `<route>-390x844.png`. The per-route records are in `apps/web/test-results/inventory/findings/`.
  Playwright empties `test-results/` at the start of every run, so re-run the command to regenerate
  them.
- The existing specs were **not** re-run in this session. "Verified by" below names the tests that
  exercise each thing, read from the spec files; it does not claim they pass today.

**Update, 2026-09-30 (after the audit above).** The findings F-1, F-2, F-3, F-4, F-5, F-6, F-8, F-9,
F-10, F-11, F-13 and F-15 were fixed in `apps/web` the same day, and the visuals ranked 1, 2, 4, 5
and 8 in §6.1 were built (none needed a backend change). Each fixed finding is marked
"fixed 2026-09-30" in §4 with what changed and the test that proves it; the pages below describe the
workstation after the fix. Two routes were added: `/system/incidents/<id>` (§2.5) and
`/lifecycle/<hash>` (§2.2). The verification of the fixes is recorded under §4
("Verification of the 2026-09-30 fixes"). Nothing was committed.

**Words used below**

| Word | Meaning |
|---|---|
| Mutates | Sends a POST that changes something on the platform. Everything else only reads. |
| Source badge | The dashed strip under a panel title: LIVE, SEED, ABSENT or MIXED, the API's own words, and "as of" time (or "as-of time not supplied"). |
| Chip | The provenance label beside a number: hollow for simulated (BT dashed, WF solid, OOS with a corner flag, HOLDOUT double), filled for executed (PAPER, SHADOW, DEMO, LIVE). MIXED opens a popover with counts. |
| Standard view states | Every panel built on `AsyncBoundary` (`components/AsyncBoundary.tsx`) has the same five: **loading** ("Loading X…" and grey bars), **error** (red "Could not load X" with a FAILED badge, the API's message, "Nothing on this panel is current", the error code, HTTP status and correlation id, and a Retry button), **empty** (a titled message saying the platform answered with nothing, which is not a failure), **success**, and **stale/lagging** on top of success (an amber badge with the age of the last good read; its popover says why and has Retry). A view that has shown data never goes back to loading. |
| Sweep | `a11y.spec.ts` "axe, dark/light theme › <route>" and `csp.spec.ts` "no route triggers a violation" visit every route. They mock only the shell (`mockShell`), so pages whose API is not in `fixtures.ts` are swept **in their error state**. |
| Walk | `inventory-walkthrough.spec.ts` "inventory: <route>", which renders the success state of every page. |

---

## 1. Shell-wide controls (on every page except Sign in)

Code: `components/shell/Shell.tsx`, which renders the banner, frame, rail, page header, status bar,
hotkeys and command bus once, around every page. The Sign-in page gets the banner and frame only.

### 1.1 Execution-mode banner (top, sticky)

`components/ModeBanner.tsx`. Reads **GET /api/system/execution-mode** (`components/shell/platform.tsx`,
polled every 30 s; also fed by the stream heartbeat when the stream is live).

| Element | What it shows | Mutates |
|---|---|---|
| Mode word | BACKTEST, PAPER, SHADOW, DEMO or LIVE; "MODE …" while the first read is in flight; "MODE UNKNOWN" if the first read failed (and every mutating control is then disabled). | No |
| REAL MONEY | Shown in LIVE or whenever the API says the mode touches real money. | No |
| Operator name | In LIVE only, when the API supplies the signed-in operator. | No |
| Headline · detail | The API's own text. | No |
| STALE / DISCONNECTED · last good … · retrying | When a refresh has failed or is overdue. The mode shown is the last one the platform reported. | No |
| Kill switch state | "Kill switch disarmed", or "KILL SWITCH ARMED · PAUSE/FLATTEN". | No |
| "Kill switch" link | **LIVE only.** A link to `/trading/risk`. It does not open the dialog. | No |

Verified by: `mode-banner.spec.ts` (all 7 tests), `mode-frame.spec.ts` "live mode › shows REAL MONEY, the operator and an always-visible kill switch", "omits the operator's name…", "mode unknown › frame, favicon and title say unknown", `refresh.spec.ts` "the mode banner keeps the mode, marked stale, when a refresh fails", "the stale marker clears…", `a11y.spec.ts` "banner in <mode> mode", "banner when the mode is unknown".

### 1.2 Mode frame, tab title and favicon

`components/shell/Mode.tsx`. No frame in backtest; 1 px cyan paper; 2 px dashed violet shadow; 3 px
amber demo; 4 px magenta live; 2 px grey stripes when unknown. Tab title `[PAPER] Fiboki`,
`● LIVE Fiboki`, `[MODE UNKNOWN] Fiboki` and so on; a favicon per mode. No controls.

Verified by: `mode-frame.spec.ts` "<mode>: frame … title and favicon" (every mode), "each mode has its own favicon", "a failed refresh is stale, never unknown › the frame keeps the last mode through a poll failure"; `visual.spec.ts` "shell frame in <mode> mode".

### 1.3 Stream DISCONNECTED strip

`components/shell/Stream.tsx`. Appears under the banner after five consecutive stream failures:
"DISCONNECTED … Next automatic attempt in N s", with a **Reconnect** button (retries the
`GET /api/stream` connection now; mutates nothing). Figures stay on screen, marked stale.

Verified by: `stream.spec.ts` "backoff 1, 2 s with jitter, DISCONNECTED after five failures, REST fallback, Reconnect"; `freshness.spec.ts` "a dropped stream shows STALE, then DISCONNECTED, and the numbers remain".

### 1.4 Navigation rail and page header

`components/shell/Rail.tsx`, `components/shell/sections.ts`, `components/shell/PageHeader.tsx`.

| Control | What it does | Mutates |
|---|---|---|
| Nine section icons | Go to the first page of each section (tooltip gives the section name). | No |
| Expanded tree | Every page under its section (when the rail is expanded). | No |
| Collapse / Expand navigation (bottom of rail) | Toggles the rail width; remembered in this browser. | No |
| **Sections** (below 1024 px) | At the start of the page header row, in the document flow (it floated over content before, F-10). Opens the rail as a drawer; tapping the scrim or navigating closes it. | No |
| Page header: section label | The section's name; hovering shows its question. Entity pages belong to a section too: `/lifecycle/<hash>` to Strategy Lifecycle, `/system/incidents/<id>` to System & Incidents. | No |
| Page header: view tabs | Links to the other pages of the section. | No |
| Page header: action slot | Page-level actions. Only Risk & Exposure uses it ("Open positions"). | No |
| Skip to content | Keyboard link to `<main>`. | No |

Verified by: `responsive.spec.ts` "the nav is a drawer, closed by default", "the drawer opens, then closes on navigation", "the rail is a 56px icon column" / "the rail is a closed drawer", "the page header lists the section's views"; `a11y.spec.ts` "axe at phone width › <route> with the drawer open". The rail collapse toggle is **not covered by a test**.

Sections and the pages in each (the order used in the rest of this document follows the task's
grouping, not the rail):

| Section (chord) | Pages |
|---|---|
| Command (g c) | Overview `/`, Market Pulse `/market-pulse`, Alerts `/alerts` |
| Fleet & Positions (g f) | Portfolio `/trading/portfolio` |
| Strategy Lifecycle (g l) | Candidates `/trading/candidates` |
| Research Lab (g r) | Lab `/research`, Hypotheses, Experiments, Strategies, Parameter Lab, Validation, Datasets |
| Market Intelligence (g m) | Explorer `/markets`, Regimes, Correlations, Agents, Agent runs, Research Memory (plus the chart `/markets/<symbol>`) |
| Risk & Exposure (g x) | Risk `/trading/risk`, Exposure `/trading/exposure` |
| Data Quality (g d) | Market data `/markets/data-quality`, Data Health `/system/data-health` |
| System & Incidents (g s) | Services, Workers, Broker Health, Audit log, Settings, Legend |
| Journal (g j) | Trades `/trading/execution` |

### 1.5 Status bar (bottom)

`components/shell/StatusBar.tsx`.

| Item | What it shows / does | Calls | Mutates |
|---|---|---|---|
| Mode | Same mode as the banner, "· stale" when stale. | (shared read) | No |
| Stream | "stream off / connecting… / live · lag N ms / quiet / reconnecting (n) / disconnected"; hover gives detail. | `GET /api/stream` | No |
| Worker heartbeat | "worker hb 12s ago", toned by the platform's verdict (green, amber, red), "never" when no worker has beaten. | health / stream heartbeat | No |
| **API** (button) | "API ok/degraded/down/unreachable". Click opens the **Platform health** inspector sheet (overall status, every check, advisory), live while open. Escape or ✕ closes. | `GET /api/health` (20 s) | No |
| **data as of** (button) | The OLDEST platform as-of among views on screen (probes excluded). Click opens the **Data as of** sheet listing every view's path and as-of. | none extra | No |
| Operator | "Joe (admin)". | `GET /api/auth/me` | No |
| **Sign out** (icon beside the operator) | Signs out at once, **no confirmation**, then goes to Sign in. A failure shows a toast and keeps you signed in. | `POST /api/auth/logout` | Ends your session only |
| **Display settings** ("regular · dark") | Popover: Theme System/Dark/Light; Density Auto/Compact/Regular/Comfortable; P&L colours Green/red or Blue/orange. Stored in this browser only. | none | No |
| UTC clock | Current time, UTC. | none | No |

Below 640 px the bar wraps onto two or three lines instead of scrolling sideways with a hidden
scrollbar (it was cut off at "AP…", F-10).

Verified by: `responsive.spec.ts` "the status bar wraps: every item is on screen, nothing cut off"; `auth.spec.ts` "the principal is in the status bar; sign-out posts with CSRF and lands on sign-in"; `trust.spec.ts` W-03 (four tests on worker tone) and W-04 (two tests on "data as of"); `freshness.spec.ts` "the worker heartbeat age counts up between heartbeats", "the newest platform as-of is visible in the status bar"; `keyboard.spec.ts` "the inspector returns focus to the status bar", "the display popover changes the P&L palette"; walk (every route: API and as-of sheets, display popover opened and closed).

### 1.6 Kill switch from anywhere: ⇧K and the palette

`components/shell/Hotkeys.tsx`, `components/GlobalKillSwitch.tsx`, `components/KillSwitchControl.tsx`.
⇧K (or the palette's "Halt trading…") **opens** the "Halt trading" dialog. It never arms.

If the dialog may not open, a toast says why and nothing opens: mode loading, mode unknown,
disconnected, or a role without `can_arm_kill_switch`.

**Friction levels (the same flow as the Risk page's button):**

| Act | Choice | Reason | Typed phrase | Other |
|---|---|---|---|---|
| Arm PAUSE (stop opening and increasing) | Must pick; no default | 8+ characters, to the audit trail | **None, in any mode, LIVE included** | Consequences are the server's |
| Arm FLATTEN (close every position) | Must pick | 8+ characters | **FLATTEN**, in every mode | Danger-styled |
| Lift the halt (disarm; dialog "Lift the halt and resume trading") | Single choice | 8+ characters | **LIFT HALT** (was RE-ARM, F-13) | Consequences from `GET /api/trading/preflight/kill-switch-disarm` |

Calls on confirm: `POST /api/system/kill-switch/arm` `{mode, reason}` or `POST /api/system/kill-switch/disarm` `{reason}`.
Afterwards the dialog stays "Working…" until the platform echoes the new state (no optimistic
display); if no echo arrives in time the panel shows "confirming…". Escape and Cancel do nothing while
a request is in flight; a click on the backdrop never closes it. Nothing can be confirmed while the
mode is unknown.

Verified by: `keyboard.spec.ts` "⇧K opens the kill-switch dialog from any screen and arms nothing", "⇧K with a role that cannot arm says why and opens nothing", "its kill-switch action only opens the dialog", "Escape and Cancel do nothing while a request is in flight", "a backdrop click does not discard a typed reason"; `kill-switch.spec.ts` (every test); `mode-frame.spec.ts` "friction follows the act in LIVE…", "disables the kill switch", "disables disarm"; `promote.spec.ts` "renders the server-computed disarm consequences verbatim"; `stream.spec.ts` "no optimistic UI…", "Tom arms in his browser; Joe's banner and panel show it within 1 s"; `trust.spec.ts` W-07; walk (⇧K opened and cancelled on 30 routes; "inventory: disarm dialog on an armed kill switch").

### 1.7 Command palette (⌘K / Ctrl+K)

`components/shell/CommandPalette.tsx`. Nothing in it changes the platform. Full list as rendered
(recorded by the walk):

- **Actions (each opens a dialog):** "Halt trading… (opens the kill-switch dialog)" ⇧K; "Keyboard shortcuts" ?.
- **Go to**, 29 entries grouped by section: Overview (g c), Market Pulse, Alerts; Portfolio (g f); Candidates (g l); Lab (g r), Hypotheses, Experiments, Strategies, Parameter Lab, Validation, Datasets; Explorer (g m), Regimes, Correlations, Agents, Agent runs, Research Memory; Risk (g x), Exposure; Market data (g d), Data Health; Services (g s), Workers, Broker Health, Audit log, Settings, Legend; Trades (g j).
- **Open by id** (appears once you type; example for "EURUSD"): "Open trade EURUSD" (`/trading/execution?row=`), "Open strategy EURUSD" (`/trading/candidates?row=`), "Open instrument EURUSD" (`/markets?row=`), "Open chart: EURUSD" (`/markets/EURUSD`), "Open the parameter space of EURUSD" (`/research/parameter-lab?strategy=`), and for a number only, "Open audit entry #N" (`/system/logs?row=`). The target page decides whether the id exists and says "… is not in this result" if not.

Escape closes it and returns focus. Verified by: `keyboard.spec.ts` "Meta+K / Control+K opens it; typing and Enter navigates", "open by id: a trade id selects that row…", "Escape closes it and returns focus", "its kill-switch action only opens the dialog"; `chart-workstation.spec.ts` "the palette opens a chart by symbol"; `a11y.spec.ts` "command palette and shortcut sheet"; walk (palette opened, listed and closed on 30 routes).

### 1.8 Keyboard

`components/shell/Hotkeys.tsx`, `components/shell/ShortcutSheet.tsx`. No single key changes anything
on the platform. Single-key shortcuts are ignored while typing in a field or while a dialog is open.

| Keys | Does | Verified by |
|---|---|---|
| ⌘K / Ctrl+K | Palette | see 1.7 |
| ? | Shortcut sheet (every shortcut and chord; Close or Escape) | `keyboard.spec.ts` "? lists every shortcut and every chord" |
| g then c f l r m x d s j (within 1.5 s) | Go to that section's first page | `keyboard.spec.ts` covers **c, l, x, j, s only**; f, r, m, d are covered only by the walk (all nine pressed from every route, all landed) |
| ⇧K | Open the kill-switch dialog | see 1.6 |
| ⌘⇧D / Ctrl+Shift+D | Cycle density (toast confirms) | `keyboard.spec.ts` "…+Shift+D cycles density, remembered across a reload" |
| ⌘⇧L / Ctrl+Shift+L | Toggle light/dark (toast confirms) | `keyboard.spec.ts` "…+Shift+L toggles the theme…" |
| ↓ ↑ or j k, Home, End, PageUp, PageDown, Enter | Move in a focused grid; Enter opens the row where the grid supports it (Explorer only) | `grid.spec.ts` "↓ j ↑ k move a single tab stop…", "End on a focused row jumps to row 10,000…" |
| ↓ ↑ or j k, Enter | Move between attention items on Command; Enter follows the link | `command.spec.ts` "Enter opens the focused item's deep link; arrows and j/k move between items" |
| ← → (Shift ×10), Home, End, Escape | Move the chart cursor bar by bar | `chart-workstation.spec.ts` "the readout shows the latest bar, and the keyboard moves it bar by bar" |

A chord typed into a field does nothing: `keyboard.spec.ts` "a chord typed into a field does nothing; a late second key does nothing".

### 1.9 Other shell pieces

- **Toasts** (`components/ui/Toast.tsx`): display confirmations and refusals; ✕ dismisses.
- **Inspector sheet** (`components/shell/Inspector.tsx`): the right-hand sheet used by the status bar and the Legend demo.
- **Shared confirm dialog** (`components/ui/ConfirmDialogLayer.tsx`): every dialog-based mutation. States the execution mode (with a DEMO stamp in demo), radio choices with no default when there are several, server consequences, a required reason (8+ characters), a typed phrase where required ("REAL MONEY" in LIVE unless the choice says otherwise), and required caveat checkboxes where the server supplies them.

---

## 2. Pages

### 2.1 Command

#### `/` Command (Overview)

**Question:** Is anything wrong, and what needs me now?
**Reads:** `GET /api/command/attention` every 15 s (`components/command/AttentionPanel.tsx`); `GET /api/trading/risk` (`app/page.tsx`); `GET /api/health` (shared, `components/command/FleetStrip.tsx`); `GET /api/system/incidents` every 30 s (`components/command/IncidentsPanel.tsx`, shared with the timeline); `GET /api/system/kill-switch/history?limit=500` every 30 s (`components/risk/KillSwitchTimeline.tsx`).

| Control | Does | Calls | Mutates | Friction | Afterwards |
|---|---|---|---|---|---|
| Attention item title (link, →) | Opens the item's deep link, if it is an in-app path. Non-app links are shown, not followed. | navigation | No | none | The target page. Incident items open `/system/incidents/<id>` and strategy-review items `/lifecycle/<hash>`; both pages exist since 2026-09-30 (F-1, F-2), and Next prefetches the real page, not a 404. |
| **Acknowledge…** on an incident item | Opens the shared confirm dialog "Acknowledge: <title>" (`components/incidents/AcknowledgeIncident.tsx`, the same component as the Incidents table and the incident page). | `POST /api/system/incidents/{id}/ack` `{reason}` | Yes (audited) | Execution mode stated; reason 8–500 characters; admin only; REAL MONEY typed in LIVE (F-3). When disabled, the reason is printed beside it ("Blocked: admin role required." / "Blocked: execution mode unknown or disconnected.", F-4). | Dialog busy ("Sent. Waiting for the platform to confirm") until the queue no longer lists the item; if no echo, "acknowledged · confirming…". |
| Loss limits: Daily loss, Total drawdown (V-1) | The Risk page's own limit rows (`LimitRowItem`): a bar ending at the limit with 70%/90% ticks, used % (†), state word, day P&L or now, limit, headroom, a chip per row; OK bars in the figure's provenance ink. | — | No | — | — |
| Loss limits: absent line | "NOT REPORTED: Open risk (risk at stop) and the drawdown throttle step are not reported by the API", link to Risk & Exposure. | — | No | — | — |
| Fleet strip | Four cells: worker heartbeat, paper session, news poll (NOT REPORTED), model provider (NOT REPORTED). | — | No | — | — |
| "All health checks (n)" | Disclosure: table of every check. | — | No | — | — |
| Incidents table: incident title (link) | Opens `/system/incidents/<id>`. | navigation | No | — | The incident page. |
| Incidents table: **Acknowledge** | The shared acknowledge control (above). Below 640 px the table stacks one incident per block, so it is on screen (F-10). | `POST /api/system/incidents/{id}/ack` `{reason}` | Yes (audited) | Same as the attention row: mode stated, reason 8–500, admin only, REAL MONEY in LIVE, blocked reason printed beside it | Dialog stays busy until the incident is no longer open; row then says "acknowledged by … at …: <reason>". |
| Kill switch and incidents, last 30 days (V-2) | Owned SVG timeline, UTC: ◐ PAUSE armed, ◆ FLATTEN armed, ○ halt lifted, halted spans as bands, incident first-seen as ticks; hover gives operator, time and reason; "View data" lists every event. States a quiet month, older events outside the window, and a full journal page (500) that may hide older events. | — | No | — | — |

**View states:** each panel has the standard states. Attention empty: "Nothing needs you" with the server's meaning. Incidents empty: "No incidents". The NOT REPORTED line is the **absent** state (dashed amber tag), never zero. The timeline's journal read has its own error state; a failed incidents read draws no ticks and says so.
**Provenance:** source badge on the queue and incidents, and on both streams of the timeline; each attention row shows "from <chip>" (the provenance of its score); each loss-limit row and incident occurrences carry chips; † marks the two utilisations the workstation divides.
**Verified by:** `command.spec.ts` (every test, including "is the top panel and keeps the server's order exactly", "acknowledge posts the reason with the CSRF header…", "acknowledging needs a reason, posts it…", "a refused acknowledgement…", "the loss limits are the Risk page's bars…", "a breached daily loss is BREACHED on Command exactly as on Risk", "in LIVE, the attention row and the Incidents table ask for the same mode statement and REAL MONEY", "with the mode unknown, both acknowledge controls are disabled and say why beside them", "Command prefetches and opens the real incident page: no 404, no console error", "arms, lifts and incidents on one UTC axis…", "a quiet month is said; a failed journal read is an error…", "the fleet strip shows the worker…"); `responsive.spec.ts` "on Command the incidents table stacks, so Acknowledge is on screen"; `error-states.spec.ts` "the overview does not draw a zero balance when the API is down"; `refresh.spec.ts` "the health panel keeps its numbers across a poll tick"; `auth.spec.ts` role-gating tests; sweep (success state); walk "inventory: /" (passes since 2026-09-30).

#### `/market-pulse` Market Pulse

**Question:** What does the traded universe look like right now?
**Reads:** `GET /api/markets/instruments?limit=40`, `GET /api/markets/regimes` (`app/market-pulse/page.tsx`).

| Control | Does | Mutates |
|---|---|---|
| "View data" under the spread chart | Disclosure: the bar values as a table. | No |

Content: a bar chart of typical spread (pips) for the first 20 instruments; "N of M instruments have a computed regime", and a red "No regime is computable" box when none are.
**View states:** standard, per card. **Provenance:** source badge per card; the chart's chip is derived from the spread figures (MIXED if mixed).
**Verified by:** `chart-provenance.spec.ts` "market pulse shows the provenance the API put on each spread" (spread card only; the regime card is **not covered by a test** except by the walk); sweep (error state: `instruments?limit=40` and `regimes` are not mocked in `fixtures.ts`); walk.

#### `/alerts` Alerts

**Question:** What is wrong right now, from live probes and risk limits?
**Reads:** `GET /api/health` every 15 s, `GET /api/trading/risk` (`app/alerts/page.tsx`).
**Controls:** none beyond the shell.
Content: "Failing checks" (each non-ok health check as a caveat line, critical or advisory) or "No failing checks" with the overall status badge; "Risk breaches" (each breach string) or "No limit is breached against limit set …".
**View states:** standard. **Provenance:** health has no source badge (it is a probe); the risk card shows none either (only the limit-set name).
**Verified by:** sweep (health panel in success, risk card in **error** state); walk. The breach list and the failing-check rendering are **not covered by a test**.

### 2.2 Trading

#### `/trading/portfolio` Portfolio (Fleet & Positions)

**Question:** What is the book in the current mode worth, and what is open?
**Reads:** `GET /api/trading/portfolio`, `GET /api/trading/positions` (`app/trading/portfolio/page.tsx`).

| Control | Does | Mutates |
|---|---|---|
| Equity chart (keyboard: arrows read each point) | Line chart of the equity curve with gaps hatched. | No |
| "View data" | Disclosure: equity points as a table. | No |
| "est" beside a mark price | Popover explaining an estimated value. | No |
| **†** beside a distance-to-stop meter (V-5) | Tooltip (hover, keyboard focus, tap) with the formula; the same formula is printed under the table. | No |

Tiles: Balance, Equity, Realised P&L, Unrealised P&L, Max drawdown. Table: open positions (source, instrument, strategy, side, size, entry, mark, stop, **to stop**, unrealised, opened). "To stop" is the API's `distance_to_stop_pct` beside an owned-SVG meter placing the mark between the stop (0) and the entry (1): position = (mark − stop) ÷ (entry − stop), computed in the workstation and marked †; with a price missing, the reason ("no stop reported") instead of a meter (`components/risk/StopMeter.tsx`).
**View states:** standard; positions empty: "No open positions". **Provenance:** source badges; chips on tiles; chip per position row; the equity chart has its own chip.
**Verified by:** `trust.spec.ts` "W-11/W-12: a missing point is a hatched gap; arrows read values; View data lists them"; `workstation-visuals.spec.ts` "each open position has a meter placed from entry, mark and stop, marked †"; sweep (error state); walk. The tiles are **not covered by a test** except by the walk.

#### `/trading/candidates` Candidates (Strategy Lifecycle)

**Question:** What should be promoted?
**Reads:** `GET /api/trading/candidates`; `GET /api/research/validation` for the Ladder column; on opening Promote, `GET /api/trading/candidates/{id}/promote/preflight` (`app/trading/candidates/page.tsx`).

| Control | Does | Calls | Mutates | Friction | Afterwards |
|---|---|---|---|---|---|
| Grid filter, sort headers, Columns (show/hide/pin, saved sets, reset), Export CSV | Standard DataGrid (section 3) | none | No | — | — |
| "why" on an ineligible row | Popover listing the blocking reasons. | none | No | — | — |
| **Promote** (eligible row) | Opens "Promote <name>": one radio choice per target the server offers (e.g. TO PAPER, TO SHADOW), the server's consequences, one required checkbox per realism caveat. | `POST /api/trading/candidates/{id}/promote` `{target_lifecycle, reason, acknowledge_caveats, acknowledged_caveats}` | Yes | Choice (no default when several), reason 8+, **every caveat ticked**, REAL MONEY in LIVE; `can_promote` role | Dialog closes and the list reloads. Errors are shown in the dialog. |
| **Promote** (ineligible or blocked) | Does nothing; stays focusable and describes why (aria-disabled). | — | — | — | — |
| Ladder column (V-4) | Each candidate's rung meter from its validation row (joined by strategy id): segments 0–6 filled to the rung reached, the stopping rung marked ✕, the binding constraint named; "No validation row for this strategy" or "Validation reports could not be read" otherwise. Not in the CSV (a join, not a figure of this payload). | — | No | — | — |

Below 640 px the grid is replaced by one card per candidate (name, id, eligibility and "why", the six
figures each with its chip, the ladder, Promote), so every figure and Promote are on screen (F-10).

Also on the page: "Why these are not promotable" (every blocking reason), and notes when your role or an unknown mode blocks promotion.
**View states:** standard; empty "No candidates". **Provenance:** source badge; a chip on every figure in the grid; CSV carries a provenance column per figure.
**Verified by:** `promote.spec.ts` (8 promote tests); `kill-switch.spec.ts` "the same dialog component is used to promote a candidate", "an ineligible candidate cannot be promoted at all"; `provenance.spec.ts` "every candidate figure carries a chip", "a chip never renders without a number beside it"; `grid.spec.ts` "candidates keep a chip per figure and explain ineligibility in a popover"; `auth.spec.ts` "a viewer sees promotion disabled, with the reason"; `mode-frame.spec.ts` "disables promotion, even for an eligible candidate", "friction follows the act in LIVE…"; `trust.spec.ts` W-05; `error-states.spec.ts` "the error names the failure and the correlation id"; `workstation-visuals.spec.ts` "Candidates: each candidate's meter…", "Candidates: a failed validation read is said in the column…"; `responsive.spec.ts` "candidates are cards: every figure with its chip, and Promote, on screen"; sweep (success); walk (Promote opened and cancelled; ineligible Promote opened nothing).

#### `/trading/execution` Execution (Journal · Trades)

**Question:** What did we trade, from which source?
**Reads:** `GET /api/trading/trades?limit=200[&provenance=…]` every 30 s, `GET /api/markets/instruments` for price decimals (`app/trading/execution/page.tsx`).

| Control | Does | Mutates |
|---|---|---|
| **Filter by provenance** (select: All sources (mixed), backtest … broker_live) | Re-reads trades for that provenance; kept in the URL (`?provenance=paper`). | No |
| DataGrid (filter, sort, Columns incl. hidden Entry, Exit price, Gross, Costs, Opened; Export CSV; j/k) | Section 3 | No |
| MIXED chip on the R-multiple chart | Popover with counts per provenance. | No |
| "View data" under the chart | Disclosure: histogram bins, ▲ on profit bins and ▼ on loss bins. | No |

The R-multiple bins wholly above zero are drawn in the P&L-up token, wholly below in the P&L-down
token, and a bin spanning zero in the source's ink (F-11).

Also: "All N trades the platform holds" or "returned X of Y"; the R-multiple distribution drawn over the returned rows only, titled "first X of Y" when truncated.
**View states:** standard; the filter stays usable in every state. **Provenance:** Source column with a chip per row; mixed-result caveat from the API; distribution chip derived from plotted values only.
**Verified by:** `provenance.spec.ts` (4 execution tests); `chart-provenance.spec.ts` (4 distribution tests, and "profit bins take the profit token with ▲, loss bins the loss token with ▼"); `grid.spec.ts` (virtualisation, sort, units, filter, columns, keyboard, live updates, CSV); `time-labels.spec.ts`; `url-state.spec.ts` "a linked Execution filter is applied on load", "changing the filter writes the URL…"; `error-states.spec.ts` (4 tests); `responsive.spec.ts`; sweep (success); walk.

#### `/trading/risk` and `/trading/exposure` Risk & Exposure

One screen (`components/risk/RiskExposureScreen.tsx`); `/trading/exposure` opens scrolled to the exposure matrix.

**Question:** How close are we to any limit?
**Reads:** `GET /api/trading/risk`, `GET /api/trading/exposure`, `GET /api/system/kill-switch`, `GET /api/system/kill-switch/history?limit=500` and `GET /api/system/incidents` (the timeline); on opening the drawer `GET /api/trading/positions`; on lifting the halt `GET /api/trading/preflight/kill-switch-disarm`.

| Control | Does | Calls | Mutates | Friction | Afterwards |
|---|---|---|---|---|---|
| **Open positions (n)** (page header) | Opens the positions sheet (source, instrument, side, size, entry, mark, stop, **to stop %** with its distance-to-stop meter and †, unrealised, strategy, opened). Escape or ✕ ("Close Open positions", F-5) closes. | `GET /api/trading/positions` | No | — | — |
| **Arm kill switch** / **Change halt level** | The "Halt trading" dialog (1.6). | `POST /api/system/kill-switch/arm` | Yes | PAUSE: reason; FLATTEN: reason + FLATTEN | Busy until echoed; then panel and banner show ARMED; else "confirming…". |
| **Lift the halt (resume trading)** (only when armed; was "Disarm and re-arm trading", F-13) | The "Lift the halt and resume trading" dialog; confirm button "Lift the halt". | `POST /api/system/kill-switch/disarm` | Yes | reason + LIFT HALT; server consequences | As above. |
| Kill switch and incidents, last 30 days (V-2) | The same timeline as on Command (arms, lifts, halted spans, incident ticks, hover and View data). | — | No | — | — |
| Retry (in a family whose source failed) | Re-reads that source. | GET | No | — | — |
| Stale badge popover / Retry | Why the numbers may be out of date; re-read. | GET | No | — | — |
| Exposure matrix (scrolls sideways, keyboard-focusable) | Heat cells per instrument, currency, strategy bucket. | — | No | — | — |
| "View data" under the matrix | Disclosure: every bucket's exposure, limit, utilisation. | — | No | — | — |
| "Open Data Quality", "Risk & Exposure" links in not-reported rows | Navigation. | — | No | — | — |

Content: the limit board (a "Closest to a limit" headline, then families: exposure, daily/weekly loss, drawdown, account, margin, correlation, data quality; each row a bar ending at the limit with ticks at 70% and 90%, OK/NEAR/CRITICAL/BREACHED/NOT REPORTED, now, limit, headroom); the gateway panel (new risk and closing PERMITTED/BLOCKED, limit set, breaches, five tiles); the drawdown throttle meter (five steps, NOT REPORTED, no step marked); "Correlated open risk: NOT REPORTED".
**View states:** each family states its own loading, error (with Retry), empty and stale; the headline says "Incomplete" when a source is missing.
**Provenance:** chip per family and on the headline; the dagger (†) marks utilisations computed in the workstation from two API figures, explained at the foot.
**Verified by:** `risk-exposure.spec.ts` (every test, including "the positions drawer draws a distance-to-stop meter per row, marked † with its formula", "a position with no stop has no meter, and says why", "the drawer's close button says what it closes", "the kill-switch timeline is on Risk & Exposure too"); `kill-switch.spec.ts` "disarming requires typing the confirm phrase" (LIFT HALT; RE-ARM no longer accepted); `trust.spec.ts` W-07, W-10 "a LIVE exposure bar is magenta…"; `chart-provenance.spec.ts` "exposure is labelled from its rows…"; `kill-switch.spec.ts`; `error-states.spec.ts` (risk case); sweep (error state for the board: risk and exposure are not mocked in `fixtures.ts`; kill-switch panel in success); walk (both routes; drawer, arm and disarm opened and cancelled).

#### `/lifecycle/<hash>` Strategy (new, 2026-09-30)

The deep link `routers/command.py` emits for strategy-review items. Code:
`app/lifecycle/[hash]/page.tsx`, `LifecycleEntity.tsx`. Belongs to Strategy Lifecycle in the header.

**Reads:** `GET /api/trading/lifecycle/strategies/{hash}` (the lifecycle API addresses a strategy by
content hash and names its id); `GET /api/trading/lifecycle/strategies/{hash}/evaluation` only when
the status says it has been evaluated (the route answers 404 by design otherwise);
`GET /api/research/strategies` and `GET /api/research/validation` (the research API addresses a
document by id, not hash, so the document is found by the id the lifecycle store gives, or, when the
store has no record, by the one document whose 12-character hash and the link's are prefixes of one
another). **Question:** What is this strategy, what did the ladder conclude, and where does it stand?

| Control | Does | Mutates |
|---|---|---|
| "← Command" | Navigation. | No |
| "Strategies list", "Parameter Lab" | Navigation (`/research/strategies`; `/research/parameter-lab?strategy=<id>`). | No |
| "Promote from Candidates" | `/trading/candidates?row=<id>`: promotion stays on Candidates, which has the one Promote control; this page has none. Demotion is an automated rule's act; no screen and no route demotes (stated). | No |

Content: the document header (name, family, hypothesis, id, content hash, author, timeframes,
universe size, rules, parameters, complexity with chips, schema, parents); a red notice when the
registered document's hash no longer agrees with the link's ("edited since the lifecycle record");
the latest validation summary (verdict, rung meter, binding constraint, gate set, dataset, report
version) with the plain statement that the API sends no per-gate statuses and keys the report by id
without its content hash; the lifecycle status (state, band, degraded, since, last evaluated, health
only when evaluated, else NOT EVALUATED, score, confidence, transitions, halts, missing rule
registrations) and the latest evaluation's summary and rule outcomes.
**View states:** each of the three reads has its own standard states; no match says so and links to
the Strategies list; two matches are not guessed between.
**Provenance:** source badge per read; chips on every figure; the meter's chip is the report's.
**Verified by:** `lifecycle.spec.ts` (8 tests: Command link resolves, full page, edited-document flag, never evaluated, no lifecycle record, no match, ambiguous match, axe); walk "inventory: /lifecycle/abc123def456" and "backend deep links reach a page".

### 2.3 Research

#### `/research` Research Lab (Lab)

**Question:** What is registered and where does each strategy stand?
**Reads:** `GET /api/research/strategies`, `GET /api/research/validation` (`app/research/page.tsx`).
**Controls:** each strategy tile's name is a link to `/research/strategies` (the list, not that strategy).
Content: a tile per strategy (family, name, hypothesis); "Validation standing" list (id, verdict badge, detail).
**View states:** standard per card. **Provenance:** source badge per card; verdict badges toned from the verdict (REJECT is never green; an unknown verdict reads "UNRECOGNISED: …").
**Verified by:** sweep (error state); walk. **Success state not covered by any other test.** (`keyboard.spec.ts` uses this page only as a starting point.)

#### `/research/hypotheses` Hypotheses

**Reads:** `GET /api/research/hypotheses` (`app/research/hypotheses/page.tsx`). **Question:** What does each strategy claim, and how much evidence stands behind it?
Table (no sort, filter or CSV): strategy, family, hypothesis, structure keywords, supporting experiments (with chip). **Controls:** none.
**View states:** standard; empty "No hypotheses". **Verified by:** sweep (error state); walk. Success state **not covered by any other test.**

#### `/research/experiments` Experiments

**Reads:** `GET /api/research/experiments`. **Question:** What has ever been tried, including failures?
Table: experiment, strategy, actor (kind), outcome, dataset, created (UTC). **Controls:** none.
Empty text tells you to read the source badge before concluding nothing was tried.
**Verified by:** `responsive.spec.ts` "the page header lists the section's views" (header only); sweep (error state); walk. Table **not covered by any other test.**

#### `/research/strategies` Strategies

**Reads:** `GET /api/research/strategies`. **Question:** Which strategy documents are registered, exactly?
Table: name, id, family, timeframes, universe size, rules, parameters, complexity, content hash. **Controls:** none.
**Verified by:** `mode-banner.spec.ts` "is present on every section" (banner only); `responsive.spec.ts` "no horizontal page scroll on /research/strategies"; `keyboard.spec.ts` ⇧K test (start page); sweep (error state); walk. Table **not covered by any other test.**

#### `/research/parameter-lab` Parameter Lab

**Reads:** `GET /api/research/strategies`, `GET /api/research/parameter-lab/{id}` (`app/research/parameter-lab/page.tsx`). **Question:** How big is the search a strategy implies?

| Control | Does | Mutates |
|---|---|---|
| **Strategy** (select) | Chooses the strategy; kept in the URL (`?strategy=`). | No |

Content: "Search space" tile, the deflation warning, declared domains (default, min, max, step, description).
**View states:** standard, plus "No parameter space to show" when the strategy list failed, and "No strategy is registered" when it is empty (never "Loading" for ever).
**Verified by:** `url-state.spec.ts` "the Parameter Lab's selected strategy is in the URL"; `trust.spec.ts` "a failed strategy list says there is nothing to show…", "an empty strategy list is an empty answer"; sweep (error state); walk.

#### `/research/validation` Validation

**Reads:** `GET /api/research/validation`. **Question:** What did the promotion ladder decide, and what was binding?
Table: strategy, verdict badge, **Ladder** (V-4: a rung meter, one segment per rung the report records, filled to the rung reached, the stopping rung marked ✕, "N of M rungs passed; stopped at rung N", a chip, and the binding constraint named; a strategy with no report is NOT EVALUATED with no segments, never rung zero; `components/research/RungMeter.tsx`), rungs passed, rungs in report (renamed from "rungs run": the ladder records every rung, reached or not), gate set, binding constraint, detail. Per-rung names and per-gate statuses are not in the API's summary (backend ask), so segments are labelled by index. **Controls:** none.
**Verified by:** `trust.spec.ts` "REJECT is never drawn with the OK badge", "a ListPage figure column's header is right-aligned"; `workstation-visuals.spec.ts` "Validation: filled to the rung reached, the stopping rung marked, the binding gate named"; sweep (error state); walk.

#### `/research/datasets` Datasets

**Reads:** `GET /api/research/datasets`. **Question:** Which exact data versions exist?
Table: short id, full id, description. **Controls:** none.
**Verified by:** sweep (error state); walk. **Not covered by any other test.**

### 2.4 Markets and Intelligence

#### `/markets` Explorer

**Reads:** `GET /api/markets/instruments` (`app/markets/page.tsx`). **Question:** What can we trade, and at what assumed cost?

| Control | Does | Mutates |
|---|---|---|
| Symbol (link) | Opens `/markets/<symbol>`. | No |
| Enter on a focused row | Same. | No |
| DataGrid (filter, sort, Columns incl. hidden pip size, contract size, size step; Export CSV) | Section 3. `?row=EURUSD` selects a row; an unknown symbol says so. | No |

**Verified by:** `chart-workstation.spec.ts` "the instrument list links each symbol to its chart"; `grid.spec.ts` "instruments: ?row selects the instrument; an unknown one is said so"; `mode-banner.spec.ts` (banner); sweep (success); walk.

#### `/markets/<symbol>` Chart workstation (e.g. `/markets/EURUSD`)

**Reads:** `GET /api/markets/bars/{symbol}?timeframe=&limit=500` and `GET /api/markets/overlays/{symbol}?timeframe=&limit=500`, each every 60 s; `GET /api/markets/instruments` for pip size (`app/markets/[symbol]/ChartWorkstation.tsx`).
**Question:** What is this market doing, and what did our strategies do on it?

| Control | Does | Mutates |
|---|---|---|
| All instruments (link) | Back to Explorer. | No |
| **Timeframe** H1 / H4 / D1 | Re-reads bars and overlays; kept in the URL (`?tf=`). Fixed list: the API does not say which timeframes exist. | No |
| **Sources (n)** | Popover: bar source and dataset, overlay source, every item-level source with counts, regime classifier fingerprint, chart library credit. | No |
| **View data / Hide data** | Splits the screen; lower half has tabs Bars, Signals, Fills, Levels, Regimes, Series, each a DataGrid with Columns and Export CSV. Split is draggable. | No |
| Layer toggles Regimes, Signals, Fills, Levels (with counts, "n/a" if unavailable) | Show/hide that layer. | No |
| Series-group toggles | Show/hide an indicator group (price pane, state pane, own pane). | No |
| **Key** | Popover explaining every marker, line and band. | No |
| "N sections not available · received, not drawn · outside the bar window" | Disclosure: which overlay sections the API marked unavailable and why; events and headlines received but not drawn. | No |
| Chart (arrows, Shift, Home, End, Escape; pointer) | Moves the crosshair; the readout below shows OHLC, volume and series values. | No |
| Retry (overlays error) | Re-reads overlays. | No |

**View states:** loading ("reading bars…"), **absent** ("No market data is mounted", when the API says `market_data_not_mounted`), empty ("No bars for …"), error, stale (badge in the header), overlays loading/error shown separately under the legend, dataset mismatch badge when overlays came from another dataset version.
**Provenance:** "Bars SOURCE <kind>", "Journal <chip>" derived from signals, fills and levels; dataset version chip, cut in the middle so the timeframe survives (`dsv_eurusd_h1_…abcdef`), focusable, showing the whole id on hover and on keyboard focus (F-8); the mismatch badge shows the whole id.
**Verified by:** `chart-workstation.spec.ts` (every test, including "the id is cut in the middle, keeping the timeframe, and shown whole on hover and on focus"); walk (all six data tabs, Sources, Key and six Columns popovers opened and closed).

#### `/markets/regimes` Regimes

**Reads:** `GET /api/markets/regimes`. **Question:** What regime is each instrument in, where it can be known?
Above the table, the regime grid (V-8, `components/markets/RegimeGrid.tsx`): one row per instrument, one heat cell per measure (volatility, direction, persistence, liquidity, stress), the value printed in each; the shade is only the value's position in the classifier's declared order (`marketstate/regime.py`), lighter first; unknown (unavailable, null or `unknown`) is **hatched**, never shaded as a real state; a value outside the classifier's vocabulary is hatched and marked "?". The cells reuse the exposure matrix's `xheat` cells. Each cell's hover gives the instrument, measure and value (or the API's reason it is unknown); the table repeats every value.
Table: instrument, CLASSIFIED/UNKNOWN, volatility, direction, liquidity, stress, persistence (now shown), detail. **Controls:** none (the grid scrolls sideways and is keyboard-focusable).
**Verified by:** `workstation-visuals.spec.ts` "instruments × measures, values printed, unknown and unrecognised hatched"; sweep (error state); walk. (The walk's fixture sends liquidity "liquid", which is not in `LiquidityAxis`; the grid marks it unrecognised, as intended.)

#### `/markets/correlations` Correlations

**Reads:** `GET /api/markets/correlations`. **Question:** Which instruments move together?
Content: a correlation heatmap (orange positive, blue negative, paler nearer zero), a legend strip, and "View data" (disclosure: the matrix). **Controls:** View data.
**Provenance:** the chip shows the envelope's source (the API supplies no provenance or window as-of for the matrix; a TODO in the page says so).
**Verified by:** `chart-provenance.spec.ts` "correlations show the API's SourceNote, not a hard-coded backtest"; `trust.spec.ts` "the correlation heatmap is blue/orange, and its values are readable as text"; sweep (error state); walk.

#### `/markets/data-quality` Data Quality (Market data)

**Reads:** `GET /api/markets/data-quality`. **Question:** Can we trust each instrument's data?
Table: instrument, quality badge (VALIDATED, WARNINGS, DEFECTIVE, PENDING, UNKNOWN), bars, gaps, stale runs, detail. **Controls:** none; rows do not link to the instrument or dataset.
**Verified by:** `trust.spec.ts` "PENDING data quality is not OK"; sweep (error state); walk.

#### `/intelligence/agents` Agents

**Reads:** `GET /api/intelligence/agents`. **Question:** What may each agent role do?
Table: role, purpose, capabilities, "Can execute" (always NO, hard-coded by design: no execution capability exists). **Controls:** none.
**Verified by:** `mode-banner.spec.ts` (banner only); sweep (error state); walk. Table **not covered by any other test.**

#### `/intelligence/runs` Agent runs

**Reads:** `GET /api/intelligence/runs`. **Question:** What have the agents run, and how did it end?
Table: run, status (untyped rows from the API). Empty text: "No agent runs recorded … no run recorded is not the same as every run succeeding". **Controls:** none.
**Verified by:** sweep (error state); walk. **Not covered by any other test.**

#### `/intelligence/research-memory` Research Memory

**Reads:** `GET /api/intelligence/research-memory`. **Question:** Have we tried this already?
Content: Available/Unavailable box, "Structures recorded" and "Rediscovery rate" tiles. **Controls:** none (the backend's `POST /api/intelligence/research-memory/notes` has no UI).
**Verified by:** sweep (error state); walk. **Not covered by any other test.**

### 2.5 System

#### `/system/services` Services

**Reads:** `GET /api/system/services`. **Question:** Is every service reachable right now?
Table: service, HEALTHY/NOT HEALTHY, kind (LIVE/SEED/ABSENT), probe latency, detail. **Controls:** none.
**Verified by:** `responsive.spec.ts` "no horizontal page scroll on /system/services"; `keyboard.spec.ts` "g then s goes to /system/services"; sweep (error state); walk. Table **not covered by any other test.**

#### `/system/workers` Workers

**Reads:** `GET /api/system/workers`. **Question:** Is each worker alive?
Table: worker, RUNNING/STALE/NEVER STARTED/STOPPED, heartbeat age (chip; "no data" when never beaten), detail. **Controls:** none.
**Verified by:** sweep (error state); walk. **Not covered by any other test.**

#### `/system/broker-health` Broker Health

**Reads:** `GET /api/system/broker-health`. **Question:** Would the mode guard let an order reach a venue?
Content: venue host ("not configured"), guard verdict ALLOWED/REFUSED, detail; controls table (each mode-guard control, YES/NO); reasons. **Controls:** none.
**Verified by:** sweep (error state); walk. **Not covered by any other test.**

#### `/system/data-health` Data Health

**Reads:** `GET /api/system/data-health`. **Question:** Which data sources are real, seeded or absent?
Table: source, kind badge, latency, detail. **Controls:** none.
**Verified by:** sweep (error state); walk. **Not covered by any other test.**

#### `/system/logs` Audit log

**Reads:** `GET /api/intelligence/audit/integrity`, `GET /api/intelligence/audit?limit=200` (`app/system/logs/page.tsx`). **Question:** Who did what, including refusals, and is the chain intact?

| Control | Does | Mutates |
|---|---|---|
| DataGrid (filter, sort, Columns incl. hidden entry hash, Export CSV) | Section 3. `?row=<sequence>` selects an entry (the palette's "Open audit entry #N"). | No |

Content: "Audit chain intact · VERIFIED" or "BROKEN · TAMPERED" banner; rows: #, when (UTC, 176 px, the whole stamp), action, actor (role), ALLOWED/REFUSED (124 px), mode, target, reason, correlation id; every truncatable text cell carries its full value as a title (F-9).
**Verified by:** `grid.spec.ts` "audit log: rows by sequence, sortable, and ?row selects an entry", "audit log at 1440 px: the time and the outcome badge fit their columns"; sweep (success); walk. The BROKEN integrity state is **not covered by a test**.

#### `/system/settings` Settings

**Reads:** `GET /api/system/settings`. **Question:** What is this deployment configured to do?
Content (read-only by design): execution mode, "Live execution compiled in" YES/NO, realism assumptions table (each code with what it means, for the codes `api/settings.py` defines; an unknown code says it is not described), risk limits table (key, value with its unit, and a Unit column: "% of equity" for `_pct`, seconds, minutes, "× the instrument's typical spread", "correlation coefficient, 0 to 1", "health score, 0 to 1", on/off, "not set (this limit set predates the rule)" for null, "unit not stated by the API" for a key it does not recognise; F-15), security list (cookie, origins, session TTL, build). **Controls:** none.
**Verified by:** `mode-banner.spec.ts` (banner only); `workstation-visuals.spec.ts` "every limit shows its unit; realism codes say what they mean"; sweep (error state); walk.

#### `/system/incidents/<id>` Incident (new, 2026-09-30)

The deep link the incident read model and the attention queue emit (`routers/incidents.py`
`deep_link`); reached from a Command attention row or an Incidents-table title. Code:
`app/system/incidents/[id]/page.tsx`, `IncidentDetail.tsx`. Belongs to System & Incidents in the header.

**Reads:** `GET /api/system/incidents/{id}` every 15 s. **Question:** What is this incident, what has happened on it, and what can I do?

| Control | Does | Calls | Mutates | Friction | Afterwards |
|---|---|---|---|---|---|
| "← Command", "Back to Command" | Navigation to `/`. | — | No | — | — |
| "Open the source: <screen>" | The screen that shows the incident's source, from the event the platform named (workers, data quality, broker health, risk, candidates, trades, services; kill-switch incidents go to Risk & Exposure). An event no screen shows says so instead of linking. | — | No | — | — |
| **Acknowledge** (open incidents only) | The shared acknowledge control: confirm dialog "Acknowledge: <title>", mode stated. | `POST /api/system/incidents/{id}/ack` `{reason}` | Yes (audited) | Reason 8–500; admin only; REAL MONEY in LIVE; blocked reason printed beside it | Dialog busy until the page's re-read shows the incident no longer open; else "acknowledged · confirming…". |
| Note text + **Add note** | Appends a note to the timeline; states the execution mode it is recorded in. | `POST /api/system/incidents/{id}/note` `{text}` (1–2000 characters, trimmed) | Yes (audited; never changes the status) | Admin only (the reason is printed when blocked); no dialog | "Sent. Waiting for the platform to show it on the timeline"; the text clears once the timeline carries the note; a refusal keeps the text and shows the API's message. |

Content: severity, title, status; incident id, event, dedupe key, source, first seen, last seen,
occurrences (with chip), acknowledged by/at, resolved at; the timeline as a vertical list, oldest
first: ● OCCURRED (with severity), ✓ ACKNOWLEDGED, ✎ NOTE, ◇ RESOLVED, each with time (UTC), actor,
text and correlation id.
**View states:** standard; an unknown id is the API's `incident_not_found` error with its correlation id (the page itself is 200, never a 404).
**Provenance:** source badge; occurrences chip.
**Verified by:** `incident-detail.spec.ts` (10 tests: header and timeline order, source link, no-screen source, 404 as an error, acknowledge with CSRF and echo, LIVE phrase, note posted and echoed, refused note, role-blocked, axe dark and light); `command.spec.ts` "Command prefetches and opens the real incident page…", "an incident title in the Incidents table opens its page"; sweep; walk "inventory: /system/incidents/inc-1" (Acknowledge opened and cancelled).

#### `/system/legend` Legend

**Reads:** nothing from the platform. **Question:** What does each colour, shape and frame mean?

| Control | Does | Mutates |
|---|---|---|
| Tabs Meanings / Tokens / Controls (URL `?tab=`) | Switch section. | No |
| MIXED chip example | Popover with counts. | No |
| Controls tab: Buttons, Tooltip, Popover, Toast, **Inspector**, Segmented, Select, Menu, Split pane | Live demonstrations of the owned components. | No |

Closing a popover no longer swallows the next click: focus returns to the popover's trigger only if the operator has not moved on (F-6, `components/ui/PopoverLayer.tsx` `returnFocusTarget`).
**Verified by:** `keyboard.spec.ts` "a Select opened while a closed popover is still animating out stays open, with focus in it", "Escape on a popover still returns focus to its trigger when nothing else was touched"; `url-state.spec.ts` "the Legend's open tab is in the URL"; `visual.spec.ts` (chips, mode frames, view states, P&L CVD, token sheet); `a11y.spec.ts` "legend controls and inspector"; `csp.spec.ts` "dialogs, popovers, tooltips and the split pane trigger none"; `trust.spec.ts` W-13, "Numbers: sign after rounding"; walk (every popover, menu, select and the inspector opened and closed).

### 2.6 Sign in

#### `/login` Sign in

**Reads:** `GET /api/system/execution-mode` (banner only). **Question:** Who are you?

| Control | Does | Calls | Mutates |
|---|---|---|---|
| Username, Password | Never pre-filled; password autofill off; password cleared after every attempt. | — | — |
| **Sign in** (disabled until both fields have text) | Posts once as JSON; returns to `?next=` if it is an in-app path, else `/`. | `POST /api/auth/login` | Creates a session |

Errors: the API's message and code; an unreachable API says "connection failure, not a wrong password". No rail, status bar, stream, palette or shortcuts on this page.
**Verified by:** `auth.spec.ts` "sign in" (5 tests) and "a 401 sends the operator to sign in, keeping the return path"; sweep; walk (not blank, no errors; shortcuts not applicable).

---

## 3. Controls shared by several pages

**DataGrid** (`components/grid/DataGrid.tsx`; Candidates, Execution, Explorer, Audit log, the chart's six data tabs): filter box with "N of M rows match"; click a header to sort (missing values last both ways); **Columns** popover (show/hide, pin, save the layout as a named set, apply or delete a set, reset); **Export CSV** (every figure as value, unit and provenance columns, missing values empty, source and export time on every row, formula injection guarded); a single tab stop with ↓ ↑ j k Home End PageUp PageDown; Enter where the page supports it; virtualised to 10,000+ rows; `?row=` selection. Verified by `grid.spec.ts` (every test).

**Plain tables** (`components/ListPage.tsx`; 15 pages: Hypotheses, Experiments, Strategies, Validation, Datasets, Regimes, Data Quality, Agents, Agent runs, Services, Workers, Data Health, and the tables inside Settings, Broker Health, Portfolio): no sort, filter or export; "Showing N of M" below.

**Figure popovers** (`components/FigureValue.tsx`, `components/ProvenanceChip.tsx`, `components/CaveatPopover.tsx`, `components/ui/StaleBadge.tsx`): "est", caveat flag, MIXED counts, "why", stale detail. All open by click, keyboard or tap and close with Escape. Verified by `keyboard.spec.ts` "a caveat popover opens from the keyboard and closes back to its badge", "tooltips" tests, and the walk.

---

## 4. Findings

From the walk run at 17:47:39Z to 17:51:52Z, the probes described, the screenshots and the code.
No page threw, no page rendered blank, no console error appeared on any route except F-1, and no
control that should open something opened nothing. Still open after 2026-09-30: F-7 (plain tables),
F-12 (candidate columns; a backend ask), F-14 (the LIVE banner's link) and F-16 (list links).

| # | Severity | Route · control | Finding | Evidence (and, where fixed, the fix) |
|---|---|---|---|---|
| F-1 | **High** | `/` · attention item title for an incident | The link goes to `/system/incidents/<id>` (the backend's own deep link, `routers/incidents.py`), and **that page does not exist: 404**. Next also prefetches it, so every load of Command with an incident in the queue logs two 404 console errors. The operator's most direct path from "needs attention" to the incident is broken. | Walk "inventory: /" **failed**: `404 GET /system/incidents/inc-1?_rsc=…` ×2 and two console errors; "backend deep links reach a page" **failed** (status 404, heading "404"). **Fixed 2026-09-30.** `/system/incidents/<id>` exists (§2.5); Command's link and its prefetch now reach it (200, heading "Incident"). Tests: `command.spec.ts` "Command prefetches and opens the real incident page: no 404, no console error"; walk "backend deep links reach a page" and "inventory: /" pass. |
| F-2 | **High** | `/` · attention item for a strategy review | `routers/command.py` emits `/lifecycle/<content_hash>` for strategy lifecycle items; no such page, 404. (`/risk`, `/system`, `/journal` are redirected correctly.) | "backend deep links reach a page" **failed** for `/lifecycle/abc123def456`; the three redirects returned 200. **Fixed 2026-09-30.** `/lifecycle/<hash>` exists (§2.2), a real entity page over the lifecycle and research APIs. Tests: `lifecycle.spec.ts` (8); walk "backend deep links reach a page" passes for `/lifecycle/abc123def456` and `/lifecycle/ichimo0123456789abcdef`. |
| F-3 | Medium | `/` · attention row "Acknowledge…" | The inline acknowledge form bypasses the shared confirm dialog: it does not state the execution mode and, in LIVE, does not ask for REAL MONEY, while acknowledging the same incident from the Incidents table does. Two friction levels for one act. | Code: `components/command/AttentionPanel.tsx` `AckForm` versus `components/command/IncidentsPanel.tsx` `ConfirmDialog`. Not exercised in LIVE in a browser. **Fixed 2026-09-30.** One component, `components/incidents/AcknowledgeIncident.tsx`, used by the attention row, the Incidents table and the incident page: the shared confirm dialog, mode stated, REAL MONEY in LIVE, reason 8–500. The inline form is deleted. Test: `command.spec.ts` "in LIVE, the attention row and the Incidents table ask for the same mode statement and REAL MONEY". |
| F-4 | Low | `/` · disabled "Acknowledge…" (attention) and "Acknowledge" (incidents) | The reason a disabled acknowledge cannot be pressed is in a hover `title` only (attention), or absent (incidents table when the mode is unknown). The plan forbids tooltip-only information. | Code: `AttentionPanel.tsx` `title={ackBlocked}`; `IncidentsPanel.tsx` `disabled={!mutationsAllowed …}` with no reason text. **Fixed 2026-09-30.** A disabled acknowledge prints why beside it ("Blocked: admin role required." / "Blocked: execution mode unknown or disconnected."), linked by `aria-describedby`. Tests: `command.spec.ts` "a role that cannot acknowledge…", "with the mode unknown, both acknowledge controls are disabled and say why beside them". |
| F-5 | Low | `/trading/risk` · Open positions sheet | The sheet's close button is announced as "Close inspector" on every sheet, including the positions drawer. | Code: `components/ui/SheetLayer.tsx` hard-codes `aria-label="Close inspector"`. **Fixed 2026-09-30.** `SheetLayer` names what it closes ("Close Open positions", "Close Platform health"). Test: `risk-exposure.spec.ts` "the drawer's close button says what it closes". |
| F-6 | Low | `/system/legend` (Controls) · Select | A click made in the first ~150 ms after a popover is closed with Escape is swallowed: the Select did not open at 0 ms, did at 150 ms and 400 ms. Unlikely by hand, but the same focus-return timing applies to every popover. | A temporary probe spec (deleted after use) printed `wait 0: expanded false`, `wait 150: expanded true`. **Fixed 2026-09-30.** Cause: Base UI returns focus when a popup unmounts, at the END of the exit transition, and our explicit `finalFocus={() => anchor}` bypassed Base UI's own "focus moved elsewhere" guard, so a control opened during the exit had focus pulled back and closed. Fix: `PopoverLayer` returns focus only if focus is still in the popup, on its trigger or on the body (`returnFocusTarget`). No delay added. A probe before the fix (17 fresh loads, clicks 0–250 ms after Escape): 4 Selects closed, 4 more with focus stolen; after: 17 of 17 open with focus in the list. Tests: `keyboard.spec.ts` "a Select opened while a closed popover is still animating out stays open, with focus in it" (transitions slowed tenfold so the click always lands in the exit), "Escape on a popover still returns focus to its trigger…". |
| F-7 | Low | every page with a ListPage | 15 pages use plain tables with no sort, filter or CSV, while 4 use the DataGrid. Workers, Services, Experiments and Validation are where sorting and export matter. | Code: `components/ListPage.tsx`; screenshots. |
| F-8 | Low | `/markets/<symbol>` · dataset chip | The dataset id is cut to 12 characters: `dsv_eurusd_h1_0007abcdef` shows as "dataset dsv_eurusd_h", which drops the timeframe. | Screenshot `markets_EURUSD-1440x900.png`; code `slice(0, 12)`. **Fixed 2026-09-30.** Middle truncation (`dsv_eurusd_h1_…abcdef`); the chip is focusable and shows the whole id on hover and focus. Test: `chart-workstation.spec.ts` "the id is cut in the middle, keeping the timeframe, and shown whole on hover and on focus". |
| F-9 | Low | `/system/logs` · When, Outcome columns | At 1440 px the time is cut ("19/09/2026, 10:00 …") and the ALLOWED badge overflows its 104 px column. | Screenshot `system_logs-1440x900.png`. **Fixed 2026-09-30.** When 176 px (header "When (UTC)"), Outcome 124 px, full values in titles. Test: `grid.spec.ts` "audit log at 1440 px: the time and the outcome badge fit their columns". |
| F-10 | Medium | phone (390 px), several | The floating **Sections** button covers content (the last line of the attention queue on Command, the chart on `/markets/EURUSD`); the status bar is cut off at "AP…"; on Candidates every figure and the **Promote** button are off-screen to the right inside the grid; on Command the incidents table's **Acknowledge** is off-screen. No page scrolls sideways, but the primary action is hidden. | Screenshots `index-390x844.png`, `markets_EURUSD-390x844.png`, `trading_candidates-390x844.png`. **Fixed 2026-09-30.** Sections is in the page header row, in the flow; the status bar wraps below 640 px; Candidates are cards below 640 px; the Command incidents table stacks below 640 px. Tests: `responsive.spec.ts` "phone layout at 390 px" (4 tests). |
| F-11 | Low | `/trading/execution` · R-multiple chart | Loss bins are drawn in loss red; profit bins in neutral provenance ink, not the profit colour. The rule is that green and red mean P&L, both ways. | Screenshot; code `components/charts.tsx` `below ? "chart__bar--down" : "chart-ink"`. **Fixed 2026-09-30.** Profit bins `--pnl-up` with ▲, loss bins `--pnl-down` with ▼, a zero-spanning bin in the source ink. Test: `chart-provenance.spec.ts` "profit bins take the profit token with ▲, loss bins the loss token with ▼". |
| F-12 | Medium | `/trading/candidates` · columns | The grid shows Net P&L and Sharpe, the headline numbers the charter says are never the ranking criterion, and none of DSR, PBO, walk-forward efficiency or OOS hit rate. That is what the API's `CandidateView` carries; it is a backend ask, but the screen currently invites the wrong comparison. | Code `app/trading/candidates/page.tsx`; AGENTS.md §2. |
| F-13 | Low | `/risk` wording | "Disarm and re-arm trading", "Re-arm trading" and the phrase RE-ARM: "arm" means *halt* for the kill switch and *resume* for trading on the same card. | Screenshot `trading_risk_armed-1440x900.png`; `components/KillSwitch.tsx`. **Fixed 2026-09-30.** "Lift the halt (resume trading)", dialog "Lift the halt and resume trading", phrase LIFT HALT, confirm "Lift the halt". Test: `kill-switch.spec.ts` "disarming requires typing the confirm phrase" (RE-ARM is refused). |
| F-14 | Low | LIVE banner · "Kill switch" | In LIVE the banner's kill-switch control is a link to `/trading/risk`, one navigation away from the dialog, rather than opening it (⇧K does open it). | Code `components/ModeBanner.tsx`. Not exercised in LIVE by the walk. |
| F-15 | Low | `/system/settings` · Risk limits | Limit values are printed raw with no unit ("2", "10"); realism settings are bare codes ("zero", "static"). | Screenshot `system_settings-1440x900.png`. **Fixed 2026-09-30.** A Unit column from each key's suffix and the limit set's convention; realism codes glossed; unknown keys and codes say so. Test: `workstation-visuals.spec.ts` "every limit shows its unit; realism codes say what they mean". |
| F-16 | Low | `/research` · strategy tile links; `/markets/data-quality` rows | Every strategy name on the Lab links to the whole Strategies list, not that strategy; Data Quality rows do not link to the instrument or its chart (the plan's primary action for that screen). | Code. |
| F-17 | Test gap | 15 routes | Before the walk, no spec rendered the **success** state of: `/research`, `/research/hypotheses`, `/research/experiments`, `/research/strategies`, `/research/datasets`, `/markets/regimes`, `/intelligence/agents`, `/intelligence/runs`, `/intelligence/research-memory`, `/system/services`, `/system/workers`, `/system/broker-health`, `/system/data-health`, `/system/settings`, `/alerts` (risk card). The a11y and CSP sweeps see their error state only. g-chords f, r, m, d had no test. | Spec files; walk now covers them. |

### Verification of the 2026-09-30 fixes

Run in `apps/web` in the session that made the fixes (times UTC):

- `npm run typecheck`: exit 0. `npm run lint`: exit 0 (no findings).
- `NEXT_PUBLIC_FIBOKI_API=http://127.0.0.1:8000 npm run build`: exit 0; 35 static pages, including
  `/system/incidents/inc-1` and `/lifecycle/abc123def456` (placeholders prerendered for the budget).
- `NEXT_PUBLIC_FIBOKI_API=http://127.0.0.1:8000 npx playwright test --project=desktop --reporter=line`,
  19:08:41Z to 19:22:59Z: **490 tests, 482 passed, 8 skipped, 0 failed**. That includes the
  walkthrough (35 tests, the two that failed above now pass) and the new specs
  `incident-detail.spec.ts`, `lifecycle.spec.ts`, `workstation-visuals.spec.ts`.
- `npm run size`: exit 0; `/` 179.4 KiB of its 180 KiB budget (the kill-switch timeline is loaded
  after first paint, and the loss rows come from `lib/limits-core.ts` to keep it there), every
  other route within 230 KiB; the two new routes 175.5 and 175.9 KiB.
- `npm run contrast`: exit 0; 316/316 pairs, 8/8 distinguishability pairs.

Not findings, to save someone the check: validation verdicts and data-quality states are lower-case in
the API (`reject`, `not_validated`, `validated`, `pending`); an unknown value is shown as
"UNRECOGNISED: …" rather than guessed, which is the intended behaviour.

**Walkthrough caveats:** the walk runs in paper mode against mocks with the stream held "connecting",
so LIVE-only and stream-only behaviour is from the code and the existing specs, not from the walk. The
walk counts a popover as opened if a dialog, menu or listbox appears or the trigger reports
`aria-expanded="true"`, and it waits for exit transitions before the next click (because of F-6).

---

## 5. Known gaps against the plan

### 5.1 The nine screens (plan §4)

| Screen | Today | Primary action present? |
|---|---|---|
| Command | `/` with server-ranked queue, loss-limit bars, fleet strip, incidents, kill-switch timeline | Yes (triage); deep links resolve since 2026-09-30 |
| Fleet & Positions | Portfolio only; **no per-bot fleet view**; positions are read-only | **No** "pause or close one bot or position" |
| Strategy Lifecycle | Candidates (promote), `/lifecycle/<hash>` | Promote only; **no demote or retire**; `/{hash}` and `/{hash}/evaluation` are read by `/lifecycle/<hash>`; `GET /api/trading/lifecycle/strategies` (the list) is still unused |
| Research Lab | Seven read-only pages | **No** "queue an experiment", although `POST /api/research/experiments` exists |
| Market Intelligence | Explorer, chart, regimes, correlations, agents, runs, memory | Yes (open a symbol in the chart) |
| Risk & Exposure | Yes | Yes (arm/disarm) |
| Data Quality | Market data, Data Health | **No** "open the affected instrument or dataset" (rows do not link) |
| System & Incidents | Services, Workers, Broker Health, Audit log, Settings, Legend, `/system/incidents/<id>`; the incident list only on Command | Acknowledge (Command and the incident page) and annotate (incident page); **no incident list page** |
| Journal | Trades list | **No** "add a note to a trade" |

**Entity routes (plan §4): `/markets/[symbol]`, and since 2026-09-30 `/lifecycle/[hash]` and
`/system/incidents/[id]`.** Missing: `/fleet/positions/[id]`, `/journal/[tradeId]`, and the inspector
that would open them inline. **Agent Desk (Wave 4e)** and the **phone/tablet kill-switch ops view (Wave 4f)**: no page.

**Backend endpoints with no UI** (the incident read and note, the kill-switch history and two of
the three lifecycle routes gained one on 2026-09-30): `GET /api/system/kill-switch/disarm/preflight`
(the UI uses the trading-side twin), `GET /api/system/queues`, `GET /api/trading/execution-telemetry`,
`GET /api/trading/lifecycle/strategies` (the list), `GET /api/research/strategies/{id}`,
`POST /api/research/experiments`, `POST /api/intelligence/research-memory/notes`.

### 5.2 Chart workstation C1–C18 (plan §10, report G §2.4)

| # | Feature | State |
|---|---|---|
| C1 | Symbol and timeframe switcher | **Partial**: H1/H4/D1 fixed list, symbol via palette; no `1`–`6` keys; no backend list of available timeframes |
| C2 | Multi-timeframe sync, link groups | Not built |
| C3 | Crosshair sync to analytics | Not built |
| C4 | Signal, fill and trade overlays | Built (markers, filled/hollow, P&L-coloured connector) |
| C5 | Stops, targets, entry; stop history | **Partial**: price lines built; stop-move history is unavailable from the backend and says so |
| C6 | Regime bands | Built (tint, ribbon, UNKNOWN hatched) |
| C7 | Session shading | Not built |
| C8 | Event and headline markers | Not built: events are **received and counted** ("received, not drawn") |
| C9 | Replay (time machine) | Not built; backend `as_of` not built |
| C10 | "Why did this trade happen" inspector | Not built |
| C11 | Drawings, server-side and audited | Not built; `/api/markets/drawings` not built |
| C12 | Forming bar | Not built: the API sends no forming flag (stated in `PriceChart.tsx`) |
| C13 | Gaps and missing data | **Partial**: series gaps are whitespace; missing candles are not marked |
| C14 | Data provenance header | **Partial**: dataset version, source kind, as-of; no price basis |
| C15 | Keyboard and screen reader | **Partial**: arrow-key crosshair, readout, View data; no Enter on a marker |
| C16 | Performance (5,000 bars, 500 markers, INP < 200 ms) | **Not tested** (no perf spec) |
| C17 | PNG snapshot for the Journal | Not built |
| C18 | Compare | Not built |

---

## 6. Where visuals would make it easier

Chart forms are from the plan: Lightweight Charts for price only; uPlot or owned SVG for analytics;
sparklines inside Stat tiles; heat cells; step meters; timelines. The browser draws what the API
sends and computes nothing that a strategy, a gate or the risk engine would compute. Where a chart
needs a series the API does not send, that is listed as a backend ask rather than derived in the
browser. Effort: **S** up to a day, **M** two to four days, **L** more than a week (including
backend).

### 6.1 Top ten, ranked by the decision they speed up

| Rank | Page | Opportunity | Decision it speeds up | Data | Form | Provenance | Effort |
|---:|---|---|---|---|---|---|---|
| 1 | Command | Replace the three "first numbers" tiles with the limit board's own bars for daily loss and drawdown, plus the single "closest to a limit" row | "Do I need to act now?" is answered on landing without opening Risk | `GET /api/trading/risk`, `GET /api/trading/exposure` (exists; reuse `lib/limits.ts` rows and `LimitBoard` `Row`) | Horizontal bullet bars with 70%/90% ticks | Chip per bar as on Risk; † for workstation-computed utilisation | S **Built 2026-09-30** (Command "Loss limits", daily loss and drawdown rows; the closest-to-a-limit row was not added, as Command reads no exposure). |
| 2 | Command, Risk | Kill-switch and incident timeline: arms, disarms (who, reason), incidents opened and acknowledged, on one UTC axis for the last 24 h | "What happened while I was away, and who did it?" | `GET /api/system/kill-switch/history` (exists, unused), incident `timeline` in `GET /api/system/incidents` (exists) | Owned SVG timeline, glyph per event kind, refusals marked | Source badge per stream of events; audit sequence on hover and in View data | M **Built 2026-09-30** over the last 30 days (Command and Risk); acknowledgements are not on it (the incident page has them), and the journal carries no audit sequence to show. |
| 3 | Command fleet strip, Workers | Heartbeat age sparkline per worker with the stale threshold drawn | "Is the worker dying or was that a blip?" (the V1 3 am lesson) | **Backend ask:** heartbeat history (the stream carries only the latest age) | Sparkline in the Stat tile, threshold line, gaps for no heartbeat | Probe data: labelled as a probe, not a result; "never" drawn as no line, never zero | M |
| 4 | Validation, Candidates | Promotion-ladder step meter per strategy: each rung passed, failed, not run, with the binding constraint on the failing rung | "Why is this not promotable, and how far did it get?" | `GET /api/research/validation` (`rungs_passed`, `rungs_total`, `binding_constraint`) exists; per-rung names are a **backend ask** | Step meter (as the throttle meter) | NOT_EVALUATED drawn as blocked, never as passed; chip from the report's provenance labels | S (counts) / M (named rungs) **Built 2026-09-30** with counts (segments by index; named rungs remain a backend ask). |
| 5 | Risk drawer, Portfolio | Distance-to-stop meter per open position | "Which position is about to be stopped?" | `distance_to_stop_pct` in `GET /api/trading/positions` (exists, shown only as a number) | Short horizontal meter per row, sorted by distance | Row chip; "est" marks estimated marks | S **Built 2026-09-30** (Risk drawer and Portfolio), placed from entry, mark and stop, marked † with the formula; not sorted. |
| 6 | Portfolio | Drawdown pane under the equity curve, and an open-risk strip | "How deep are we, and is it recovering?" | **Backend ask:** drawdown series (plan §6 says drawdown is its own pane; do not derive it in the browser) | uPlot, second pane, shared time axis | Same chip as equity; split rather than mix if provenances differ | M |
| 7 | Chart workstation | Draw the calendar events the overlays already return (C8) | "Did that fill happen into a news release?" | `events` in `GET /api/markets/overlays/{symbol}` (exists; counted as "received, not drawn") | Vertical rules with impact glyph on the price pane | Event source (official calendar) in the Sources popover, per event | M |
| 8 | Regimes, Market Pulse | Instrument × axis heat cells (volatility, direction, liquidity, stress, persistence) | "Where is the market trending or stressed, and where do we not know?" | `GET /api/markets/regimes` (exists; `persistence` is not shown today) | Categorical heat cells; UNKNOWN hatched, never a colour of a real state | Source badge; classifier fingerprint on hover | S **Built 2026-09-30** on Regimes (not Market Pulse); no classifier fingerprint is in the API, so hover shows value and reason. |
| 9 | Data Quality | Coverage strip per instrument with gap and stale-run ticks | "Can I trust this instrument's history for that period?" | Counts exist in `GET /api/markets/data-quality`; **backend ask:** gap and stale-run time ranges | Timeline strip per row; hatched gaps | Dataset version on each strip | M |
| 10 | Execution (Journal) | Cumulative R over time, one small chart per provenance (never one line across provenances) | "Is paper tracking what research said?" | **Backend ask:** cumulative series per provenance (the page holds at most 200 trades, so a browser sum would silently cover a slice) | uPlot small multiples, dashed backtest, dotted walk-forward, solid executed | One chip per chart; mixed data split, as the plan requires | M |

### 6.2 The rest, by page

- **Market Pulse:** the page is a thinner copy of Explorer. Either make it the regime heat grid (6.1 #8) plus a spread-cost strip, or retire it. Data exists. S.
- **Alerts:** a single health-check strip (one cell per check, glyph and word) above the list, so "all green" is visible at a glance. `GET /api/health`. S.
- **Candidates:** an out-of-sample equity sparkline per row. **Backend ask:** per-candidate OOS equity series. Must carry the OOS chip; never an in-sample line. M.
- **Parameter Lab:** each domain as a range bar (min to max, a tick at the default, step marks), and the search-space size as a product of per-parameter counts. Data exists. S.
- **Correlations:** larger cells with the value printed in each, backend-ordered clusters, and the window (`window_bars`) in the title. Data exists except cluster order (backend ask). S.
- **Experiments, Hypotheses:** outcome counts per strategy as a stacked bar ("tried 14, 12 rejected at deflation"). **Backend ask:** counts, since the list is paginated. M.
- **Services, Data Health:** probe latency sparkline. **Backend ask:** probe history. M.
- **Audit log:** a day timeline of actions with refusals marked, above the grid. Data exists in the 200 rows returned; label it "last 200 entries". S.
- **Research Memory:** rediscovery rate over time. **Backend ask:** history. M.
- **Broker Health:** the five mode-guard controls as a checklist diagram ending in ALLOWED/REFUSED. Data exists. S.
- **Chart workstation:** session shading (C7, needs the backend session calendar, M); stop-move step series once the paper journal records stop history (backend, M).

### 6.3 Usability frictions seen in the screenshots

| Where | Friction | Fix |
|---|---|---|
| Every page | The source badge ("LIVE · Inventory fixture · as of …") takes a full-width dashed row under every panel; on Research Lab and Market Pulse it appears twice in one screen. | One compact source line in the panel title row; the full detail in its popover. |
| Command | Two of the three "first numbers" are NOT REPORTED, so the hero row leads with absences. | Keep the honesty but move absences to one line ("Open risk and throttle are not reported by the API") and give the space to 6.1 #1. |
| Command | Fleet strip cells are paragraphs of explanatory text. | One line per cell; the explanation in a popover. |
| Risk & Exposure | Six NOT REPORTED rows take about half of the limit board's height. | Group them into one collapsed "Not reported by the API (6)" disclosure under the measured rows. |
| Candidates (390 px) | Figures and **Promote** are off-screen inside the grid (F-10). | Pin the Action column on the right at narrow widths, or a card layout per candidate. |
| Command, chart (390 px) | The floating Sections button covers content; the status bar is cut off (F-10). | Put Sections in the page header row; let the status bar wrap or collapse to mode, worker and API. |
| Audit log | Truncated time and outcome badge (F-9). | Widen the two columns; show the date once per day group. |
| Chart | Dataset chip loses the timeframe (F-8); the disclosure summary "3 sections not available · received, not drawn · outside the bar window" is jargon. | Truncate in the middle (`dsv_eurusd_h1…abcdef`); summary "Some overlays are missing — why". |
| Settings | Raw keys and unit-less values (F-15). | Use the limit's own unit and a label per key from the API. |
| Kill switch | "Disarm and re-arm trading" / "Re-arm trading" / RE-ARM (F-13). | "Lift the halt" and phrase LIFT HALT, or "Resume trading" and RESUME. |
| 15 table pages | No sort, filter or export (F-7). | Move Workers, Services, Validation, Experiments and Data Quality onto the DataGrid first. |
| Execution | Profit bins not in the profit colour (F-11). | Profit bins in the profit token with ▲, loss in the loss token with ▼. |
| Research Lab, Data Quality | Links go to a list, not the thing (F-16). | Deep-link once entity routes exist; until then `?row=`. |
