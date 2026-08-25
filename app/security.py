"""
FastAPI Security Module for Media Roulette
JWT-based authentication with bcrypt password hashing.
Uses sqlite3 directly for consistency with library.py
"""
import os
import sqlite3
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Dict, Any

from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordRequestForm

load_dotenv_required = __import__('dotenv')
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logger = logging.getLogger(__name__)

# ── Configuration ──────────────────────────────────────────────
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-change-me-immediately")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.environ.get("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@media-roulette.local")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "ChangeMeNow123!")

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# ── Database Setup - CONSISTENT WITH library.py ────────────────
DB_PATH = os.environ.get("DATABASE_PATH", "/state/media_roulette.db")

def get_db_connection() -> sqlite3.Connection:
    """Create database connection consistent with library.py"""
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    
    return conn

def init_user_table():
    """Initialize users table if not exists"""
    conn = get_db_connection()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                hashed_password TEXT NOT NULL,
                is_active BOOLEAN DEFAULT 1,
                is_admin BOOLEAN DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                last_login TEXT
            )
        """)
        conn.commit()
    finally:
        conn.close()

# ── Pydantic Models ────────────────────────────────────────────
class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    email: Optional[str] = None

class UserCreate(BaseModel):
    email: EmailStr
    password: str

class UserResponse(BaseModel):
    email: str
    is_active: bool
    is_admin: bool

    class Config:
        from_attributes = True

# Row type alias for SQLite
Row = Dict[str, Any]

# ── Password Functions ─────────────────────────────────────────
def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

# ── User CRUD ──────────────────────────────────────────────────
def get_user_by_email(email: str) -> Optional[Row]:
    """Get user by email from database"""
    conn = get_db_connection()
    try:
        cursor = conn.execute(
            "SELECT * FROM users WHERE email = ?",
            (email,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def authenticate_user(email: str, password: str) -> Optional[Row]:
    """Authenticate user with email/password"""
    user = get_user_by_email(email)
    if not user or not verify_password(password, user["hashed_password"]):
        return None
    return user

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def create_default_admin():
    """Create default admin user on startup if none exists"""
    init_user_table()
    conn = get_db_connection()
    try:
        cursor = conn.execute(
            "SELECT id FROM users WHERE email = ?",
            (ADMIN_EMAIL,)
        )
        existing = cursor.fetchone()
        
        if not existing:
            conn.execute(
                """
                INSERT INTO users (email, hashed_password, is_active, is_admin)
                VALUES (?, ?, ?, ?)
                """,
                (
                    ADMIN_EMAIL,
                    get_password_hash(ADMIN_PASSWORD),
                    1,
                    1
                )
            )
            conn.commit()
            logger.info(f"✓ Default admin created: {ADMIN_EMAIL}")
            logger.warning("⚠️  Change admin password immediately after first login!")
        else:
            logger.debug("Admin user already exists")
    except Exception as e:
        logger.error(f"Failed to create default admin: {e}")
        conn.rollback()
    finally:
        conn.close()

# ── Dependencies ───────────────────────────────────────────────
async def get_current_user(
    request: Request,
    token: Optional[str] = None,
) -> Optional[Row]:
    """Validate JWT token and return current user"""
    credential_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    # Try token from Authorization header
    if token is None:
        # Try session fallback
        user_id = request.session.get("user_id")
        if user_id:
            conn = get_db_connection()
            try:
                cursor = conn.execute(
                    "SELECT * FROM users WHERE id = ? AND is_active = 1",
                    (user_id,)
                )
                row = cursor.fetchone()
                if row:
                    return dict(row)
            finally:
                conn.close()
        return None

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            raise credential_exception
    except JWTError:
        raise credential_exception

    user = get_user_by_email(email)
    if user is None:
        raise credential_exception

    if not user.get("is_active"):
        raise HTTPException(status_code=403, detail="User account inactive")

    return user

async def require_auth(
    current_user: Optional[Row] = Depends(get_current_user),
) -> Row:
    """Dependency to enforce authentication on protected routes"""
    if current_user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return current_user

async def require_admin(
    current_user: Row = Depends(require_auth),
) -> Row:
    """Dependency to enforce admin role"""
    if not current_user.get("is_admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
        )
    return current_user
