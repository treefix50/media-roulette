"""
Media Roulette - FastAPI Application Entry Point
Secure production-ready configuration
"""
import os
import logging
from pathlib import Path
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.security import get_current_user, User
from app.api.routes import router as api_router
from app.rate_limit import limiter, rate_limiter_exception_handler
from app.middleware import add_security_headers_fastapi

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('/state/app.log')
    ]
)

logger = logging.getLogger(__name__)

# Global templates instance for routes module
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "templates"))

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events"""
    # Startup
    logger.info("Starting Media Roulette...")
    
    # Initialize security and create default admin
    from app.security import init_default_admin
    init_default_admin()
    
    # Initialize library scan on startup
    from app.api.routes import library
    try:
        count = library.scan()
        logger.info(f"Initial library scan complete: {count} items")
    except Exception as e:
        logger.error(f"Initial library scan failed: {e}")
    
    yield
    
    # Shutdown
    logger.info("Shutting down Media Roulette...")


app = FastAPI(
    title="Media Roulette",
    description="Local random media recommendation service",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None,  # Disable Swagger in production
    redoc_url=None,
)

# Security middleware
app.state.limiter = limiter

# Add session support (for template rendering)
secret_key = os.environ.get("SECRET_KEY", os.urandom(32).hex())
app.add_middleware(SessionMiddleware, secret_key=secret_key)

# Add trusted host middleware in production
trusted_hosts = os.environ.get("TRUSTED_HOSTS", "*")
if trusted_hosts != "*" and trusted_hosts:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=trusted_hosts.split(","))

# Add security headers middleware
app = add_security_headers_fastapi(app)

# Rate limiting exception handler
app.add_exception_handler(RateLimitExceeded, rate_limiter_exception_handler)

# Include API router
app.include_router(api_router)

# Mount static files
BASE_DIR = Path(__file__).resolve().parents[1]
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.get("/", response_class=HTMLResponse, tags=["UI"])
async def root(request: Request):
    """Main page"""
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/health", tags=["Health"])
async def health_check():
    """Public health check endpoint"""
    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": "1.0.0",
        "service": "media-roulette"
    }


@app.get("/login", response_class=HTMLResponse, tags=["Auth"])
async def login_page(request: Request):
    """Login page"""
    return templates.TemplateResponse("login.html", {"request": request})


@app.post("/logout", tags=["Auth"])
async def logout(request: Request):
    """Logout - clears session"""
    request.session.pop("user_id", None)
    request.session.pop("token", None)
    return RedirectResponse(url="/login", status_code=303)


# Export templates for other modules
__all__ = ['app', 'templates']


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    host = os.environ.get("HOST", "0.0.0.0")
    debug = os.environ.get("DEBUG", "false").lower() == "true"
    
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=debug,
        workers=2 if not debug else 1,
    )
