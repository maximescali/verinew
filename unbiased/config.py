"""Settings from the environment. A .env file next to the package (code/.env) is loaded first if present."""
import os
from pathlib import Path

_env = Path(__file__).resolve().parent.parent / ".env"
if _env.exists():
    for line in _env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

# Aiven Runtime injects DATABASE_URL when the Postgres service is connected. Empty = local SQLite file.
DATABASE_URL = os.environ.get("DATABASE_URL", "")
# Postgres schema for the tables, so they do not collide with other apps in the same database. Empty = public.
DB_SCHEMA = os.environ.get("DB_SCHEMA", "verinew")
SQLITE_PATH = os.environ.get("SQLITE_PATH", str(Path(__file__).resolve().parent.parent / "unbiased.sqlite"))
KEENABLE_API_KEY = os.environ.get("KEENABLE_API_KEY", "")

QUESTION_MODEL = os.environ.get("QUESTION_MODEL", "claude-opus-5-5")
SOURCE_MODEL = os.environ.get("SOURCE_MODEL", "claude-opus-5-5")
EXTRACT_MODEL = os.environ.get("EXTRACT_MODEL", "claude-sonnet-5-5")

WORKERS = int(os.environ.get("AGENT_WORKERS", "6"))
MAX_QUESTIONS = int(os.environ.get("MAX_QUESTIONS", "7"))
MAX_SOURCES_PER_QUESTION = int(os.environ.get("MAX_SOURCES_PER_QUESTION", "4"))
MAX_TURNS = int(os.environ.get("AGENT_MAX_TURNS", "12"))
FETCH_MAX_CHARS = int(os.environ.get("FETCH_MAX_CHARS", "12000"))  # page text handed to an agent; most of the token cost
