# Fiboki Authentication Specification

## 1. Overview

Fiboki uses cookie-based JWT authentication. The backend issues an HttpOnly cookie on login. The frontend never reads or stores the token directly -- all API calls use `credentials: "include"` to let the browser handle cookie transmission.

## 2. Cookie Configuration

Set on successful `POST /api/v1/auth/login`:

| Property | Value |
|----------|-------|
| Key | `fibokei_token` |
| HttpOnly | `true` |
| SameSite | `Lax` |
| Secure | `false` (dev) -- must be `true` in production |
| max_age | `86400` (24 hours) |

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

Next.js middleware runs server-side on every request (except static assets and API routes). It checks for the `fibokei_token` cookie:

- **No cookie + not on `/login`:** Redirect to `/login`.
- **Has cookie + on `/login`:** Redirect to `/` (dashboard).
- **Otherwise:** Pass through.

```typescript
export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|api).*)"],
};
```

**Note:** The middleware only checks cookie presence, not validity. JWT validation happens server-side on each API call.

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
2. **login():** POSTs form-encoded credentials to `/auth/login`. On success, calls `/auth/me` to populate user state. Returns `true` on success, `false` on failure.
3. **logout():** POSTs to `/auth/logout` (clears cookie server-side). Sets `user = null` locally.
4. **No token storage:** The frontend never reads or stores the JWT. Cookie management is handled entirely by the browser.

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

- All requests include `credentials: "include"`.
- On `401` response (for non-auth endpoints): Redirects to `/login` via `window.location.href`.
- Login uses `application/x-www-form-urlencoded` content type (OAuth2 form format).

## 10. Auth Flow Sequence

```
1. User visits any page
2. Middleware checks for fibokei_token cookie
   - No cookie -> redirect to /login
   - Has cookie -> render page

3. AuthProvider mounts, calls GET /auth/me
   - Valid cookie -> populate user state
   - Invalid/expired cookie -> user = null (API calls will 401 -> redirect)

4. User submits login form
   - POST /auth/login (form-encoded username + password)
   - Backend validates credentials
   - Backend sets fibokei_token cookie (HttpOnly, 24h)
   - Frontend calls GET /auth/me -> populates user state
   - Frontend redirects to dashboard

5. User clicks logout
   - POST /auth/logout
   - Backend deletes fibokei_token cookie
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

- **HttpOnly cookies** prevent JavaScript access to the token (XSS mitigation).
- **SameSite=Lax** provides CSRF protection for state-changing requests.
- **Secure flag** must be set to `true` in production (currently `false` for local dev).
- **JWT secret** must be set via `FIBOKEI_JWT_SECRET` env var -- the app raises `ValueError` if missing.
- **No plaintext passwords** -- all passwords stored as bcrypt hashes.
- **Bearer fallback** retained for API testing tools (Swagger UI, curl).
