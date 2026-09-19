# Security Model

**Snapshot:** 2026-09-19T05:05Z (`pytest tests/ -q` → 2683 passed, 2 skipped). Primary modules: `src/fiboki/api/security.py`,
`settings.py`, `errors.py`, `audit_trail.py`; `src/fiboki/broker/mode_guard.py`,
`src/fiboki/broker/oanda.py`, `src/fiboki/broker/ig.py`; `src/fiboki/agents/capabilities.py`.

**Read this first.** The `api/` package is **implemented and tested** (`tests/api/test_security.py`,
`test_behaviour.py`, `test_health.py`, `test_provenance_contract.py`) but is **uncommitted** at
this snapshot, along with 156 other paths. It has never served a request outside a test client,
and no deployment exists. Nothing here should be read as a claim about production behaviour.
§11 carries the per-control verification table.

---

## 1. What is being protected, and from whom

Fiboki holds no customer data and no payment information. What it holds is the ability to place
orders, the credentials that would allow it, and a research record whose value depends entirely
on nobody having quietly edited it.

The three assets, in order of consequence:

1. **The path to real money.** Reaching `ExecutionMode.LIVE`, or pointing a demo credential at a
   live host.
2. **The kill switch and the risk limits.** An attacker who can disarm the switch or widen a
   limit does not need to place an order themselves.
3. **The research and audit record.** A ledger that can be edited is worth nothing, because its
   entire value is that it contains the results nobody liked.

The operators are two named humans on one machine. The realistic threats are therefore not a
targeted intrusion but: a cross-site request from a page one of them has open; a credential
reused from elsewhere; a misconfigured environment variable; a dependency that ships something
it should not; and — the one that actually happened in V1 — a well-meaning change that removes a
control nobody was checking.

## 2. Authentication and session handling

`api/security.py`.

The session is a signed cookie **plus** a server-side record, and both are required. The cookie
is `base64(json).base64(hmac-sha256)`, opaque to the browser, verified with
`hmac.compare_digest`. A malformed cookie returns `None` rather than raising, so a broken cookie
is simply not a session.

The signature proves the cookie was minted here. It cannot prove the session is still wanted, so
`SessionStore` is a server-side revocation list that decides whether it is still valid — *"a
signed cookie alone cannot be revoked before it expires, which is not acceptable for an account
that can arm a kill switch."* It supports `revoke`, `revoke_user` (every session for one user,
which is what you want during an incident) and `active`, and expires records lazily on read.

Cookie hardening comes from `Settings`: `httponly=True` always, `secure=True` by default and
turned off only for local HTTP development explicitly, `samesite="strict"` by default, and an
explicit path. V1 used `SameSite=None`, which was necessary for its Vercel↔Railway split and was
precisely what made the CSRF in §3 reachable. V2 being local-first removes the need for the
relaxation.

`session_secret` comes from `FIBOKI_SESSION_SECRET`. If unset, a 32-byte secret is generated per
process and `session_secret_is_ephemeral` is set to `True` — which means restarts invalidate
sessions. That is correct for a single-node development box and **is reported as a health
warning** so nobody ships it that way. Default TTL is 12 hours.

**Nothing in this module reads a credential from a query string or a path parameter.** The
session lives only in the httpOnly cookie. V1 is not recorded as having put tokens in URLs, but
the property is stated so a future route cannot introduce one without contradicting a written
rule.

### Login rate limiting

V1 had none: unlimited password guesses against two known usernames, no lockout, no
failed-attempt alerting, and seeded passwords defaulting to the literal `changeme`.

`LoginRateLimiter` is a fixed-window attempt counter with a lockout, keyed by **identity and
source together** — so one user's typos cannot lock the whole office out, and one source cannot
enumerate usernames without tripping its own limit. Defaults: 5 attempts in a 300-second window,
then a 900-second lockout. A locked-out request returns 429 with a `Retry-After` header. A
success clears both counters.

