"""
Media Roulette - authentication and session security.

Authentication model:

```
Browser
    |
  Zoraxy
    |
FastAPI
    |
signed session cookie
```

Passwords are stored as Argon2id password hashes.

The browser session contains only the minimum information required by the
application. Passwords and password hashes are never placed into the session.

CSRF protection is implemented using a random per-session token and
constant-time comparison.
"""

from **future** import annotations

import hashlib
import hmac
import logging
import os
import secrets
from typing import Any

from fastapi import HTTPException, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.library import get_db

logger = logging.getLogger(
"media_roulette.security"
)

# ============================================================================

# CONFIGURATION

# ============================================================================

SESSION_USER_KEY = "user_id"
SESSION_EMAIL_KEY = "email"
SESSION_AUTHENTICATED_KEY = "authenticated"
SESSION_CSRF_KEY = "csrf_token"

CSRF_TOKEN_BYTES = 32

# ============================================================================

# OPTIONAL BASIC-AUTH COMPATIBILITY

# ============================================================================

# Kept available for backwards compatibility with older imports.

#

# The application itself uses browser sessions, not HTTP Basic Auth.

basic_security = HTTPBasic(
auto_error=False
)

# ============================================================================

# PASSWORD HASHING

# ============================================================================

def _argon2_hasher():
"""
Create the Argon2 password hasher lazily.

```
Importing this module therefore does not immediately fail if Argon2 is
unavailable. Authentication itself will provide a clear error.
"""

try:
    from argon2 import PasswordHasher

    return PasswordHasher(
        time_cost=3,
        memory_cost=65536,
        parallelism=2,
        hash_len=32,
        salt_len=16,
    )

except ImportError as exc:
    raise RuntimeError(
        "The 'argon2-cffi' package is required for authentication."
    ) from exc
```

def hash_password(
password: str,
) -> str:
"""
Hash a password using Argon2id.
"""

```
if not isinstance(
    password,
    str,
):
    raise TypeError(
        "password must be a string."
    )

if not password:
    raise ValueError(
        "password must not be empty."
    )

hasher = _argon2_hasher()

return hasher.hash(
    password
)
```

def verify_password(
password: str,
password_hash: str,
) -> bool:
"""
Verify a password against an Argon2id hash.

```
Returns False for malformed hashes instead of leaking implementation
details to the caller.
"""

if not password:
    return False

if not password_hash:
    return False

try:
    hasher = _argon2_hasher()

    return bool(
        hasher.verify(
            password_hash,
            password,
        )
    )

except Exception:
    return False
```

def password_needs_rehash(
password_hash: str,
) -> bool:
"""
Return whether the stored Argon2 hash should be regenerated.
"""

```
if not password_hash:
    return True

try:
    hasher = _argon2_hasher()

    return bool(
        hasher.check_needs_rehash(
            password_hash
        )
    )

except Exception:
    return False
```

# ============================================================================

# USER DATABASE

# ============================================================================

def _ensure_users_table() -> None:
"""
Create the application users table if it does not already exist.
"""

```
with get_db() as conn:

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )

    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_users_email
        ON users(email)
        """
    )
```

_ensure_users_table()

def _normalize_email(
email: str,
) -> str:
"""
Normalize an email address for account lookup.

```
Email local-part case sensitivity is technically possible, but practically
most application accounts treat email addresses case-insensitively. Media
Roulette follows that common behavior.
"""

value = (
    email
    or ""
).strip().casefold()

if not value:
    raise ValueError(
        "Email address must not be empty."
    )

if len(value) > 320:
    raise ValueError(
        "Email address is too long."
    )

return value
```

def get_user_by_email(
email: str,
) -> dict[str, Any] | None:
"""
Retrieve one user by normalized email address.
"""

```
normalized = _normalize_email(
    email
)

with get_db() as conn:

    row = conn.execute(
        """
        SELECT
            id,
            email,
            password_hash,
            is_active,
            created_at,
            updated_at
        FROM users
        WHERE email = ?
        LIMIT 1
        """,
        (
            normalized,
        ),
    ).fetchone()

return (
    dict(row)
    if row
    else None
)
```

def get_user_by_id(
user_id: int,
) -> dict[str, Any] | None:
"""
Retrieve one user by numeric ID.
"""

```
if user_id <= 0:
    return None

with get_db() as conn:

    row = conn.execute(
        """
        SELECT
            id,
            email,
            password_hash,
            is_active,
            created_at,
            updated_at
        FROM users
        WHERE id = ?
        LIMIT 1
        """,
        (
            user_id,
        ),
    ).fetchone()

return (
    dict(row)
    if row
    else None
)
```

def create_user(
email: str,
password: str,
) -> int:
"""
Create a new local application user.

