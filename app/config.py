import os
from datetime import timedelta

class Config:
    # Secret Key
    SECRET_KEY = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-production')
    
    # Database Configuration
    DB_HOST = os.getenv('DB_HOST', 'localhost')
    DB_NAME = os.getenv('DB_NAME', 'radius')
    DB_USER = os.getenv('DB_USER', 'radius')
    DB_PASSWORD = os.getenv('DB_PASSWORD', 'radiuspass')
    DB_PORT = os.getenv('DB_PORT', '5432')
    
    # FIX: Session Configuration
    SESSION_TYPE = 'redis'
    SESSION_PERMANENT = True
    PERMANENT_SESSION_LIFETIME = timedelta(days=7)
    SESSION_COOKIE_SECURE = os.getenv('SESSION_COOKIE_SECURE', 'False').lower() == 'true'
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    # Redis configuration for Session storage
    REDIS_URL = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
    from redis import Redis
    SESSION_REDIS = Redis.from_url(REDIS_URL)
    
    # Flask Configuration
    FLASK_ENV = os.getenv('FLASK_ENV', 'development')
    DEBUG = os.getenv('DEBUG', 'False').lower() == 'true'
    TESTING = False
    
    # Upload Configuration
    MAX_CONTENT_LENGTH = 2 * 1024 * 1024  # 2MB

    # Backup Configuration
    BACKUP_FOLDER = '/app/backups'
    MAX_BACKUP_SIZE = 100 * 1024 * 1024  # 100MB
    ALLOWED_BACKUP_EXTENSIONS = {'sql', 'backup'}
    
    # Redis Configuration
    REDIS_PASSWORD = os.getenv('REDIS_PASSWORD', '')
    REDIS_URL = os.getenv('REDIS_URL', 'redis://localhost:6379/0')

    # Telegram Notification Configuration
    TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
    TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '')
