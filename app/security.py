"""
Media Roulette - authentication and session security.

Authentication is based on a signed Starlette session cookie.

The session contains only:
- authenticated flag
- numeric user id
- email for convenience
- CSRF token

Passwords are never stored in the session.

Passwords are hashed with Argon2id.

The authentication database is kept in the same SQLite database configured
through DATABASE_PATH. This deliberately does not depend on a get_db()
helper in library.py.
"""

from **future** import annotations

import hashlib
import hmac
import logging
import os
import secrets
import sqlite3
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, Request
from fastapi.security import HTTPBasic

logger = logging.getLogger(
"media_roulette.security"
)

# ============================================================================

# CONFIGURATION

# ============================================================================

DATABASE_PATH = os.getenv(
"DATABASE_PATH",
"/state/media_roulette.db",
).strip()

SESSION_USER_KEY = "user_id"
SESSION_EMAIL_KEY = "email"
SESSION_AUTHENTICATED_KEY = "authenticated"
SESSION_CSRF_KEY = "csrf_token"

CSRF_TOKEN_BYTES = 32

MIN_PASSWORD_LENGTH = 12

# ============================================================================

# OPTIONAL BASIC AUTH COMPATIBILITY

# ============================================================================

basic_security = HTTPBasic(
auto_error=False,
)

# ============================================================================

# DATABASE

# ============================================================================

def _connect() -> sqlite3.Connection:
"""
Open the authentication database.

```
The same SQLite database is used by Library for media metadata.
"""

database = os.path.abspath(
    DATABASE_PATH
)

parent = os.path.dirname(
    database
)

if parent:
    os.makedirs(
        parent,
        exist_ok=True,
    )

conn = sqlite3.connect(
    database,
    timeout=30,
)

conn.row_factory = sqlite3.Row

conn.execute(
    "PRAGMA busy_timeout=30000"
)

conn.execute(
    "PRAGMA foreign_keys=ON"
)

conn.execute(
    "PRAGMA journal_mode=WAL"
)

conn.execute(
    "PRAGMA synchronous=NORMAL"
)

return conn
```

def _ensure_users_table() -> None:
"""
Create the users table when necessary.

```
Existing databases are preserved.
"""

with _connect() as conn:

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

try:
    path = os.path.abspath(
        DATABASE_PATH
    )

    if os.path.exists(path):
        os.chmod(
            path,
            0o600,
        )

except OSError:
    logger.warning(
        "Unable to restrict database permissions: %s",
        DATABASE_PATH,
    )
```

# ============================================================================

# ARGON2

# ============================================================================

def _argon2_hasher():
"""
Return the configured Argon2 password hasher.
"""

```
try:
    from argon2 import PasswordHasher

except ImportError as exc:
    raise RuntimeError(
        "argon2-cffi is required for Media Roulette authentication."
    ) from exc

return PasswordHasher(
    time_cost=3,
    memory_cost=65536,
    parallelism=2,
    hash_len=32,
    salt_len=16,
)
```

def hash_password(
password: str,
) -> str:
"""
Hash a password with Argon2id.
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

if len(password) < MIN_PASSWORD_LENGTH:
    raise ValueError(
        f"password must contain at least "
        f"{MIN_PASSWORD_LENGTH} characters."
    )

return _argon2_hasher().hash(
    password
)
```

def verify_password(
password: str,
password_hash: str,
) -> bool:
"""
Verify a password against an Argon2 hash.

```
Invalid hashes are treated as authentication failures.
"""

if not isinstance(
    password,
    str,
):
    return False

if not isinstance(
    password_hash,
    str,
):
    return False

if not password:
    return False

if not password_hash:
    return False

try:
    return bool(
        _argon2_hasher().verify(
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
Determine whether an Argon2 hash should be upgraded.
"""

```
if not password_hash:
    return True

try:
    return bool(
        _argon2_hasher().check_needs_rehash(
            password_hash
        )
    )

except Exception:
    return False
```

# ============================================================================

# USER NORMALIZATION

# ============================================================================

def _normalize_email(
email: str,
) -> str:
"""
Normalize an application email identifier.
"""

```
if not isinstance(
    email,
    str,
):
    raise ValueError(
        "Invalid email address."
    )

value = email.strip().casefold()

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

# ============================================================================

# USER ACCESS

# ============================================================================

def get_user_by_email(
email: str,
) -> dict[str, Any] | None:
"""
Load a user by email.
"""

```
normalized = _normalize_email(
    email
)

with _connect() as conn:

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

if row is None:
    return None

return dict(row)
```

def get_user_by_id(
user_id: int,
) -> dict[str, Any] | None:
"""
Load a user by numeric ID.
"""

```
try:
    user_id = int(
        user_id
    )

