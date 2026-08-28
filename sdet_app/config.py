import os
from dotenv import load_dotenv

load_dotenv()

basedir = os.path.dirname(os.path.abspath(__file__))


def _int(name, default):
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return int(default)


class Config:
    # Security / app
    SECRET_KEY = os.getenv("APP_SECRET_KEY", "change-me-in-production")

    # Database
    DATABASE_PATH = os.getenv("DATABASE_PATH", os.path.join(basedir, "qa.db"))

    # Admin bootstrap
    ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@example.com")
    ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")

    # OpenAI assistance (optional; engine works without it)
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")

    # Exploration limits
    MAX_PAGES = _int("MAX_PAGES", 50)
    PAGE_TIMEOUT = _int("PAGE_TIMEOUT", 30000)
    HEADLESS = os.getenv("HEADLESS", "false").lower() in ("1", "true", "yes")

    # Directories (relative to sdet_app/)
    STATIC_DIR = os.path.join(basedir, "static")
    REPORT_DIR = os.path.join(basedir, "reports")
    TEMPLATE_DIR = os.path.join(basedir, "templates")

    # Strictness default severity
    DEFAULT_FAIL_SEVERITY = "MAJOR"


class Env:
    """Convenient accessor so other modules do not import Config's static class
    only to read one value. Prefer from .config import env."""
    pass


env = Config()

# ---------------------------------------------------------------------------
# Runtime settings persisted in the DB ("Paramètres" UI) with .env fallback.
# Infra/secret values (SECRET_KEY, DB path, admin password, OPENAI_API_KEY)
# stay in .env on purpose - they are never exposed in the interface.
# ---------------------------------------------------------------------------

_SETTINGS = None


def get_setting(name, default=None):
    """Return a DB-stored setting, falling back to the supplied default."""
    global _SETTINGS
    if _SETTINGS is None:
        try:
            from . import database as _db
            _SETTINGS = _db.load_settings()
        except Exception:
            _SETTINGS = {}
    raw = _SETTINGS.get(name)
    if raw is None or raw == "":
        return default
    return raw


def apply_settings(values):
    """Persist UI-edited settings and refresh the in-memory cache."""
    global _SETTINGS
    from . import database as _db
    data = {str(k): str(v) for k, v in values.items()}
    _db.save_settings(data)
    if _SETTINGS is None:
        _SETTINGS = {}
    _SETTINGS.update(data)


def reset_settings(keys):
    """Remove specific settings, restoring .env defaults."""
    global _SETTINGS
    from . import database as _db
    _db.delete_settings([str(k) for k in keys])
    if _SETTINGS is not None:
        for k in keys:
            _SETTINGS.pop(str(k), None)


def setting_int(name, default):
    try:
        return int(get_setting(name, default))
    except (TypeError, ValueError):
        return int(default)


def setting_bool(name, default):
    v = get_setting(name, default)
    if isinstance(v, bool):
        return v
    return str(v).lower() in ("1", "true", "yes", "on")
