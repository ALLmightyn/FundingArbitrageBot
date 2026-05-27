"""core/logger.py — Coloured console logging, no external dependencies."""
from __future__ import annotations

import io
import sys
from datetime import datetime

# Force UTF-8 output on Windows so Unicode symbols don't crash on cp1251
if sys.platform == "win32":
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except AttributeError:
        pass  # already wrapped or running in pytest


class _C:
    RESET  = "\033[0m"
    CYAN   = "\033[96m"
    YELLOW = "\033[93m"
    RED    = "\033[91m"
    GREEN  = "\033[92m"
    BOLD   = "\033[1m"


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def log(msg: str) -> None:
    print(f"{_C.CYAN}[{_ts()}] [CARRY] {msg}{_C.RESET}", flush=True)


def log_ok(msg: str) -> None:
    print(f"{_C.GREEN}[{_ts()}] [CARRY] OK {msg}{_C.RESET}", flush=True)


def log_warn(msg: str) -> None:
    print(f"{_C.YELLOW}[{_ts()}] [CARRY] WARN {msg}{_C.RESET}", flush=True)


def log_err(msg: str) -> None:
    print(f"{_C.RED}[{_ts()}] [CARRY] ERR {msg}{_C.RESET}", flush=True)


def crash_log(context: str, exc: Exception, tb: str = "") -> None:
    log_err(f"CRASH in {context}: {type(exc).__name__}: {exc}")
    if tb:
        print(tb, file=sys.stderr)