`obs/alerts.AlertEvent` does not currently include a login-failure event; that is a gap.

## 3. CSRF and Origin validation

This is the control V1 lacked entirely, and the one whose absence was most exploitable.

V1's session cookie was `SameSite=None`, and several dangerous endpoints took **no body**:
`POST /system/killswitch`, `POST /bots/{id}/approve`, bot stop/pause/resume/restart,
restart-all, reset-all, account reset. A plain HTML form POST with no body and no custom headers
is a **CORS-simple request**: the browser sends it with credentials and never fires a preflight,
so CORS configuration alone stops nothing. There was no CSRF token and no Origin check.

V2 requires **two independent things** on every mutating request.

**An allow-listed Origin.** `validate_origin` reads `Origin`, falling back to the origin reduced
from `Referer`, normalises it (trimmed, trailing slash removed, lower-cased) and compares it for
**exact membership** in `Settings.allowed_origins`. Not a prefix match, not a regex. And
critically: **absence is a refusal, not a pass.** A request carrying neither header is rejected
403 `origin_missing`, with the reasoning stated in the function's docstring — a cross-site form
post carries an Origin, so a request with none is either a non-browser client (which should send
one anyway, and the deployment can allow-list it) or an attempt to slip past a check that treats
"missing" as "same site". V1 had neither branch.

**A double-submit CSRF token.** `validate_csrf` compares the `fiboki_csrf` cookie against the
`X-Fiboki-CSRF` header with `hmac.compare_digest`. Belt and braces with the Origin check: an
attacker's page can cause the cookie to be sent but cannot read it to populate the header,
because the CSRF cookie is same-site and the session cookie is httpOnly.

`GET`, `HEAD` and `OPTIONS` skip both.

## 4. RBAC

`Role` has three members, ordered, with least privilege first:

| Role | Rank | Can |
|---|---:|---|
| `VIEWER` | 0 | read only — **cannot mutate anything at all** |
| `OPERATOR` | 1 | day-to-day operation |
| `ADMIN` | 2 | everything an operator can, plus administration |

`satisfies(required)` compares ranks. `require_operator` and `require_admin` are FastAPI
dependencies built by a shared `_require(role)` factory.

V1 had a `UserModel.role` field referenced **only** in the `/auth/me` response, no
`require_admin` dependency anywhere, and therefore every logged-in session could create
execution accounts with `environment="live"` and `live_allowed=True`, PATCH `live_allowed`,
deactivate the kill switch, delete all bots and reset the account.

V2 applies the admin dependency **by the router factory** rather than route-by-route, so a new
route cannot be added without inheriting it, and `tests/api/test_security.py` enumerates every
mutating route across the six routers (`auth`, `system`, `markets`, `research`, `trading`,
`intelligence`) and asserts it. That is the mechanism that makes the guarantee survive a route
somebody adds in a hurry.

## 5. Error handling

`api/errors.py`. The body carries a stable machine-readable `code`, a `detail` written for an
operator to act on, the request's correlation id, and an optional `context` populated only by
code that deliberately chose its contents.

It **never** carries a traceback, a file path, a SQL fragment, a driver message or a settings
value. Those go to the log, joined to the same correlation id — so an operator can quote one
token and the server-side record can be found, without the token itself disclosing anything.

## 6. The operator audit trail

`api/audit_trail.py` is an append-only, hash-chained record of every mutating API call, sealed
with `sha256(previous_hash + canonical_payload)` from a fixed `GENESIS`. A deleted or edited row
breaks verification at that point.

It is deliberately **separate** from `agents/audit.py`: that ledger records what an *agent* did,
this one records what a *human operator* did through HTTP. Different question, different
retention need, so a separate ledger rather than a widened one.

**Refusals are recorded as loudly as successes.** An attempted kill-switch disarm by a
non-admin is exactly the row an incident review needs, and V1 wrote neither.

## 7. Secret management and execution-mode isolation

