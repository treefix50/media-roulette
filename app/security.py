"""
Media Roulette - Authentication and security helpers.

Authentication is session-based.

The browser receives a signed HttpOnly session cookie from Starlette's
SessionMiddleware. No JWT is stored in localStorage or sessionStorage.

The application expects the following environment variables:

```
SECRET_KEY
ADMIN_EMAIL
ADMIN_PASSWORD
```

Optional:

```
ACCESS_TOKEN_EXPIRE_MINUTES
```

The legacy JWT-related configuration is intentionally not used by the
application anymore. The name ACCESS_TOKEN_EXPIRE_MINUTES is retained only
for backwards-compatible configuration handling and is not required for
session authentication.
"""

from **future** import annotations

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, HTTPException, Request, status
from passlib.context import CryptContext

from app.library import get_db

# ============================================================================

# PASSWORD HASHING

# ============================================================================

pwd_context = CryptContext(
schemes=["bcrypt"],
deprecated="auto",
)

# ============================================================================

# CONFIGURATION

# ============================================================================

ADMIN_EMAIL = os.getenv(
"ADMIN_EMAIL",
"",
).strip().lower()

ADMIN_PASSWORD = os.getenv(
"ADMIN_PASSWORD",
"",
)

if not ADMIN_EMAIL:
raise RuntimeError(
"ADMIN_EMAIL is not configured."
)

if not ADMIN_PASSWORD:
raise RuntimeError(
"ADMIN_PASSWORD is not configured."
)

if len(ADMIN_PASSWORD) < 12:
raise RuntimeError(
"ADMIN_PASSWORD must contain at least 12 characters."
)

# ============================================================================

# INTERNAL HELPERS

# ============================================================================

def _utc_now() -> str:
"""
Return the current UTC time as an ISO-8601 string.
"""

```
return datetime.now(timezone.utc).isoformat()
```

def _constant_time_equal(
left: str,
right: str,
) -> bool:
"""
Compare two strings in constant time.

```
This is used for values such as CSRF/session-related tokens where a
timing-safe comparison is appropriate.
"""

return hmac.compare_digest(
    left.encode("utf-8"),
    right.encode("utf-8"),
)
```

def _session_fingerprint(request: Request) -> str:
"""
Create a lightweight fingerprint for the current authenticated session.

```
The fingerprint is not used as the authentication credential itself.
It only gives us a stable value that can be stored in the session and
compared during the current browser session.

We deliberately do not store IP addresses or user-agent strings in the
database.
"""

user_agent = request.headers.get(
    "user-agent",
    "",
)

forwarded_for = request.headers.get(
    "x-forwarded-for",
    "",
)

raw = f"{user_agent}|{forwarded_for}"

return hashlib.sha256(
    raw.encode("utf-8")
).hexdigest()
```

# ============================================================================

# DATABASE HELPERS

# ============================================================================

def _ensure_users_table() -> None:
"""
Ensure the users table exists.

```
This keeps authentication initialization self-contained. The operation is
idempotent and safe to execute repeatedly.
"""

with get_db() as conn:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            is_admin INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )

    conn.commit()
```

def _find_user_by_email(
email: str,
) -> dict[str, Any] | None:
"""
Find a user by normalized email address.
"""

```
normalized_email = email.strip().lower()

with get_db() as conn:
    row = conn.execute(
        """
        SELECT
            id,
            email,
            password_hash,
            is_active,
            is_admin,
            created_at,
            updated_at
        FROM users
        WHERE email = ?
        LIMIT 1
        """,
        (normalized_email,),
    ).fetchone()

if row is None:
    return None

return dict(row)
```

def _find_user_by_id(
user_id: int,
) -> dict[str, Any] | None:
"""
Find a user by database ID.
"""

```
with get_db() as conn:
    row = conn.execute(
        """
        SELECT
            id,
            email,
            password_hash,
            is_active,
            is_admin,
            created_at,
            updated_at
        FROM users
        WHERE id = ?
        LIMIT 1
        """,
        (user_id,),
    ).fetchone()

if row is None:
    return None

return dict(row)
```

# ============================================================================

# PASSWORD FUNCTIONS

# ============================================================================

def hash_password(password: str) -> str:
"""
Hash a password using bcrypt.
"""

```
if not isinstance(password, str):
    raise TypeError("password must be a string")

if not password:
    raise ValueError("password must not be empty")

return pwd_context.hash(password)
```

def verify_password(
plain_password: str,
password_hash: str,
) -> bool:
"""
Verify a plaintext password against a stored bcrypt hash.

```
Invalid/malformed hashes are treated as authentication failures rather
than being allowed to crash the login endpoint.
"""

if not plain_password or not password_hash:
    return False

try:
    return pwd_context.verify(
        plain_password,
        password_hash,
    )
except Exception:
    return False
```

# ============================================================================

# ADMIN INITIALIZATION

# ============================================================================

def create_default_admin() -> None:
"""
Create the configured administrator if no user with that email exists.

```
This function does NOT overwrite an existing password.

It is intentionally idempotent so it can safely be called during
application startup.

