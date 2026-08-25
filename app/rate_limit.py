"""
Rate limiting for FastAPI using SlowAPI
"""
import os
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from fastapi import Request, Response
from fastapi.responses import JSONResponse

# Initialize limiter
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[
        "200 per day",
        "50 per hour",
        "10 per minute"
    ],
    storage_uri=os.environ.get(
        "RATE_LIMIT_STORAGE",
        "memory://"
    ),
)

# Custom rate limit exceeded handler
async def rate_limiter_exception_handler(
    request: Request,
    exc: RateLimitExceeded,
) -> Response:
    """Return custom response when rate limit exceeded"""
    return JSONResponse(
        status_code=429,
        content={
            "detail": "Rate limit exceeded",
            "message": "Too many requests. Please try again later.",
            "retry_after": 60,
        },
    )

# Apply limits to specific endpoints
@limiter.limit("5 per minute")
async def login_limit(request: Request):
    pass

@limiter.limit("30 per minute")
async def api_limit(request: Request):
    pass
