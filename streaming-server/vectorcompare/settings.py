import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")
print("BASE_DIR:", BASE_DIR)
print("ENV PATH:", BASE_DIR / ".env")
print("ENV EXISTS:", (BASE_DIR / ".env").exists())
print(
    "AWS credentials loaded:",
    bool(os.getenv("AWS_ACCESS_KEY_ID"))
)

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "dev-only-change-me")
DEBUG = os.getenv("DJANGO_DEBUG", "False").lower() == "true"
ALLOWED_HOSTS = [h.strip() for h in os.getenv(
    "DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost"
).split(",") if h.strip()]

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.staticfiles",
    "streaming",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
]

ROOT_URLCONF = "vectorcompare.urls"

TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [],
    "APP_DIRS": True,
    "OPTIONS": {
        "context_processors": [
            "django.template.context_processors.request",
        ],
    },
}]

WSGI_APPLICATION = "vectorcompare.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"

AWS_S3_BUCKET_NAME = os.getenv("AWS_S3_BUCKET_NAME", "")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")

CSRF_TRUSTED_ORIGINS = [
    "https://vector.compare",
]

#if not DEBUG:
#    SECURE_PROXY_SSL_HEADER = (
#        "HTTP_X_FORWARDED_PROTO",
#        "https",
#    )
#
#    SECURE_SSL_REDIRECT = True
#    SESSION_COOKIE_SECURE = True
#    CSRF_COOKIE_SECURE = True

HTTPS_ENABLED = (
    os.getenv("DJANGO_HTTPS_ENABLED", "False").lower() == "true"
)

if HTTPS_ENABLED:
    CSRF_TRUSTED_ORIGINS = [
        "https://vector.compare",
    ]

    SECURE_PROXY_SSL_HEADER = (
        "HTTP_X_FORWARDED_PROTO",
        "https",
    )

    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
else:
    CSRF_TRUSTED_ORIGINS = [
        "http://localhost:8080",
        "http://127.0.0.1:8080",
        "https://vector.compare",
    ]
