"""
Media Roulette - Main Application Entry Point
With Security Module Integration
"""
import os
import logging
from flask import Flask, redirect, url_for
from flask_sqlalchemy import SQLAlchemy

# Initialize extensions
db = SQLAlchemy()

def create_app():
    """Application factory for Flask"""
    app = Flask(__name__)
    
    # ==================== CONFIGURATION ====================
    # Load from environment with secure defaults
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 
                               os.urandom(32).hex())
    app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get(
        'DATABASE_URL', 'sqlite:///state/media_roulette.db')
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['FLASK_ENV'] = os.environ.get('FLASK_ENV', 'development')
    
    # Security configuration will be overridden by security module
    app.config['SESSION_COOKIE_SECURE'] = app.config['FLASK_ENV'] == 'production'
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    
    # CORS settings for reverse proxy
    app.config['SERVER_NAME'] = os.environ.get('SERVER_NAME', None)
    app.config['PREFERRED_URL_SCHEME'] = os.environ.get('URL_SCHEME', 'https')
    
    # ==================== INITIALIZATION ====================
    # Initialize database
    db.init_app(app)
    
    # Import and initialize security
    from app.security import init_security
    security, user_datastore = init_security(app, db)
    
    # Add route protection decorator
    from flask_security.utils import login_required
    
    # ==================== LOGGING ====================
    if app.config['FLASK_ENV'] == 'production':
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler('/state/app.log'),
                logging.StreamHandler()
            ]
        )
    
    # ==================== ROUTES ====================
    @app.route('/')
    def index():
        """Home page with redirect to authenticated areas"""
        return redirect(url_for('login'))
    
    @app.route('/health')
    def health_check():
        """Health check endpoint for load balancers/proxies"""
        from datetime import datetime
        return {
            'status': 'healthy',
            'timestamp': datetime.utcnow().isoformat(),
            'version': '1.0.0',
            'authenticated': False
        }, 200
    
    # Import routes after app creation
    from app.routes import register_routes
    register_routes(app, db, user_datastore)
    
    return app

# Create application instance
app = create_app()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8000))
    debug = os.environ.get('FLASK_ENV', 'development') != 'production'
    app.run(host='0.0.0.0', port=port, debug=debug)