This section is committed and tested (`tests/unit/test_mode_guard.py`,
`test_oanda_adapter.py`, `test_ig_adapter.py`).

### Secrets

Everything dangerous is read in `api/settings.py` and nowhere else, once, at process start.
There is no setter for the execution mode; `api/routers/system.py` exposes it as data and
refuses every request to change it. An unrecognised `FIBOKI_EXECUTION_MODE` is a **startup
error**, not a silent fallback to paper, because a typo must not decide where orders go.

No credential is committed. `.gitignore` excludes the data directories and local databases. V1
committed `FIBOKEI_LIVE_EXECUTION_ENABLED: "true"` as a literal in `render.yaml`; V2 has no
deploy file containing a live flag, and — more importantly — no deploy file *could* enable live,
because the first control is a source constant.

### The controls that must all hold for a live order

Enumerated in full in `EXECUTION_ARCHITECTURE.md` §6. In summary: two **build-time source
constants** in different modules, deliberately named differently so flipping one does not flip
the other (`LIVE_EXECUTION_COMPILED_IN`, `OANDA_LIVE_HOST_COMPILED_IN`, both `False`); two
**unguessable environment tokens** (`FIBOKI_LIVE_RUNTIME_ARMED` must equal
`"ARMED-LIVE-EXECUTION-I-ACCEPT-REAL-MONEY-RISK"`; `FIBOKI_OANDA_LIVE_RUNTIME` must equal
`"OANDA-LIVE-HOST-ARMED-I-ACCEPT-REAL-MONEY-RISK"` — deliberately not `"true"`, because a value
that cannot be guessed cannot be set by accident); a **persisted, expiring, human-named
authorisation** with a **per-strategy allow-list**; and **parsed-hostname assertions** at both
the mode guard and the adapter.

The hostname assertion is the specific fix for V1's worst control. V1's live gate was an exact
string comparison against a base-URL constant, which **a trailing slash defeated** — so one
misconfigured environment variable reached the live Tradovate API. V2 parses with
`urllib.parse.urlsplit` and compares `hostname`: `https://api.example.com` and
`https://api.example.com/` parse identically, and `https://api.example.com.evil` does not match
despite sharing the prefix. There is no "unknown host, probably fine" branch — anything that is
neither the practice host nor an explicitly authorised live host is refused outright.

The guard also runs the safe way round: a `DEMO` or `PAPER` run whose venue URL resolves to a
known live host is **refused**, because pointing demo credentials at a live host is how "we were
only testing" becomes an incident.

`LiveAuthorisationStore` never raises into the caller and never falls back to a permissive
default. A missing or unreadable file means **no** authorisation, and the file being absent is
the normal state.

`tests/unit/test_mode_guard.py` sets each control on its own and asserts live is **still**
blocked, and asserts a config file alone cannot enable it.

**There is no API endpoint, for any role, that can move the platform to LIVE.** Asking over HTTP
returns 403 with the list of controls.

## 8. Agent least privilege

Committed and tested. Full treatment in `AI_AGENT_ARCHITECTURE.md`; the security-relevant
summary:

- **Deny by default.** `CapabilityResolver` resolves an unknown principal to the empty set.
  There is no wildcard, no admin short-circuit and no inheritance.
- **The capability enum cannot express execution.** 20 members: 12 reads, 6 research writes, 1
  job submission. `assert_no_execution_capability()` runs **at import** and makes adding one an
  `ImportError` for the whole package; it checks both member name and value.
- **No agent module may import the execution layer.** An AST test over every file under
  `agents/` fails on any import from `fiboki.broker`, `fiboki.risk` or `fiboki.portfolio`, or of
  the names `Order`, `Fill`, `OrderType`, `ExecutionMode`.
- **The write surface is enumerated.** `WriteDomain` has six members, all research artefacts or
  the job queue. A tool wanting to write elsewhere cannot describe itself.
- **The job queue cannot express an execution job.** `JobType` has no ORDER, EXECUTION,
  PROMOTION or LIMIT_CHANGE.