except (
    TypeError,
    ValueError,
):
    return None

if user_id <= 0:
    return None

with _connect() as conn:

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

if row is None:
    return None

return dict(row)
```

# ============================================================================

# USER CREATION / PASSWORD MANAGEMENT

# ============================================================================

def create_user(
email: str,
password: str,
) -> int:
"""
Create a local application user.
"""

```
normalized = _normalize_email(
    email
)

if len(password) < MIN_PASSWORD_LENGTH:
    raise ValueError(
        f"Password must contain at least "
        f"{MIN_PASSWORD_LENGTH} characters."
    )

password_hash = hash_password(
    password
)

now = datetime.now(
    timezone.utc
).isoformat()

with _connect() as conn:

    cursor = conn.execute(
        """
        INSERT INTO users (
            email,
            password_hash,
            is_active,
            created_at,
            updated_at
        )
        VALUES (?, ?, 1, ?, ?)
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
Change a user's password.
"""

```
if len(password) < MIN_PASSWORD_LENGTH:
    raise ValueError(
        f"Password must contain at least "
        f"{MIN_PASSWORD_LENGTH} characters."
    )

password_hash = hash_password(
    password
)

now = datetime.now(
    timezone.utc
).isoformat()

with _connect() as conn:

    cursor = conn.execute(
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
            int(user_id),
        ),
    )

    if cursor.rowcount == 0:
        raise ValueError(
            "User does not exist."
        )
```

# ============================================================================

# AUTHENTICATION

# ============================================================================

# A valid Argon2id hash used only for timing equalization when a user does

# not exist. The password can never successfully authenticate against it.

_DUMMY_PASSWORD_HASH = (
"$argon2id$v=19$m=65536,t=3,p=2$"
"c2FsdHNhbHQxMjM0NTY3OA$"
"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
)

def authenticate_user(
email: str,
password: str,
) -> dict[str, Any] | None:
"""
Authenticate one user.

```
Returns the database record on success.
Returns None on every authentication failure.
"""

normalized = _normalize_email(
    email
)

user = get_user_by_email(
    normalized
)

if user is None:

    # Perform an expensive password verification even when the account
    # does not exist to reduce timing differences.
    verify_password(
        password,
        _DUMMY_PASSWORD_HASH,
    )

    return None

if not bool(
    user.get(
        "is_active",
        0,
    )
):
    return None

password_hash = str(
    user.get(
        "password_hash",
        "",
    )
)

if not verify_password(
    password,
    password_hash,
):
    return None

if password_needs_rehash(
    password_hash
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

        if refreshed is not None:
            user = refreshed

    except Exception:
        logger.exception(
            "Unable to transparently rehash password for user %s",
            user.get(
                "id"
            ),
        )

return user
```

# ============================================================================

# PUBLIC USER REPRESENTATION

# ============================================================================

def public_user(
user: dict[str, Any] | None,
) -> dict[str, Any] | None:
"""
Return safe user data for API responses.

```
Password hashes are never exposed.
"""

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
            0,
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
Create a fresh authenticated session.

```
Existing session state is removed to prevent session fixation.
"""

request.session.clear()

request.session[
    SESSION_AUTHENTICATED_KEY
] = True

request.session[
    SESSION_USER_KEY
] = int(
    user["id"]
)

request.session[
    SESSION_EMAIL_KEY
] = str(
    user["email"]
)

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
Remove all session data.
"""

```
request.session.clear()
```

def is_authenticated(
request: Request,
) -> bool:
"""
Check the session authentication marker.
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
Resolve the authenticated user from the database.

```
The session's user ID is treated only as an identifier. The database
remains authoritative for account state.
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

if user is None:
    destroy_session(
        request
    )
    return None

if not bool(
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

def require_auth(
request: Request,
) -> dict[str, Any]:
"""
FastAPI dependency requiring an authenticated session.
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
Return the current session CSRF token.
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
A missing or invalid token always results in HTTP 403.
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

# COMPATIBILITY HELPERS

# ============================================================================

def get_current_username(
request: Request,
) -> str | None:
"""
Compatibility helper for older code.
"""

```
user = get_current_user(
    request
)

if user is None:
    return None

return str(
    user["email"]
)
```

def verify_session(
request: Request,
) -> bool:
"""
Compatibility helper returning only the authentication state.
"""

```
return (
    get_current_user(
        request
    )
    is not None
)
```

def get_current_user_info(
request: Request,
) -> dict[str, Any] | None:
"""
Compatibility helper returning safe user information.
"""

```
return public_user(
    get_current_user(
        request
    )
)
```

# ============================================================================

# INITIALIZATION

# ============================================================================

_ensure_users_table()

# ============================================================================

# EXPORTS

# ============================================================================

**all** = [
"DATABASE_PATH",
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
