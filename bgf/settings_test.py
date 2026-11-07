import os

os.environ.setdefault("DJANGO_DEBUG", "1")

from .settings import *  # noqa: E402, F403

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
SITE_URL = "http://testserver"
