"""
Django settings for PESOPay project.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# ============================================================
# CORE PATHS
# ============================================================
BASE_DIR = Path(__file__).resolve().parent.parent


# ============================================================
# SECURITY
# ============================================================
SECRET_KEY = os.getenv('SECRET_KEY')

DEBUG = os.getenv('DEBUG', 'False') == 'True'

ALLOWED_HOSTS = os.getenv('ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',')

# Prevent session cookie from being accessed by JavaScript
SESSION_COOKIE_HTTPONLY = True

# Prevent CSRF cookie from being accessed by JavaScript
CSRF_COOKIE_HTTPONLY = True

# Prevent clickjacking attacks
X_FRAME_OPTIONS = 'DENY'

# Enable browser XSS protection header
SECURE_BROWSER_XSS_FILTER = True

# Prevent browsers from guessing content types (MIME sniffing)
SECURE_CONTENT_TYPE_NOSNIFF = True

# --- Production-only security settings ---
# These activate automatically when DEBUG=False (i.e. in production)
# They do nothing in local development so they are always safe to include
if not DEBUG:
    SECURE_SSL_REDIRECT = True               # Force all traffic to HTTPS
    SESSION_COOKIE_SECURE = True             # Only send session cookie over HTTPS
    CSRF_COOKIE_SECURE = True                # Only send CSRF cookie over HTTPS
    SECURE_HSTS_SECONDS = 31536000           # Tell browsers to only use HTTPS for 1 year
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True    # Apply HSTS to all subdomains too
    SECURE_HSTS_PRELOAD = True               # Allow browser preload list inclusion


# ============================================================
# PAYSTACK
# ============================================================
PAYSTACK_SECRET_KEY = os.getenv('PAYSTACK_SECRET_KEY')
PAYSTACK_PUBLIC_KEY = os.getenv('PAYSTACK_PUBLIC_KEY')
PAYSTACK_CALLBACK_URL = os.getenv('PAYSTACK_CALLBACK_URL')


# ============================================================
# AUTHENTICATION
# ============================================================
AUTH_USER_MODEL = 'users.User'

LOGIN_URL = 'users:login'
LOGIN_REDIRECT_URL = 'users:dashboard'
LOGOUT_REDIRECT_URL = 'users:login'


# ============================================================
# APPLICATIONS
# ============================================================
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    # Project apps
    'core.apps.CoreConfig',
    'tenants.apps.TenantsConfig',
    'users.apps.UsersConfig',
    'transactions.apps.TransactionsConfig',
    'products.apps.ProductsConfig',
    'orders.apps.OrdersConfig',
    'cart.apps.CartConfig',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]


# ============================================================
# URLS & TEMPLATES
# ============================================================
ROOT_URLCONF = 'PESOPay.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'PESOPay.wsgi.application'


# ============================================================
# DATABASE
# ============================================================
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.getenv('DB_NAME', 'payfusion'),
        'USER': os.getenv('DB_USER', 'payfusion_user'),
        'PASSWORD': os.getenv('DB_PASSWORD'),
        'HOST': os.getenv('DB_HOST', 'localhost'),
        'PORT': os.getenv('DB_PORT', '5432'),
        
        'CONN_MAX_AGE': 60,  # Reuse DB connections for 60 seconds (performance)
    }
}


# ============================================================
# PASSWORD VALIDATION
# ============================================================
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
        'OPTIONS': {'min_length': 8}
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# ============================================================
# INTERNATIONALISATION
# ============================================================
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Africa/Lagos'   # Changed from UTC — your platform is Nigeria-based
USE_I18N = True
USE_TZ = True


# ============================================================
# STATIC FILES
# ============================================================
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'  # Where collectstatic puts files for production


# ============================================================
# MEDIA FILES (for product images — Phase 10+)
# ============================================================
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'mediafiles'


# ============================================================
# CART
# ============================================================
CART_SESSION_ID = 'cart'


# ============================================================
# DEFAULT PRIMARY KEY
# ============================================================
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


# ============================================================
# LOGGING
# Writes errors to console in dev, will write to file in production
# ============================================================
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '[{levelname}] {asctime} {module} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'loggers': {
        'django': {
            'handlers': ['console'],
            'level': 'WARNING',
            'propagate': True,
        },
        # PayFusion app-level logger — use logging.getLogger('payfusion') anywhere
        'payfusion': {
            'handlers': ['console'],
            'level': 'DEBUG' if DEBUG else 'INFO',
            'propagate': False,
        },
    },
}