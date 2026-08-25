"""
FastAPI Security Module for Media Roulette
JWT-based authentication with bcrypt password hashing.
"""
import os
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy import Column, Integer, String, Boolean, DateTime, create_engine
from sqlalchemy.orm import sessionmaker, Session, declarative_base
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ── Configuration ──────────────────────────────────────────────
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-change-me-immediately")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.environ.get("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@media-roulette.local")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "ChangeMeNow123!")

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/token", auto_error=False)

# ── Database Setup ─────────────────────────────────────────────
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///state/media_roulette.db")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
    echo=False,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class User(Base):
    """User model for authentication"""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True)
    is_admin = Column(Boolean, default=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    last_login = Column(DateTime, nullable=True)

Base.metadata.create_all(bind=engine)

def get_db():
    """Database session dependency"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

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

# ── Password Functions ─────────────────────────────────────────
def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

# ── User CRUD ──────────────────────────────────────────────────
def get_user_by_email(db: Session, email: str) -> Optional[User]:
    return db.query(User).filter(User.email == email).first()

def authenticate_user(db: Session, email: str, password: str) -> Optional[User]:
    user = get_user_by_email(db, email)
    if not user or not verify_password(password, user.hashed_password):
        return None
    return user

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

# ── Init: Create default admin user ────────────────────────────
def init_default_admin():
    """Create default admin user on startup if none exists"""
    db = SessionLocal()
    try:
        existing = get_user_by_email(db, ADMIN_EMAIL)
        if not existing:
            admin = User(
                email=ADMIN_EMAIL,
                hashed_password=get_password_hash(ADMIN_PASSWORD),
                is_active=True,
                is_admin=True,
            )
            db.add(admin)
            db.commit()
            logger.info(f"✓ Default admin created: {ADMIN_EMAIL}")
            logger.warning("⚠️  Change admin password immediately after first login!")
        else:
            logger.debug("Admin user already exists")
    except Exception as e:
        logger.error(f"Failed to create default admin: {e}")
        db.rollback()
    finally:
        db.close()

# ── Dependencies ───────────────────────────────────────────────
async def get_current_user(
    request: Request,
    token: Optional[str] = None,
    db: Session = Depends(get_db),
) -> Optional[User]:
    """Validate JWT token or session and return current user"""
    credential_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    # Try token from header first
    if token is None:
        # Try session
        user_id = request.session.get("user_id")
        if user_id:
            user = db.query(User).filter(User.id == user_id, User.is_active == True).first()
            if user:
                return user
        return None

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            raise credential_exception
        token_data = TokenData(email=email)
    except JWTError:
        raise credential_exception

    user = get_user_by_email(db, token_data.email)
    if user is None:
        raise credential_exception

    if not user.is_active:
        raise HTTPException(status_code=403, detail="User account inactive")

    return user

async def require_auth(
    current_user: Optional[User] = Depends(get_current_user),
) -> User:
    """Dependency to enforce authentication on protected routes"""
    if current_user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return current_user

async def require_admin(
    current_user: User = Depends(require_auth),
) -> User:
    """Dependency to enforce admin role"""
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
        )
    return current_user
