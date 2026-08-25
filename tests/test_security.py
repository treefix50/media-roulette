"""
Security tests for Media Roulette
Run with: pytest tests/ -v
"""
import pytest
import httpx
from fastapi.testclient import TestClient
from app.main import app

@pytest.fixture
def client():
    """Create test client"""
    return TestClient(app)

def test_health_endpoint(client):
    """Test public health endpoint"""
    response = client.get('/health')
    assert response.status_code == 200
    assert response.json()['status'] == 'healthy'

def test_protected_route_requires_auth(client):
    """Test that protected routes require authentication"""
    response = client.get('/api/random')
    assert response.status_code == 401  # Unauthorized

def test_security_headers_present(client):
    """Test security headers are present"""
    response = client.get('/health')
    
    assert response.headers.get('X-Frame-Options') == 'SAMEORIGIN'
    assert response.headers.get('X-Content-Type-Options') == 'nosniff'
    assert response.headers.get('X-XSS-Protection') == '1; mode=block'
