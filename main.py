"""main.py — HLCarryBot entry point.

Architecture:
  carry_loop() (main)
    ├── FundingScanner.run_forever()        [asyncio.Task]
    ├── DeltaMonitor.run_forever()          [asyncio.Task]
    ├── HLClient.ws_listen(userFundings)    [asyncio.Task — funding payment accounting]
    └── strategy.tick()                     [every 30s]

Circuit breakers:
  CB1: API error surge (>5 errors / 60s) → halt
  CB2: Kill-switch (IronDome) → pause ticks
  CB3: Session loss floor ($-75 net) → halt and alert
"""
from __future__ import annotations

import asyncio
import sys
import time
import traceback
import uuid
from collections import deque
from datetime import datetime
from typing import Dict

import config  # loads .env
from config import (
    HL_PRIVATE_KEY, TESTNET,
    TELEGRAM_TOKEN, TELEGRAM_CHAT_ID,
    check_required_env, crash_log,
)
from core.constants import ACTIVE_CAPITAL_USD, MAX_POSITIONS
from core.logger import log, log_warn, log_err
from core.models import SessionStats
from database.db import init_db, get_db
from feeds.funding_scanner import FundingScanner
from notifier.telegram import TelegramNotifier
from risk.delta_monitor import DeltaMonitor
from risk.iron_dome import IronDome
from strategies.funding_carry import CarryStrategy
from venues.hyperliquid import HLClient

# ── Runtime constants ─────────────────────────────────────────────────────────
TICK_INTERVAL_S: int    = 30       # strategy tick frequency
REPORT_INTERVAL_S: int  = 1800     # Telegram report every 30 min
SESSION_LOSS_FLOOR: float = -8.0   # Hard stop if session P&L < this ($50 account)

# ── Global API error tracking (CB1) ──────────────────────────────────────────
_api_errors: deque = deque(maxlen=60)


async def _recover_state(client: HLClient, strategy: CarryStrategy) -> None:
    """Reconstruct strategy.positions from on-chain state at startup.
    For each open perp position with a corresponding spot balance + DB cycle,
    rebuild a CarryPosition in HOLD state.
    """
    from core.models import CarryPosition, CarryState
    try:
        ch_state = await client.get_clearinghouse()
        spot_balances = await client.get_spot_balances()
        spot_held: Dict[str, float] = {b["coin"]: float(b.get("total", 0)) for b in spot_balances}

        async with get_db() as conn:
            cur = await conn.execute(
                "SELECT id, asset, entered_at, spot_size_usd, perp_size_usd, "
                "perp_leverage, spot_entry, perp_entry, entry_funding_rate, "
                "funding_collected, fee_paid FROM funding_cycles WHERE state='OPEN'"
            )
            open_rows = await cur.fetchall()

        recovered = 0
        for row in open_rows:
            cycle_id, asset, entered_at, spot_usd, perp_usd, lev, spot_px, perp_px, fund_rate, fund_coll, fee = row
            # Find perp position
            perp_pos = None
            for ap in ch_state.get("assetPositions", []):
                p = ap.get("position", {})
                if p.get("coin") == asset and abs(float(p.get("szi", 0))) > 0:
                    perp_pos = p
                    break
            if perp_pos is None:
                log_warn(f"Recover: cycle {cycle_id[:8]} {asset} — no live perp position, marking CLOSED")
                async with get_db() as conn:
                    await conn.execute(
                        "UPDATE funding_cycles SET state='ABANDONED', exit_reason='not_on_chain' WHERE id=?",
                        (cycle_id,),
                    )
                    await conn.commit()
                continue

            spot_pair = await client.get_spot_pair(asset)
            units = abs(float(perp_pos.get("szi", 0)))
            pos = CarryPosition(
                asset=asset,
                state=CarryState.HOLD,
                spot_size_usd=spot_usd,
                perp_size_usd=perp_usd,
                perp_leverage=int(lev or 3),
                spot_entry_price=float(spot_px or 0),
                perp_entry_price=float(perp_px or 0),
                entered_at=int(entered_at),
                funding_collected_usd=float(fund_coll or 0),
                fee_paid_usd=float(fee or 0),
                entry_funding_rate=float(fund_rate or 0),
                cycle_id=cycle_id,
                units=units,
                spot_pair=spot_pair,
            )
            strategy.positions[asset] = pos
            recovered += 1
            log(f"Recover: {asset} HOLD | units={units:.6f} perp@{perp_px:.4f} spot@{spot_px:.4f} "
                f"funding=${fund_coll:.4f} fees=${fee:.4f}")

        if recovered:
            log(f"State recovery: restored {recovered} HOLD position(s)")
        else:
            log("State recovery: no open positions to restore")
    except Exception as e:
        crash_log("recover_state", e)


