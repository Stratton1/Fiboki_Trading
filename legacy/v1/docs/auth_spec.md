# Fiboki Authentication Specification

## 1. Overview

Fiboki uses a dual-cookie JWT authentication pattern. The backend issues an HttpOnly `fibokei_token` cookie on login. The frontend also stores the token in `localStorage` and sets a non-HttpOnly `fiboki_auth` marker cookie for Next.js middleware (which cannot read HttpOnly cookies). API calls use `credentials: "include"` for cross-origin cookie transmission, with a `Bearer` header fallback from localStorage when cookies are unavailable.

## 2. Cookie Configuration

Set on successful `POST /api/v1/auth/login`:

| Property | Value |
|----------|-------|
| Key | `fibokei_token` |
| HttpOnly | `true` |
| SameSite | `None` (production) / `Lax` (local dev) |
| Secure | `true` (production) / `false` (local dev) |
| max_age | `86400` (24 hours) |

**Note:** `SameSite=None; Secure=true` is required in production because frontend (`fiboki.uk`) and backend (`api.fiboki.uk`) are on different origins. The `FIBOKEI_LOCAL_DEV` env var switches to `SameSite=Lax; Secure=false` for local development.

Cleared on `POST /api/v1/auth/logout` via `response.delete_cookie("fibokei_token")`.

**Source:** `backend/src/fibokei/api/routes/auth.py`

## 3. JWT Token Structure

| Claim | Value | Example |
|-------|-------|---------|
| `sub` | User ID (string) | `"1"` |
| `username` | Username | `"joe"` |
| `exp` | Expiry (UTC timestamp) | `1709856000` |

**Signing:**
- Algorithm: `HS256`
- Secret: `FIBOKEI_JWT_SECRET` environment variable (required)

**Source:** `backend/src/fibokei/api/auth.py`

```python
payload = {
    "sub": str(user_id),
    "username": username,
    "exp": datetime.now(timezone.utc) + timedelta(hours=24),
}
jwt.encode(payload, secret, algorithm="HS256")
```

## 4. Backend Authentication Dependency

The `get_current_user` FastAPI dependency extracts and validates the token:

1. Check for `fibokei_token` cookie.
2. If no cookie, fall back to `Authorization: Bearer <token>` header.
3. If neither present, raise `401 Unauthorized`.
4. Decode JWT and return `TokenData(user_id, username)`.

**Source:** `backend/src/fibokei/api/auth.py`

```python
def get_current_user(
    request: Request,
    token: str | None = Depends(oauth2_scheme),
) -> TokenData:
    cookie_token = request.cookies.get("fibokei_token")
    effective_token = cookie_token or token
    if not effective_token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return decode_token(effective_token)
```

All protected routes declare `user: TokenData = Depends(get_current_user)`.

## 5. User Model

**Source:** `backend/src/fibokei/api/auth.py`

| Column | Type | Constraints |
|--------|------|------------|
| `id` | Integer | Primary key, autoincrement |
| `username` | String(50) | Unique, not null |
| `password_hash` | String(200) | Not null (bcrypt) |
| `role` | String(20) | Default: `"user"` |

Passwords are hashed with `bcrypt` via `bcrypt.hashpw()` and verified with `bcrypt.checkpw()`.

## 6. Seeded Users

On application startup, two users are created if they do not already exist.

**Source:** `backend/src/fibokei/api/seed.py`

| Username | Password Source | Role |
|----------|----------------|------|
| `joe` | `FIBOKEI_USER_JOE_PASSWORD` env var (default: `changeme`) | `admin` |
| `tom` | `FIBOKEI_USER_TOM_PASSWORD` env var (default: `changeme`) | `admin` |

Seeding runs during the FastAPI lifespan startup in `backend/src/fibokei/api/app.py`.

## 7. Frontend Middleware

**Source:** `frontend/src/middleware.ts`

Next.js middleware runs server-side on every request (except static assets and API routes). It checks for the `fiboki_auth` marker cookie (a non-HttpOnly cookie set by the frontend on login):

- **No `fiboki_auth` cookie + not on `/login`:** Redirect to `/login`.
- **Has `fiboki_auth` cookie + on `/login`:** Redirect to `/` (dashboard).
- **Otherwise:** Pass through.

```typescript
export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|icon.svg|api).*)"],
};
```

**Why `fiboki_auth` instead of `fibokei_token`?** Next.js middleware runs on the edge and cannot read HttpOnly cookies. The frontend sets a separate `fiboki_auth=1` marker cookie (non-HttpOnly, `path=/`) after successful login to signal auth state to the middleware. The actual JWT (`fibokei_token`) remains HttpOnly and is validated server-side on each API call.