- **No dynamic execution.** `assert_no_dynamic_execution()` walks the package's ASTs and proves
  there is no `eval`, `exec`, `compile`, `__import__`, `pickle` or `subprocess` call site
  anywhere in it. Agent text enters only through `json.loads`, into schemas declared
  `extra="forbid"`, after a conservative code-syntax scan and structural shape limits applied
  first so a hostile payload cannot exhaust memory before validation rejects it.
- **One enforcement point.** `AgentSession.call` checks role bundle → capability → input schema →
  budget → handler → output schema → exactly one audit record, on every path including refusal.
  No handler is ever handed out.
- **Model prompts stay local by preference.** `ModelRouter` sorts by `(cost, non-local)`, so a
  local model wins when registered, keeping research prompts on the machine that generated them.
  The three remote adapters are inert without an injected HTTP client and open no socket at
  import.

## 9. Supply chain and dependency pinning

`pyproject.toml` pins every direct dependency to an **exact** version, including the build
backend (`hatchling==1.27.0`), with the reason written above the block. Python is constrained to
`>=3.11,<3.13`. The dev extras are pinned identically.

This closes the V1 defect where `pandas>=2.0` and `numpy>=1.24` resolved to a major version
beyond what the author intended on every machine tested, with two of the six uncommitted
working-tree changes already being hot-fixes for the resulting breakage.

**Gaps, stated:**

- **Transitive dependencies are pinned** by `deploy/constraints.txt` and recorded in
  `deploy/requirements.lock`, with `make lock-check`, the CI `lockfile` job and
  `fiboki system doctor` all failing on drift. **They are not hash-pinned**, so a compromised
  release of a locked version would still install.
- **A dependency audit runs in CI** (`pip-audit --strict --desc`) as a required gate, and
  `make audit` runs it locally. It needs network, so an offline run reports that rather than
  passing silently.
- V1 carried `ecdsa` 0.19.2 with PYSEC-2026-1325 and no fix version, in the JWT signing path via
  `python-jose`. **V2 does not depend on `python-jose` or `ecdsa`** — sessions are signed with
  `hmac` from the standard library, so that specific exposure is gone rather than mitigated.
- There is **no frontend** in V2 at this snapshot (`apps/web/` is empty), so V1's 8 npm
  vulnerabilities, 3 critical, including a vulnerable `next` and a caret range on a beta
  charting library, do not carry over. They will return as a live concern the moment a frontend
  is added, and that is a decision to take deliberately.
- The `mypy` and `ruff` dev pins exist; `ruff check src tests` currently reports **32 findings**
  (25 in `src`), concentrated in `strategy/dsl.py`, `api/platform.py`, `obs/metrics.py`,
  `api/security.py` and four test files. None is a security finding, but a lint signal that is
  not green is a lint signal nobody reads — which is exactly how V1 normalised 17 errors and 33
  warnings.

## 10. Threat model

### Defended against

| Threat | Control |
|---|---|
| Cross-site request forging a mutating call | exact Origin allow-list with absence-is-refusal, plus double-submit CSRF, plus `SameSite=strict` |
| Session theft via XSS reading the cookie | `httpOnly` |
| Session replay after revocation | server-side `SessionStore`, `revoke_user` for incidents |
| Password guessing | per-(identity, source) fixed-window limiter with lockout and `Retry-After` |
| Privilege escalation to administration | ranked `Role`, `require_admin` dependency *(route coverage not yet proved)* |
| A misconfigured URL reaching a live venue | parsed-hostname assertion at two layers; trailing slash and prefix attacks both fail |
| A single environment variable enabling live | five independent controls, two of which are source constants |
| A stale live authorisation surviving an incident | expiry checked against the clock |
| An LLM reaching an order | six independent mechanisms, two of them import-time or AST-enforced |
| An LLM turning text into behaviour | `json.loads` only; AST proof of no dynamic execution; `extra="forbid"` |
| Quiet edits to the research or audit record | hash-chained ledgers; SQLite triggers on the experiment ledger; `verify_chain` |
| Error responses leaking internals | `ErrorBody` carries code, detail, correlation id only |
| Major-version dependency drift changing results | exact direct pins |