```
Returns the numeric user ID.
"""

normalized = _normalize_email(
    email
)

if len(password) < 12:
    raise ValueError(
        "Password must contain at least 12 characters."
    )

password_hash = hash_password(
    password
)

from datetime import datetime, timezone

now = datetime.now(
    timezone.utc
).isoformat()

with get_db() as conn:

    cursor = conn.execute(
        """
        INSERT INTO users (
            email,
            password_hash,
            is_active,
            created_at,
            updated_at
        )
        VALUES (
            ?, ?, 1, ?, ?
        )
        """,
        (
            normalized,
            password_hash,
            now,
            now,
        ),
    )

    return int(
        cursor.lastrowid
    )
```

def update_password(
user_id: int,
password: str,
) -> None:
"""
Replace a user's password hash.
"""

```
if user_id <= 0:
    raise ValueError(
        "Invalid user ID."
    )

if len(password) < 12:
    raise ValueError(
        "Password must contain at least 12 characters."
    )

password_hash = hash_password(
    password
)

from datetime import datetime, timezone

now = datetime.now(
    timezone.utc
).isoformat()

with get_db() as conn:

    conn.execute(
        """
        UPDATE users
        SET
            password_hash = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            password_hash,
            now,
            user_id,
        ),
    )
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
Returns the database user record on success and None on failure.

A dummy Argon2 verification is performed when the email does not exist.
This makes username enumeration somewhat harder by keeping the expensive
password-hashing operation present for both paths.
"""

normalized = _normalize_email(
    email
)

user = get_user_by_email(
    normalized
)

if user is None:

    # Deliberately perform an Argon2 verification against a static,
    # intentionally invalid account hash if possible.
    #
    # The value is only a timing-equalization mechanism and does not
    # represent an actual user password.
    dummy_hash = (
        "$argon2id$v=19$m=65536,t=3,p=2$"
        "c2FsdHNhbHQ$"
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    )

    verify_password(
        password,
        dummy_hash,
    )

    return None

if not bool(
    user.get(
        "is_active",
        0,
    )
):
    return None

if not verify_password(
    password,
    str(
        user.get(
            "password_hash",
            "",
        )
    ),
):
    return None

# Transparently upgrade old Argon2 parameters.
if password_needs_rehash(
    str(
        user["password_hash"]
    )
):
    try:
        update_password(
            int(
                user["id"]
            ),
            password,
        )

        refreshed = get_user_by_id(
            int(
                user["id"]
            )
        )

        if refreshed:
            user = refreshed

    except Exception:
        logger.exception(
            "Could not rehash password for user %s",
            user.get("id"),
        )

return user
```

# ============================================================================

# PUBLIC USER DATA

# ============================================================================

def public_user(
user: dict[str, Any] | None,
) -> dict[str, Any] | None:
"""
Return only safe user fields for API responses.
"""

```
if not user:
    return None

return {
    "id": int(
        user["id"]
    ),
    "email": str(
        user["email"]
    ),
    "is_active": bool(
        user.get(
            "is_active",
            1,
        )
    ),
}
```

# ============================================================================

# SESSION

# ============================================================================

def create_session(
request: Request,
user: dict[str, Any],
) -> None:
"""
Create a minimal authenticated browser session.

```
Existing session contents are discarded before authentication information
is written, preventing session fixation.
"""

user_id = int(
    user["id"]
)

email = str(
    user["email"]
)

request.session.clear()

request.session[
    SESSION_AUTHENTICATED_KEY
] = True

request.session[
    SESSION_USER_KEY
] = user_id

request.session[
    SESSION_EMAIL_KEY
] = email