async def reconcile_with_exchange(client: HLClient, strategy: CarryStrategy, notifier=None) -> None:
    """
    Called after _recover_state(). Finds perp positions on exchange that have no
    matching entry in strategy.positions and emergency-closes them (taker reduce-only).
    Prevents "ghost shorts" from accumulating across restarts.
    """
    try:
        ch_state = await client.get_clearinghouse()
        orphans = []
        for ap in ch_state.get("assetPositions", []):
            p = ap.get("position", {})
            asset = p.get("coin", "")
            szi = float(p.get("szi", 0) or 0)
            if abs(szi) < 1e-9:
                continue
            if asset in strategy.positions:
                continue
            orphans.append((asset, szi, p))

        if not orphans:
            log("Reconcile: no orphan positions — exchange state matches strategy")
            return

        log_warn(f"Reconcile: {len(orphans)} orphan perp position(s) found — emergency closing")
        for asset, szi, p in orphans:
            is_short = szi < 0
            size = abs(szi)
            entry_px = float(p.get("entryPx", 0) or 0)
            log_warn(f"Reconcile: orphan {asset} szi={szi:.6f} ({'short' if is_short else 'long'}) — closing taker")
            try:
                book = await client.get_l2_book(asset)
                levels = book.get("levels", [[], []])
                if is_short:
                    raw_px = float(levels[1][0]["px"]) * 1.005 if levels[1] else entry_px * 1.01
                    px = client.round_price(raw_px)
                    await client.place_taker(asset, is_buy=True, size=size, price=px, reduce_only=True)
                else:
                    raw_px = float(levels[0][0]["px"]) * 0.995 if levels[0] else entry_px * 0.99
                    px = client.round_price(raw_px)
                    await client.place_taker(asset, is_buy=False, size=size, price=px, reduce_only=True)
                log(f"Reconcile: emergency close order sent for orphan {asset} @ {px:.4f}")
            except Exception as e:
                crash_log(f"reconcile.{asset}", e)

        if notifier and orphans:
            assets_str = ", ".join(a for a, _, _ in orphans)
            await notifier.send_alert(
                f"⚠️ <b>Reconcile: {len(orphans)} orphan position(s) closed</b>\n"
                f"Assets: {assets_str}\nAction: emergency taker close"
            )
    except Exception as e:
        crash_log("reconcile_with_exchange", e)