### Explicitly NOT defended against

Stated plainly, because a threat model that lists only successes is a marketing document.

- **A compromised operator machine.** Fiboki is local-first. Anyone with the ability to run code
  as the operator can read `FIBOKI_SESSION_SECRET`, edit the source constants, and edit the
  ledgers on disk. The hash chains make the tampering *detectable* by a later `verify_chain`;
  they do not prevent it, and an attacker who can edit the chain can re-seal it.
- **A malicious or compromised dependency.** The lockfile records versions, not hashes, and
  nothing is vendored. `pip-audit` catches *published* advisories; it cannot catch a
  freshly-compromised release of a version we have locked.
- **A malicious commit.** Every live control is a source constant or an AST test in the same
  repository. A reviewer is the only thing between a commit that deletes them and a deploy;
  there is no branch protection or signing requirement configured in this repository.
- **Broker credential theft at rest.** Credentials come from the environment. There is no
  keychain integration, no envelope encryption and no secret rotation.
- **Denial of service.** Rate limiting exists on login only. No other endpoint is throttled and
  there is no request-size limit configured.
- **Multi-tenant isolation.** There is none, and none is intended. All operators share one
  deployment, one data root and one set of limits. RBAC separates *capability*, not *data*.
- **Network-level attackers.** No TLS termination is configured in this repository, no
  certificate pinning, and no mutual TLS to the broker. A local-first deployment on `localhost`
  sidesteps this; a deployment reachable from a network does not, and would need it added.
- **Insider misuse by an authorised operator.** An admin can activate live execution given the
  full control set, flatten the book, or delete data. The defence is the audit trail — which is
  detection, not prevention, and is the correct trade for a two-person team.
- **Log confidentiality.** Structured logs carry full tool inputs and outputs, and the agent
  audit ledger records prompts verbatim. Anyone who can read `var/` can read the research
  programme. There is no redaction layer.
- **Timing side channels** beyond the `compare_digest` uses noted above.

## 11. Verification status

| Control | Implemented | Tested |
|---|---|---|
| Mode guard, five controls | yes | **yes** — `test_mode_guard.py` |
| OANDA three host controls | yes | **yes** — `test_oanda_adapter.py` |
| IG demo-only impossibility | yes | **yes** — `test_ig_adapter.py` |
| Agent capability guard at import | yes | **yes** — `test_agents_capabilities.py` |
| Agents never import execution | yes | **yes** — `test_agents_research_writes.py` |
| No dynamic execution in agents | yes | **yes** — `test_agents_sandbox.py` |
| Deny-by-default permission resolution | yes | **yes** — `test_agents_permissions.py` |
| Agent audit ledger append-only + chain | yes | **yes** — `test_agents_audit.py` |
| Session signing, revocation, cookies, rate limiting | yes | **yes** — `tests/api/test_security.py` |
| Origin allow-list and double-submit CSRF | yes | **yes** |
| Ranked RBAC and the admin dependency on mutating routes | yes | **yes** |
| Error bodies leak nothing | yes | **yes** |
| Operator audit hash chain | yes | **yes** |
| Exact direct dependency pins | yes | n/a |
| Transitive pins via constraints + lockfile | yes | CI `lockfile` job |
| Dependency vulnerability audit | yes | CI `audit` job (needs network) |
| Hash-pinned dependencies | **no** | **no** |

One row is not green, and it is the one that matters most operationally: nothing here has served
a request outside a test client, no deployment exists, and the whole tree is uncommitted.
**Keep the HTTP surface on `127.0.0.1` until it has been deployed, reviewed in place and
exercised against a browser**, and that is the operating instruction in `OPERATIONS.md`.
