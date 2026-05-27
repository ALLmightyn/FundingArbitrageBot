"""main_cross.py — Cross-venue (HL + Lighter) funding arbitrage bot entry point.

Architecture:
  carry_loop()
    ├── SpreadScanner.run_forever()      [asyncio.Task — scans HL+Lighter spread]
    ├── HLClient.ws_listen(userFundings) [asyncio.Task — HL funding payment accounting]
    └── strategy.tick()                  [every TICK_INTERVAL_S seconds]

Prerequisites:
  1. Install Lighter SDK (required for order placement):
       uv pip install git+https://github.com/elliottech/zklighter-perps-python.git
  2. Set in .env:
       LIGHTER_ACCOUNT_INDEX, LIGHTER_L1_ADDRESS,
       LIGHTER_API_KEY_INDEX, LIGHTER_API_PRIVATE_KEY

Circuit breakers:
  CB1: API error surge (>5 errors / 60s) → halt
  CB2: Session loss floor → halt

IMPORTANT: Bot will not place any orders until you START it explicitly.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
import traceback
import uuid
from collections import deque
from datetime import datetime
from typing import Optional

import config  # loads .env
from config import (
    HL_PRIVATE_KEY, TESTNET,
    TELEGRAM_TOKEN, TELEGRAM_CHAT_ID,
    check_required_env, crash_log,
)
from core.constants import (
    CROSS_POSITION_SIZE_USD, CROSS_MAX_POSITIONS, SPREAD_SCAN_INTERVAL_S,
    DRIFT_CHECK_INTERVAL_S,
)
from core.logger import log, log_warn, log_err
from core.models import SessionStats
from ui.dashboard import Dashboard
from database.db import init_db, get_db
from feeds.spread_scanner import SpreadScanner
from notifier.telegram import TelegramNotifier
from notifier.tg_bot import TelegramBot
from strategies.cross_venue_carry import CrossVenueStrategy
from venues.hyperliquid import HLClient
from venues.lighter import LighterClient

# ── Runtime constants ─────────────────────────────────────────────────────────
TICK_INTERVAL_S: int   = 30        # strategy tick frequency
REPORT_INTERVAL_S: int = 1800      # Telegram report every 30 min
SESSION_LOSS_FLOOR: float = -6.0   # Hard stop if session P&L < this  ← TEST (was -15)

# ── Global API error tracking (CB1) ──────────────────────────────────────────
_api_errors: deque = deque(maxlen=60)


def _get_lighter_config() -> tuple[str, Optional[int], Optional[int], Optional[str]]:
    """Read Lighter credentials from environment. Fails loudly if missing for trading."""
    import os
    l1_address    = os.getenv("LIGHTER_L1_ADDRESS", "").strip()
    acct_index    = os.getenv("LIGHTER_ACCOUNT_INDEX", "").strip()
    api_key_index = os.getenv("LIGHTER_API_KEY_INDEX", "").strip()
    api_priv_key  = os.getenv("LIGHTER_API_PRIVATE_KEY", "").strip()

    if not l1_address:
        raise ValueError("LIGHTER_L1_ADDRESS not set in .env")

    return (
        l1_address,
        int(acct_index)    if acct_index    else None,
        int(api_key_index) if api_key_index else None,
        api_priv_key       if api_priv_key  else None,
    )


def _fire(coro, name: str) -> asyncio.Task:
    async def _wrapper():
        try:
            await coro
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log_err(f"Background task '{name}' crashed: {e}")
            traceback.print_exc()
    return asyncio.create_task(_wrapper(), name=name)


async def carry_loop() -> None:
    """Main async loop for cross-venue funding arb."""
    session_id = str(uuid.uuid4())
    started_at = int(time.time())

    # ── Live dashboard ────────────────────────────────────────────────────────
    dash = Dashboard()
    dash.start_live()

    def dlog(msg: str, level: str = "info") -> None:
        """Unified log: goes to dashboard AND stdout logger."""
        dash.log(msg, level)
        {"info": log, "ok": log, "warn": log_warn, "error": log_err}.get(level, log)(msg)

    dlog(f"CrossVenueBot starting | session={session_id[:8]} | testnet={TESTNET}")

    # ── Init DB ───────────────────────────────────────────────────────────────
    await init_db()
    async with get_db() as conn:
        await conn.execute(
            "INSERT INTO sessions (id, started_at, starting_capital) VALUES (?,?,?)",
            (session_id, started_at, CROSS_POSITION_SIZE_USD * CROSS_MAX_POSITIONS * 2),
        )
        await conn.commit()

    # ── Init components ───────────────────────────────────────────────────────
    hl_client = HLClient(HL_PRIVATE_KEY, testnet=TESTNET)

    l1_address, acct_index, api_key_index, api_priv_key = _get_lighter_config()
    lt_client = LighterClient(
        l1_address=l1_address,
        account_index=acct_index,
        api_key_index=api_key_index,
        api_private_key=api_priv_key,
    )

    notifier = TelegramNotifier(TELEGRAM_TOKEN, TELEGRAM_CHAT_ID)
    stats    = SessionStats()
    scanner  = SpreadScanner(hl_client, lt_client)
    strategy = CrossVenueStrategy(hl_client, lt_client, scanner, stats, notifier)

    # TG interactive bot (commands: /status /pos /spreads /history /help)
    tg_bot = TelegramBot(
        token=TELEGRAM_TOKEN, chat_id=TELEGRAM_CHAT_ID,
        stats_ref=stats, positions_ref=strategy.positions,
        scanner_ref=scanner,
        db_path=os.getenv("DB_PATH", "database/carry.db"),
        started_at=started_at,
    )
    _fire(tg_bot.run_forever(), "tg_bot")

    # Link dashboard to live state
    dash.stats     = stats
    dash.positions = strategy.positions

    # ── Pre-load Lighter market metadata ─────────────────────────────────────
    dlog("Warming up: loading Lighter market metadata...")
    await lt_client._ensure_markets()

    # ── Initial scan ─────────────────────────────────────────────────────────
    dlog("Warm-up: running initial spread scan...")
    candidates = await scanner.scan_once()
    dash.opportunities = candidates
    dlog(f"Warm-up complete — {len(candidates)} spread opportunities found", "ok")

    # ── Verify balances (read-only check) ────────────────────────────────────
    try:
        dash.hl_balance = await hl_client.get_usdc_balance()
        dlog(f"HL balance: ${dash.hl_balance:.2f} USDC (unified)", "ok")
    except Exception as e:
        dlog(f"HL balance check failed: {e}", "warn")
    try:
        lt_balance = await lt_client.get_available_balance()
        dash.lt_balance = lt_balance
        dlog(f"Lighter balance: ${lt_balance:.2f} USDC available", "ok")
        if lt_balance < CROSS_POSITION_SIZE_USD * 0.5:
            dlog(
                f"Lighter balance ${lt_balance:.2f} may be insufficient for "
                f"${CROSS_POSITION_SIZE_USD:.0f} positions. Deposit USDC to Lighter.", "warn"
            )
    except Exception as e:
        dlog(f"Lighter account check failed (may be OK in monitoring-only mode): {e}", "warn")

    # ── WebSocket: listen for HL funding payments ─────────────────────────────
    hl_address = hl_client._address

    async def _on_ws_message(msg: dict) -> None:
        channel = msg.get("channel", "")
        data    = msg.get("data", {})
        if channel == "userFundings":
            fundings = data.get("fundings", []) if isinstance(data, dict) else []
            for f in fundings:
                asset  = f.get("coin", "")
                amount = float(f.get("usdc", 0))
                if asset and amount != 0:
                    await strategy.record_hl_funding(asset, amount)

    ws_subs = [
        {"type": "userFundings", "user": hl_address},
        {"type": "userEvents",   "user": hl_address},
    ]
    _fire(hl_client.ws_listen(ws_subs, _on_ws_message), "hl_ws_funding")

    # ── Background tasks ──────────────────────────────────────────────────────
    _fire(scanner.run_forever(), "spread_scanner")

    # ── Start notification ────────────────────────────────────────────────────
    await notifier.send_alert(
        f"🚀 <b>CrossVenueBot STARTED</b>\n"
        f"Session: <code>{session_id[:8]}</code>\n"
        f"Mode: HL ↔ Lighter funding arb\n"
        f"Testnet: {TESTNET}\n"
        f"Leg size: ${CROSS_POSITION_SIZE_USD:.0f} | Max positions: {CROSS_MAX_POSITIONS}\n"
        f"Initial candidates: {len(candidates)}"
    )

    dash.bot_status    = "RUNNING"
    dash.refresh()

    last_report      = int(time.time())
    last_lt_funding  = int(time.time())
    last_drift_check = int(time.time())
    last_bal_check   = int(time.time())
    LT_FUNDING_POLL  = 300  # poll Lighter funding every 5 min
    BAL_POLL         = 60   # refresh balances for dashboard every 60s

    try:
        while True:
            now = int(time.time())

            # ── CB1: API error surge ──────────────────────────────────────────
            recent_errors = sum(1 for t in _api_errors if now - t < 60)
            if recent_errors > 5:
                dash.bot_status = "HALTED"
                dlog(f"CB1: {recent_errors} API errors in 60s — HALTING", "error")
                await notifier.send_alert(
                    f"🚨 <b>CB1: API Error Surge</b>\n"
                    f"{recent_errors} errors/60s — CrossVenueBot HALTED"
                )
                break

            # ── CB2: Session loss floor ───────────────────────────────────────
            if stats.total_pnl_usd < SESSION_LOSS_FLOOR:
                dash.bot_status = "HALTED"
                dlog(f"CB2: session P&L ${stats.total_pnl_usd:.2f} < floor — HALTING", "error")
                await notifier.send_alert(
                    f"🛑 <b>CB2: Session loss floor hit</b>\n"
                    f"P&L: ${stats.total_pnl_usd:.2f} (floor: ${SESSION_LOSS_FLOOR:.2f})\n"
                    f"CrossVenueBot HALTED."
                )
                break

            # ── Strategy tick ─────────────────────────────────────────────────
            try:
                await strategy.tick()
                dash.portfolio_killed = strategy._portfolio_killed
            except Exception as e:
                _api_errors.append(now)
                crash_log("carry_loop.strategy.tick", e, traceback.format_exc())
                dlog(f"tick error: {type(e).__name__}: {e}", "error")

            # ── Poll Lighter funding ──────────────────────────────────────────
            if now - last_lt_funding >= LT_FUNDING_POLL:
                try:
                    await strategy.poll_lighter_funding()
                except Exception:
                    pass
                last_lt_funding = now

            # ── Position drift reconciliation ─────────────────────────────────
            if now - last_drift_check >= DRIFT_CHECK_INTERVAL_S:
                try:
                    await strategy.check_position_drift()
                except Exception as e:
                    crash_log("carry_loop.drift_check", e, traceback.format_exc())
                last_drift_check = now

            # ── Refresh dashboard balances & opportunities ────────────────────
            if now - last_bal_check >= BAL_POLL:
                try:
                    dash.hl_balance    = await hl_client.get_usdc_balance()
                    dash.lt_balance    = await lt_client.get_available_balance()
                    dash.opportunities = scanner.ranked[:5]
                except Exception:
                    pass
                last_bal_check = now

            # ── Periodic Telegram report ──────────────────────────────────────
            if now - last_report >= REPORT_INTERVAL_S:
                await notifier.send_report(stats)
                last_report = now
                open_pos = [a for a, p in strategy.positions.items() if p.state.value == "HOLD"]
                dlog(
                    f"REPORT | cycles={stats.total_cycles} "
                    f"pnl=${stats.total_pnl_usd:+.4f} open={open_pos}"
                )

            # Keep dashboard fresh every tick
            dash.refresh()
            await asyncio.sleep(TICK_INTERVAL_S)

    except asyncio.CancelledError:
        dlog("carry_loop cancelled — shutting down", "warn")
    except Exception as e:
        crash_log("carry_loop.fatal", e, traceback.format_exc())
        raise
    finally:
        # Gracefully close all open positions
        for asset, pos in list(strategy.positions.items()):
            try:
                await strategy._close_position(asset, pos, reason="shutdown")
            except Exception:
                pass

        await lt_client.close()

        # Update session record
        try:
            async with get_db() as conn:
                await conn.execute(
                    """UPDATE sessions SET
                       ended_at=?, total_cycles=?, successful_cycles=?,
                       total_funding_usd=?, total_fees_usd=?, total_pnl_usd=?
                       WHERE id=?""",
                    (
                        int(time.time()),
                        stats.total_cycles, stats.successful_cycles,
                        stats.total_funding_usd, stats.total_fees_usd, stats.total_pnl_usd,
                        session_id,
                    ),
                )
                await conn.commit()
        except Exception:
            pass

        dlog(f"Session {session_id[:8]} ended | P&L=${stats.total_pnl_usd:+.4f}", "ok")
        dash.stop()
        await notifier.send_report(stats)


async def main() -> None:
    check_required_env()
    await carry_loop()


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("Stopped by user")