request.session[
    SESSION_CSRF_KEY
] = secrets.token_urlsafe(
    CSRF_TOKEN_BYTES
)
```

def destroy_session(
request: Request,
) -> None:
"""
Destroy the current browser session.
"""

```
request.session.clear()
```

def is_authenticated(
request: Request,
) -> bool:
"""
Check whether the request contains an authenticated session.
"""

```
return bool(
    request.session.get(
        SESSION_AUTHENTICATED_KEY,
        False,
    )
)
```

def get_current_user(
request: Request,
) -> dict[str, Any] | None:
"""
Resolve the current session user from the database.

```
This deliberately does not trust the email stored in the cookie.
"""

if not is_authenticated(
    request
):
    return None

raw_user_id = request.session.get(
    SESSION_USER_KEY
)

try:
    user_id = int(
        raw_user_id
    )
except (
    TypeError,
    ValueError,
):
    destroy_session(
        request
    )
    return None

user = get_user_by_id(
    user_id
)

if not user or not bool(
    user.get(
        "is_active",
        0,
    )
):
    destroy_session(
        request
    )
    return None

return user
```

# ============================================================================

# FASTAPI AUTH DEPENDENCY

# ============================================================================

def require_auth(
request: Request,
) -> dict[str, Any]:
"""
Require an authenticated session.
"""

```
user = get_current_user(
    request
)

if user is None:
    raise HTTPException(
        status_code=401,
        detail="Authentication required.",
    )

return user
```

# ============================================================================

# CSRF

# ============================================================================

def get_or_create_csrf_token(
request: Request,
) -> str:
"""
Return the current CSRF token or create one for this session.
"""

```
token = request.session.get(
    SESSION_CSRF_KEY
)

if (
    isinstance(token, str)
    and len(token) >= 32
):
    return token

token = secrets.token_urlsafe(
    CSRF_TOKEN_BYTES
)

request.session[
    SESSION_CSRF_KEY
] = token

return token
```

def validate_csrf_token(
request: Request,
submitted_token: str | None,
) -> None:
"""
Validate a submitted CSRF token.

```
Comparison is performed with hmac.compare_digest().
"""

expected = request.session.get(
    SESSION_CSRF_KEY
)

if not isinstance(
    expected,
    str,
):
    raise HTTPException(
        status_code=403,
        detail="Invalid CSRF token.",
    )

if not isinstance(
    submitted_token,
    str,
):
    raise HTTPException(
        status_code=403,
        detail="Invalid CSRF token.",
    )

# Hashing both values also makes the comparison operate on fixed-size
# values, avoiding accidental type/length differences.
expected_digest = hashlib.sha256(
    expected.encode(
        "utf-8"
    )
).digest()

submitted_digest = hashlib.sha256(
    submitted_token.encode(
        "utf-8"
    )
).digest()

if not hmac.compare_digest(
    expected_digest,
    submitted_digest,
):
    raise HTTPException(
        status_code=403,
        detail="Invalid CSRF token.",
    )
```

# ============================================================================

# LEGACY COMPATIBILITY HELPERS

# ============================================================================

def get_current_username(
request: Request,
) -> str | None:
"""
Backwards-compatible helper.

```
New code should use get_current_user().
"""

user = get_current_user(
    request
)

if not user:
    return None

return str(
    user["email"]
)
```

def verify_session(
request: Request,
) -> bool:
"""
Backwards-compatible boolean session check.
"""

```
return (
    get_current_user(
        request
    )
    is not None
)
```

# ============================================================================

# EXPORTS

# ============================================================================

**all** = [
"SESSION_AUTHENTICATED_KEY",
"SESSION_CSRF_KEY",
"SESSION_EMAIL_KEY",
"SESSION_USER_KEY",
"authenticate_user",
"basic_security",
"create_session",
"create_user",
"destroy_session",
"get_current_user",
"get_current_user_info",
"get_current_username",
"get_or_create_csrf_token",
"get_user_by_email",
"get_user_by_id",
"hash_password",
"is_authenticated",
"password_needs_rehash",
"public_user",
"require_auth",
"update_password",
"validate_csrf_token",
"verify_password",
"verify_session",
]
