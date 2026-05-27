"""config.py — Environment loading, runtime configuration."""
from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

from dotenv import load_dotenv

_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    load_dotenv(_env_file, override=True)

# ── Runtime flags ─────────────────────────────────────────────────────────────
TESTNET: bool = os.getenv("HL_TESTNET", "true").lower() in ("true", "1", "yes")
DB_PATH: str  = os.getenv("DB_PATH", str(Path(__file__).parent / "database" / "carry.db"))

# ── Credentials ───────────────────────────────────────────────────────────────
HL_PRIVATE_KEY: str = os.getenv("HL_PRIVATE_KEY", "")

# ── Telegram ──────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN:   str = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

# ── Capital overrides (env can override constants.py) ─────────────────────────
import core.constants as C

if os.getenv("ACTIVE_CAPITAL_USD"):
    C.ACTIVE_CAPITAL_USD = float(os.getenv("ACTIVE_CAPITAL_USD"))  # type: ignore
if os.getenv("POSITION_SIZE_USD"):
    C.POSITION_SIZE_USD = float(os.getenv("POSITION_SIZE_USD"))    # type: ignore
if os.getenv("MAX_POSITIONS"):
    C.MAX_POSITIONS = int(os.getenv("MAX_POSITIONS"))              # type: ignore


def check_required_env() -> None:
    """Halt if mandatory env vars are missing."""
    missing = []
    if not HL_PRIVATE_KEY:
        missing.append("HL_PRIVATE_KEY")
    if missing:
        print(f"[config] FATAL: Missing required env vars: {', '.join(missing)}")
        print("[config] Copy .env.example → .env and fill in values.")
        sys.exit(1)


def crash_log(context: str, exc: Exception, tb: str = "") -> None:
    from core.logger import log_err
    log_err(f"CRASH [{context}]: {type(exc).__name__}: {exc}")
    if tb:
        print(tb, file=sys.stderr)
    else:
        traceback.print_exc(file=sys.stderr)