## 8. AuthProvider Context

**Source:** `frontend/src/lib/auth.tsx`

The `AuthProvider` React context manages client-side auth state.

### Interface

```typescript
interface AuthContextType {
  user: User | null;           // { user_id, username, role }
  isAuthenticated: boolean;    // !!user
  isLoading: boolean;          // true during initial /auth/me check
  login: (username: string, password: string) => Promise<boolean>;
  logout: () => Promise<void>;
}
```

### Behavior

1. **On mount:** Calls `GET /auth/me` to check existing session. Sets `isLoading = true` until resolved.
2. **login():** POSTs form-encoded credentials to `/auth/login`. On success: stores the `access_token` in `localStorage` as `fibokei_token`, sets `fiboki_auth=1` marker cookie (for middleware), then calls `/auth/me` to populate user state. Returns `true` on success, `false` on failure.
3. **logout():** POSTs to `/auth/logout` (clears HttpOnly cookie server-side). Removes `fibokei_token` from localStorage, clears the `fiboki_auth` marker cookie, and sets `user = null` locally.
4. **Dual auth transmission:** API calls include `credentials: "include"` (for the HttpOnly cookie) and an `Authorization: Bearer` header from localStorage (fallback for when cross-origin cookies are blocked).

### Usage

```tsx
// In app/layout.tsx
<AuthProvider>{children}</AuthProvider>

// In any component
const { user, isAuthenticated, login, logout, isLoading } = useAuth();
```

## 9. API Client Auth Handling

**Source:** `frontend/src/lib/api.ts`

The `apiFetch` function handles auth at the HTTP level:

- All requests include `credentials: "include"` for cross-origin cookie transmission.
- All requests include `Authorization: Bearer <token>` from localStorage (fallback when cookies are unavailable).
- On `401` response (for non-auth endpoints): Clears localStorage token and `fiboki_auth` marker cookie, then redirects to `/login` via `window.location.href`.
- Login uses `application/x-www-form-urlencoded` content type (OAuth2 form format).
- Requests abort after 10 seconds to prevent infinite loading screens.

## 10. Auth Flow Sequence

```
1. User visits any page
2. Middleware checks for fiboki_auth marker cookie
   - No cookie -> redirect to /login
   - Has cookie -> render page

3. AuthProvider mounts, calls GET /auth/me
   - Valid session -> populate user state
   - Invalid/expired -> user = null (API calls will 401 -> redirect)

4. User submits login form
   - POST /auth/login (form-encoded username + password)
   - Backend validates credentials
   - Backend sets fibokei_token cookie (HttpOnly, SameSite=None, Secure, 24h)
   - Backend returns { access_token, token_type } in response body
   - Frontend stores access_token in localStorage as fibokei_token
   - Frontend sets fiboki_auth=1 marker cookie (non-HttpOnly, path=/)
   - Frontend calls GET /auth/me -> populates user state
   - Frontend redirects to dashboard

5. User clicks logout
   - POST /auth/logout
   - Backend deletes fibokei_token HttpOnly cookie
   - Frontend removes fibokei_token from localStorage
   - Frontend clears fiboki_auth marker cookie
   - Frontend sets user = null
   - Redirect to /login
```

## 11. CORS Configuration

**Source:** `backend/src/fibokei/api/app.py`

```python
origins = ["http://localhost:3000"]
# Plus any origins in FIBOKEI_CORS_ORIGINS env var

CORSMiddleware(
    allow_origins=origins,
    allow_credentials=True,   # Required for cookie auth
    allow_methods=["*"],
    allow_headers=["*"],
)
```

`allow_credentials=True` is required for cross-origin cookie transmission between the Vercel frontend and the Railway/Render backend.

## 12. Security Considerations

- **HttpOnly cookies** prevent JavaScript access to the JWT token (XSS mitigation).
- **SameSite=None + Secure** in production (required for cross-origin cookie auth between `fiboki.uk` and `api.fiboki.uk`). CSRF protection relies on CORS origin checking rather than SameSite.
- **SameSite=Lax + Secure=false** in local dev (same-origin, no HTTPS needed).
- **JWT secret** must be set via `FIBOKEI_JWT_SECRET` env var -- the app raises `ValueError` if missing.
- **No plaintext passwords** -- all passwords stored as bcrypt hashes.
- **Bearer fallback** from localStorage ensures auth works even when cross-origin cookies are blocked by browser privacy settings.
- **Marker cookie (`fiboki_auth`)** is non-HttpOnly and contains no sensitive data (value is `"1"`). It exists solely to signal auth state to Next.js edge middleware.