def _fire(coro, name: str) -> asyncio.Task:
    """Launch a background coroutine and log if it dies unexpectedly."""
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
    """Main async loop."""
    session_id = str(uuid.uuid4())
    started_at = int(time.time())
    log(f"HLCarryBot starting | session={session_id[:8]} | testnet={TESTNET}")

    # ── Init DB ───────────────────────────────────────────────────────────────
    await init_db()
    async with get_db() as conn:
        await conn.execute(
            "INSERT INTO sessions (id, started_at, starting_capital) VALUES (?,?,?)",
            (session_id, started_at, ACTIVE_CAPITAL_USD),
        )
        await conn.commit()

    # ── Init components ───────────────────────────────────────────────────────
    client   = HLClient(HL_PRIVATE_KEY, testnet=TESTNET)
    notifier = TelegramNotifier(TELEGRAM_TOKEN, TELEGRAM_CHAT_ID)
    stats    = SessionStats()
    scanner  = FundingScanner(client)
    dome     = IronDome(client, scanner, stats, notifier)
    monitor  = DeltaMonitor(client, dome)
    strategy = CarryStrategy(client, scanner, dome, stats)

    # ── Warm-up scan (block until we have at least one result) ───────────────
    log("Warm-up: running initial funding scan...")
    await scanner.scan_once()
    log(f"Warm-up complete — {len(scanner.ranked)} candidates found")

    # ── State recovery: reconstruct positions from HL + DB ───────────────────
    await _recover_state(client, strategy)
    # ── Exchange reconciliation: close any orphan positions not in strategy ──
    await reconcile_with_exchange(client, strategy, notifier)

    # ── WebSocket: listen for funding payments ────────────────────────────────
    address = client._address

    async def _on_ws_message(msg: dict) -> None:
        """Process WebSocket messages for funding payment accounting."""
        channel = msg.get("channel", "")
        data    = msg.get("data", {})

        if channel == "userFundings":
            fundings = data.get("fundings", []) if isinstance(data, dict) else []
            for f in fundings:
                asset  = f.get("coin", "")
                amount = float(f.get("usdc", 0))   # positive = received by us (short side)
                rate   = float(f.get("fundingRate", 0))
                # BUG-017: HL `time` is event-millis. Used as dedup key against WS replays.
                paid_at_ms = int(f.get("time", 0) or 0)
                if asset and amount != 0:
                    await strategy.record_funding_payment(asset, amount, rate, paid_at_ms)

    ws_subs = [
        {"type": "userFundings", "user": address},
        {"type": "userEvents",   "user": address},
    ]
    _fire(client.ws_listen(ws_subs, _on_ws_message), "ws_funding_listener")

    # ── Background tasks ──────────────────────────────────────────────────────
    _fire(scanner.run_forever(),                              "funding_scanner")
    _fire(monitor.run_forever(strategy.positions),            "delta_monitor")

    # ── Main tick loop ────────────────────────────────────────────────────────
    last_report = int(time.time())
    log(f"Entering main tick loop (interval={TICK_INTERVAL_S}s, max_positions={MAX_POSITIONS})")

    await notifier.send_alert(
        f"🚀 <b>HLCarryBot STARTED</b>\n"
        f"Session: <code>{session_id[:8]}</code>\n"
        f"Testnet: {TESTNET}\n"
        f"Capital: ${ACTIVE_CAPITAL_USD:.0f} | Max positions: {MAX_POSITIONS}"
    )

    try:
        while True:
            now = int(time.time())

            # ── CB1: API error surge ──────────────────────────────────────────
            recent_errors = sum(1 for t in _api_errors if now - t < 60)
            if recent_errors > 5:
                log_err(f"CB1: {recent_errors} API errors in 60s — HALTING")
                await notifier.send_alert(
                    f"🚨 <b>CB1 TRIGGERED: API Error Surge</b>\n"
                    f"{recent_errors} errors/60s — bot HALTED"
                )
                break

            # ── CB3: Session loss floor ───────────────────────────────────────
            if stats.total_pnl_usd < SESSION_LOSS_FLOOR:
                log_err(f"CB3: session P&L ${stats.total_pnl_usd:.2f} < floor ${SESSION_LOSS_FLOOR:.2f} — HALTING")
                await notifier.send_alert(
                    f"🛑 <b>CB3: SESSION LOSS FLOOR HIT</b>\n"
                    f"P&L: ${stats.total_pnl_usd:.2f} (floor: ${SESSION_LOSS_FLOOR:.2f})\n"
                    f"Bot HALTED."
                )
                break

            # ── Strategy tick ─────────────────────────────────────────────────
            try:
                await strategy.tick()
            except Exception as e:
                _api_errors.append(now)
                crash_log("carry_loop.strategy.tick", e, traceback.format_exc())

            # ── Periodic Telegram report ──────────────────────────────────────
            if now - last_report >= REPORT_INTERVAL_S:
                await notifier.send_report(stats)
                last_report = now
                log(
                    f"SESSION | cycles={stats.total_cycles} "
                    f"pnl=${stats.total_pnl_usd:+.4f} "
                    f"funding=${stats.total_funding_usd:.4f} "
                    f"fees=${stats.total_fees_usd:.4f}"
                )

            await asyncio.sleep(TICK_INTERVAL_S)

    except asyncio.CancelledError:
        log("carry_loop cancelled — shutting down")
    except Exception as e:
        crash_log("carry_loop.fatal", e, traceback.format_exc())
        raise
    finally:
        # Close all open positions gracefully
        for asset, pos in list(strategy.positions.items()):
            try:
                await strategy._close_position(asset, pos, reason="shutdown")
            except Exception:
                pass

        # Update session record
        try:
            async with get_db() as conn:
                await conn.execute(
                    """UPDATE sessions SET
                       ended_at=?, total_cycles=?, successful_cycles=?,
                       total_funding_usd=?, total_fees_usd=?, total_pnl_usd=?,
                       legging_events=?, deleverage_events=?, kill_switch_activations=?
                       WHERE id=?""",
                    (
                        int(time.time()),
                        stats.total_cycles, stats.successful_cycles,
                        stats.total_funding_usd, stats.total_fees_usd, stats.total_pnl_usd,
                        stats.legging_events, stats.deleverage_events, stats.kill_switch_activations,
                        session_id,
                    ),
                )
                await conn.commit()
        except Exception:
            pass

        log(f"Session {session_id[:8]} ended | P&L=${stats.total_pnl_usd:+.4f}")
        await notifier.send_report(stats)


# ── Entry point ───────────────────────────────────────────────────────────────

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
