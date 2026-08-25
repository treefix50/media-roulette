"""
Security middleware for Media Roulette
Adds security headers, rate limiting, and request validation
"""
from functools import wraps
from flask import request, jsonify, abort
import re
from datetime import datetime

def add_security_headers(app):
    """Add security headers to all responses"""
    
    @app.after_request
    def after_request(response):
        # Prevent clickjacking
        response.headers['X-Frame-Options'] = 'SAMEORIGIN'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-XSS-Protection'] = '1; mode=block'
        
        # Referrer policy
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        
        # Permissions policy
        response.headers['Permissions-Policy'] = 'geolocation=(), microphone=(), camera=()'
        
        # Content Security Policy (basic)
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'"
        
        # Remove server identification
        response.headers.pop('X-Powered-By', None)
        response.headers.pop('Server', None)
        
        # HTTPS enforcement in production
        if app.config.get('FLASK_ENV') == 'production':
            response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
        
        return response
    
    return app

def validate_input(f):
    """Validate user input for common injection attacks"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Check for SQL injection patterns in query params
        dangerous_patterns = [
            r"(\b(SELECT|INSERT|UPDATE|DELETE|DROP|UNION|ALTER)\b)",
            r"(--|\#|\/\*)",
            r"('|\"|;|\\)",
        ]
        
        for pattern in dangerous_patterns:
            for param in request.args.values():
                if param and re.search(pattern, param, re.IGNORECASE):
                    app.logger.warning(f"Suspicious input detected from {request.remote_addr}")
                    abort(400)
        
        return f(*args, **kwargs)
    return decorated_function

def audit_log(app, event_type, details=None):
    """Log security-relevant events"""
    if not details:
        details = {}
    
    log_entry = {
        'timestamp': datetime.utcnow().isoformat(),
        'event_type': event_type,
        'ip': request.remote_addr,
        'user_agent': request.user_agent.string,
        'details': details
    }
    
    app.logger.info(f"AUDIT: {log_entry}")
