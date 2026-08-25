"""
Security module for Media Roulette
Provides user authentication, authorization, and session management
"""
from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from flask_security import Security, SQLAlchemyUserDatastore, UserMixin, RoleMixin
from sqlalchemy.orm import relationship
from werkzeug.security import generate_password_hash, check_password_hash
import secrets

# Association table for many-to-many relationship between users and roles
roles_users = db.Table('roles_users',
    db.Column('user_id', db.Integer(), db.ForeignKey('user.id')),
    db.Column('role_id', db.Integer(), db.ForeignKey('role.id'))
)

class Role(db.Model, RoleMixin):
    """User roles for access control"""
    __tablename__ = 'role'
    
    id = db.Column(db.Integer(), primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    description = db.Column(db.String(255))
    
    def __repr__(self):
        return f'<Role {self.name}>'

class User(db.Model, UserMixin):
    """Application user model"""
    __tablename__ = 'user'
    
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    active = db.Column(db.Boolean(), default=True, nullable=False)
    fs_uniquifier = db.Column(db.String(255), unique=True, nullable=False)
    confirmed_at = db.Column(db.DateTime())
    created_at = db.Column(db.DateTime(), default=datetime.utcnow)
    last_login_at = db.Column(db.DateTime())
    current_login_at = db.Column(db.DateTime())
    last_login_ip = db.Column(db.String(50))
    roles = relationship('Role', secondary=roles_users, 
                        backref=db.backref('users', lazy='dynamic'))
    
    def set_password(self, password):
        """Hash and set password"""
        self.password = generate_password_hash(password)
    
    def check_password(self, password):
        """Verify password against hash"""
        return check_password_hash(self.password, password)
    
    def __repr__(self):
        return f'<User {self.email}>'

def init_security(app, db):
    """Initialize Flask-Security with secure configuration"""
    from flask_security import Security
    
    # Create user datastore
    user_datastore = SQLAlchemyUserDatastore(db, User, Role)
    
    # Create default admin user if none exists
    with app.app_context():
        db.create_all()
        if not user_datastore.find_user(email='admin'):
            admin_role = user_datastore.find_or_create_role(name='admin')
            user_datastore.create_user(
                email=os.environ.get('ADMIN_EMAIL', 'admin@local'),
                password=generate_password_hash(os.environ.get('ADMIN_PASSWORD', 
                                    'ChangeMe123!Secure')),
                roles=[admin_role]
            )
            db.session.commit()
    
    # Secure security configuration
    app.config.update({
        # Security settings
        'SECURITY_PASSWORD_SALT': os.environ.get('SECURITY_PASSWORD_SALT', 
                                                 app.config.get('SECRET_KEY')),
        'SECURITY_USERNAME_ENABLE': False,
        'SECURITY_USERNAME_REQUIRED': False,
        'SECURITY_REGISTERABLE': False,
        'SECURITY_SEND_REGISTER_EMAIL': False,
        'SECURITY_LOGIN_USER_WITHOUT_VERIFICATION': True,
        'SECURITY_RETURN_DATA_AFTER_CONFIRMATION': True,
        'SECURITY_CHANGEABLE': True,
        'SECURITY_RESETABLE': True,
        'SECURITY_POST_LOGIN_VIEW': '/',
        'SECURITY_POST_LOGOUT_VIEW': '/',
        'SECURITY_POST_REGISTER_VIEW': '/',
        
        # Session security
        'PERMANENT_SESSION_LIFETIME': 3600,
        'SESSION_COOKIE_SECURE': app.config.get('FLASK_ENV') == 'production',
        'SESSION_COOKIE_HTTPONLY': True,
        'SESSION_COOKIE_SAMESITE': 'Lax',
        
        # Password requirements
        'SECURITY_PASSWORD_MIN_LENGTH': 8,
        'SECURITY_COMPLEXITY_RULES': [
            ('At least one uppercase letter', lambda p: any(c.isupper() for c in p)),
            ('At least one lowercase letter', lambda p: any(c.islower() for c in p)),
            ('At least one digit', lambda p: any(c.isdigit() for c in p)),
            ('At least one special character', lambda p: any(not c.isalnum() for c in p)),
        ]
    })
    
    # Initialize security
    security = Security(app, user_datastore)
    return security, user_datastore