ADMIN_PASSWORD therefore acts as an initial bootstrap credential only.
Once the admin account exists, changing ADMIN_PASSWORD in the environment
does not silently replace the existing database password.
"""

_ensure_users_table()

existing = _find_user_by_email(
    ADMIN_EMAIL,
)

if existing is not None:
    return

now = _utc_now()

password_hash = hash_password(
    ADMIN_PASSWORD,
)

with get_db() as conn:
    conn.execute(
        """
        INSERT INTO users (
            email,
            password_hash,
            is_active,
            is_admin,
            created_at,
            updated_at
        )
        VALUES (?, ?, 1, 1, ?, ?)
        """,
        (
            ADMIN_EMAIL,
            password_hash,
            now,
            now,
        ),
    )

    conn.commit()
```

# ============================================================================

# AUTHENTICATION

# ============================================================================

def authenticate_user(
email: str,
password: str,
) -> dict[str, Any] | None:
"""
Authenticate a user.

```
Returns the database user dictionary on success and None on failure.
"""

normalized_email = email.strip().lower()

if not normalized_email or not password:
    return None

user = _find_user_by_email(
    normalized_email,
)

if user is None:
    return None

if not bool(user["is_active"]):
    return None

if not verify_password(
    password,
    user["password_hash"],
):
    return None

return user
```

def create_session(
request: Request,
user: dict[str, Any],
) -> None:
"""
Initialize an authenticated server-side session.

```
Starlette's SessionMiddleware signs the session cookie. Sensitive
credentials are never stored in the browser's JavaScript storage.

Only the minimal identity information required by the application is
stored in the session.
"""

request.session.clear()

request.session["authenticated"] = True
request.session["user_id"] = int(user["id"])
request.session["email"] = str(user["email"])
request.session["is_admin"] = bool(user["is_admin"])
request.session["created_at"] = _utc_now()

# Session fingerprint is informational/defensive only. Authentication
# remains based on the signed session cookie and the active database user.
request.session["session_fingerprint"] = _session_fingerprint(
    request
)
```

def destroy_session(
request: Request,
) -> None:
"""
Destroy the current authenticated session.
"""

```
request.session.clear()
```

# ============================================================================

# CURRENT USER

# ============================================================================

def get_current_user(
request: Request,
) -> dict[str, Any]:
"""
Return the currently authenticated user.

```
Raises HTTP 401 if no valid authenticated session exists.
"""

authenticated = request.session.get(
    "authenticated",
    False,
)

user_id = request.session.get(
    "user_id",
)

if not authenticated or not user_id:
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required.",
        headers={
            "WWW-Authenticate": "Session",
        },
    )

try:
    user_id_int = int(user_id)
except (TypeError, ValueError):
    request.session.clear()

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid authentication session.",
        headers={
            "WWW-Authenticate": "Session",
        },
    )

user = _find_user_by_id(
    user_id_int,
)

if user is None:
    request.session.clear()

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="User no longer exists.",
        headers={
            "WWW-Authenticate": "Session",
        },
    )

if not bool(user["is_active"]):
    request.session.clear()

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="User account is inactive.",
        headers={
            "WWW-Authenticate": "Session",
        },
    )

return user
```

def get_optional_current_user(
request: Request,
) -> dict[str, Any] | None:
"""
Return the authenticated user if a valid session exists.

```
Unlike get_current_user(), this helper does not raise for anonymous
visitors.
"""

try:
    return get_current_user(request)
except HTTPException:
    return None
```

def require_auth(
user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
"""
FastAPI dependency requiring authentication.
"""

```
return user
```

def require_admin(
user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
"""
FastAPI dependency requiring an administrator account.
"""

```
if not bool(user.get("is_admin")):
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Administrator privileges required.",
    )

return user
```

# ============================================================================

# CSRF PROTECTION

# ============================================================================

def get_or_create_csrf_token(
request: Request,
) -> str:
"""
Return the CSRF token associated with the current session.

```
The token is random and stored inside the signed session. It is intended
for state-changing browser requests.
"""

token = request.session.get(
    "csrf_token",
)

if isinstance(token, str) and len(token) >= 32:
    return token

token = secrets.token_urlsafe(32)

request.session["csrf_token"] = token

return token
```

def validate_csrf_token(
request: Request,
supplied_token: str | None,
) -> None:
"""
Validate a CSRF token.

```
Raises HTTP 403 if the token is absent or invalid.
"""

expected_token = request.session.get(
    "csrf_token",
)

if not isinstance(expected_token, str):
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="CSRF validation failed.",
    )

if not supplied_token:
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="CSRF validation failed.",
    )

if not _constant_time_equal(
    expected_token,
    supplied_token,
):
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="CSRF validation failed.",
    )
```

# ============================================================================

# PUBLIC USER REPRESENTATION

# ============================================================================

def public_user(
user: dict[str, Any],
) -> dict[str, Any]:
"""
Return the safe subset of user information suitable for an API response.

```
Password hashes and other internal database fields are never exposed.
"""

return {
    "id": int(user["id"]),
    "email": str(user["email"]),
    "is_admin": bool(user["is_admin"]),
    "is_active": bool(user["is_active"]),
}
```

# ============================================================================

# INITIALIZATION

# ============================================================================

_ensure_users_table()

**all** = [
"ADMIN_EMAIL",
"ADMIN_PASSWORD",
"authenticate_user",
"create_default_admin",
"create_session",
"destroy_session",
"get_current_user",
"get_optional_current_user",
"get_or_create_csrf_token",
"hash_password",
"public_user",
"require_admin",
"require_auth",
"validate_csrf_token",
"verify_password",
]
