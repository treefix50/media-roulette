"""
Rate limiting configuration for DDoS protection
"""
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

def init_rate_limiter(app):
    """Initialize rate limiter with production-safe defaults"""
    
    limiter = Limiter(
        key_func=get_remote_address,
        app=app,
        default_limits=[
            "200 per day",
            "50 per hour",
            "10 per minute"
        ],
        storage_uri=os.environ.get(
            'RATE_LIMIT_STORAGE',
            "memory://"  # Use redis://localhost:6379 for production
        ),
        strategy="fixed-window"
    )
    
    # Define rate limits for specific endpoints
    @limiter.limit("5 per minute")
    def login_limit():
        pass  # Applied to login routes automatically
    
    @limiter.limit("30 per minute")
    def api_limit():
        pass  # Applied to API routes
    
    # Exempt health checks from rate limiting
    @limiter.exempt
    def exempt_endpoints():
        return [
            '/health',
            '/static/'
        ]
    
    return limiter
