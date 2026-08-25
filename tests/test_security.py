"""
Security tests for Media Roulette
Run with: pytest tests/ -v
"""
import pytest
from app.main import create_app
from app.security import init_security

@pytest.fixture
def app():
    """Create test application"""
    app = create_app()
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False
    yield app

def test_health_endpoint(app):
    """Test public health endpoint"""
    client = app.test_client()
    response = client.get('/health')
    assert response.status_code == 200
    assert b'status' in response.data

def test_protected_route_requires_auth(app):
    """Test that protected routes require authentication"""
    client = app.test_client()
    response = client.get('/roulette')
    assert response.status_code in [302, 401]  # Redirect or Unauthorized

def test_security_headers_present(app):
    """Test security headers are present"""
    client = app.test_client()
    response = client.get('/health')
    
    assert response.headers.get('X-Frame-Options') == 'SAMEORIGIN'
    assert response.headers.get('X-Content-Type-Options') == 'nosniff'
    assert response.headers.get('X-XSS-Protection') == '1; mode=block'

def test_invalid_input_blocked(app):
    """Test SQL injection patterns are blocked"""
    client = app.test_client()
    response = client.get('/api/random?id=1%20OR%201=1')
    assert response.status_code == 400
