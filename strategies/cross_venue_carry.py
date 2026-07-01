"""strategies/cross_venue_carry.py — Cross-venue (HL + Lighter) funding arb strategy.

Delta-neutral structure:
  - SHORT perp on the exchange with the HIGHER funding rate (earn funding)
  - LONG  perp on the exchange with the LOWER  funding rate (pay less / earn if negative)
  - Same unit size on both legs → delta = 0, only funding spread captured.

Entry execution:
  1. Open HL leg via maker_chase_entry (existing machinery).
  2. Open Lighter leg via LighterClient.maker_chase_entry (post-only, 0% fee).
  3. If Lighter leg fails → close HL leg taker (cover_naked_cross_leg).
  4. Record both legs filled → state = HOLD.

Exit triggers:
  - spread_flip: spread turned negative → taker close both legs immediately.
  - spread_soft_exit: spread < SPREAD_EXIT_THRESHOLD → maker exit both legs.
  - max_hold: MAX_HOLD hours elapsed → maker exit.
  - external: main loop or IronDome calls close_position() directly.

Exit execution (symmetric to entry):
  1. Close HL leg (maker → taker fallback).
  2. Close Lighter leg (maker → taker fallback).

Rebalancing:
  This strategy does NOT auto-bridge capital between exchanges.
  If margin on either side drops below REBALANCE_MARGIN_WARN, the bot sends
  a Telegram alert. The user manually bridges USDC via Arbitrum bridge.
  (HL ↔ Arbitrum ↔ Lighter, ~10 min, ~$1.5 round-trip.)
"""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import Dict, Optional

from core.constants import (
    CROSS_POSITION_SIZE_USD,
    CROSS_MAX_POSITIONS,
    CROSS_MIN_HOLD_HOURS,
    CROSS_MAX_HOLD_HOURS,
    SPREAD_ENTRY_THRESHOLD,
    SPREAD_EXIT_THRESHOLD,
    SPREAD_EXIT_FLIP,
    CROSS_FLIP_MIN_HOLD_HOURS,
    CROSS_FLIP_DEEP_MULT,
    CROSS_EXIT_COOLDOWN_S,
    CROSS_FLIP_REENTRY_COOLDOWN_S,
    MAKER_CHASE_TIMEOUT_S,
    LT_ENTRY_TIMEOUT_S,
    LT_ENTRY_MAKER_TIMEOUT_S,
    HL_ENTRY_MAKER_TIMEOUT_S,
    FEE_CROSS_ROUND_TRIP,
    FEE_PERP_MAKER,
    FEE_PERP_TAKER,
    FEE_LIGHTER_TAKER,
    REBALANCE_MARGIN_WARN,
    REBALANCE_MARGIN_EMERGENCY,
    CROSS_VENUE_WHITELIST,
    MAX_LIGHTER_BOOK_SPREAD_PCT,
    MIN_LIGHTER_FREE_BALANCE_USD,
    PRICE_DIVERGENCE_KILL_PCT,
    PORTFOLIO_DRAWDOWN_KILL_USD,
    DRIFT_TOLERANCE_PCT,
    SPREAD_TWAP_MIN_SAMPLES,
)
from core.logger import log, log_warn, crash_log
from core.models import CrossVenuePosition, CrossVenueState, SpreadSnapshot, SessionStats
from database.db import get_db
from feeds.spread_scanner import SpreadScanner
from notifier.telegram import TelegramNotifier
from venues.hyperliquid import HLClient
from venues.lighter import LighterClient


# If margin stays in emergency zone this long → auto-close all positions (taker)
_EMERGENCY_CLOSE_AFTER_S = 1800  # 30 minutes


class CrossVenueStrategy:
    """
    Manages the full lifecycle of HL ↔ Lighter delta-neutral funding arb positions.
    """

    def __init__(
        self,
        hl_client: HLClient,
        lighter_client: LighterClient,
        scanner: SpreadScanner,
        stats: SessionStats,
        notifier: Optional[TelegramNotifier] = None,
    ):
        self._hl = hl_client
        self._lt = lighter_client
        self._scanner = scanner
        self._stats = stats
        self._notifier = notifier

        # asset → CrossVenuePosition
        self.positions: Dict[str, CrossVenuePosition] = {}

        # Per-asset entry cooldowns (prevents rapid retry loops)
        self._entry_cooldowns: Dict[str, int] = {}

        # venue → timestamp when emergency margin threshold was first breached
        # Used to trigger auto-close if emergency persists > _EMERGENCY_CLOSE_AFTER_S
        self._emergency_since: Dict[str, int] = {}

        # Portfolio drawdown kill-switch state.
        # When tripped: no new entries, existing positions managed normally,
        # bot continues running for telemetry. Reset only by restart.
        self._portfolio_killed: bool = False

        # Lighter collateral snapshot at position entry — kept as secondary sanity
        # check for single-position mode; NOT the primary funding source anymore.
        self._lt_collateral_at_entry: Dict[str, float] = {}

        # Per-position timestamp of the last Lighter funding poll.
        # Primary funding tracker: rate × elapsed_hours × notional_usd per position.
        # Valid for any CROSS_MAX_POSITIONS (unlike account-level collateral delta).
        self._lt_last_funding_poll: Dict[str, float] = {}

        # Consecutive entry-failure counter per asset (ANY leg: HL maker timeout
        # OR Lighter ghost). If an asset fails N times in a row it gets a long
        # cooldown so we stop spinning / burning cover fees on something that
        # won't fill cleanly as maker.
        self._entry_fail_streak: Dict[str, int] = {}
        self._ENTRY_FAIL_STREAK_LIMIT = 3        # 3 fails → 4h blacklist
        self._ENTRY_FAIL_LONG_COOLDOWN_S = 14400 # 4 hours

        # Suppress repeated TG drift alerts for the same asset (fire once per interval)
        self._last_drift_alert: Dict[str, float] = {}
        self._DRIFT_ALERT_COOLDOWN_S = 1800  # 30 min

        # ── Exchange-truth reconciliation (P0) ────────────────────────────────
        # The drift check only sees positions in self.positions. Once a cycle is
        # ABANDONED/CLOSED it leaves tracking and any residual exchange leg becomes
        # an INVISIBLE orphan (ADA dup, XLM ghosts that had to be closed by hand).
        # reconcile_orphans() sweeps BOTH venues as source of truth. Debounced:
        # an untracked leg must be seen on 2 consecutive sweeps before we flatten,
        # so a transient/partial API read can never trigger a wrong close.
        self._orphan_strikes: Dict[str, int] = {}
        self._ORPHAN_STRIKE_LIMIT = 2
        # Gate: the sweep must not run until startup recovery has finished, else a
        # position that is ours but not yet restored to self.positions looks orphan.
        self._recovery_done: bool = False

    # ── State recovery on restart ─────────────────────────────────────────────

    async def recover_open_positions(self) -> None:
        """
        Called once at startup — reconcile DB OPEN cycles with real exchange positions.

        For each OPEN cycle in DB:
          • Both legs confirmed on exchange  → restore as HOLD (prevents layering)
          • Neither leg found               → mark DB ABANDONED (ghost cleanup)
          • Only one leg found              → taker-close the orphan leg + ABANDONED

        Without this, a restart during an open position causes the new session to
        open a duplicate position on top of the orphan → doubled margin → emergency_margin
        fires immediately → position closed 30 min later with a fee loss.
        """
        import aiosqlite as _aiosqlite

        # P2/P3: cancel any resting orders on HL BEFORE snapshotting positions.
        # The orphan we hit was born from a resting maker close-order that filled
        # *after* recovery had already snapshotted the leg as present. Clearing the
        # book first guarantees the position read below reflects a settled state.
        try:
            await self._cancel_all_resting_orders()
            await asyncio.sleep(1.0)  # let cancels settle before reading positions
        except Exception as e:
            crash_log("CrossVenue.recover.cancel_resting", e)

        try:
            async with get_db() as conn:
                conn.row_factory = _aiosqlite.Row
                cur  = await conn.execute(
                    "SELECT * FROM cross_venue_cycles WHERE state='OPEN'"
                )
                rows = await cur.fetchall()
        except Exception as e:
            crash_log("CrossVenue.recover_open_positions.db_read", e)
            self._recovery_done = True
            return

        if not rows:
            log("CrossVenue.recover: no OPEN DB cycles — starting fresh")
            self._recovery_done = True
            return

        log(f"CrossVenue.recover: found {len(rows)} OPEN cycle(s) in DB — checking exchange")

        for row in rows:
            r = dict(row)

            asset    = r["asset"]
            cycle_id = r["id"]
            units    = float(r["units"] or 0)
            short_v  = r["short_venue"]
            long_v   = r["long_venue"]

            # Check both legs on exchange
            try:
                hl_size = await self._hl.get_perp_position_size(asset)
            except Exception as e:
                log_warn(f"CrossVenue.recover: {asset} HL check failed: {e}")
                hl_size = -1.0  # unknown

            try:
                # Use or_raise variant: get_position_for_asset() swallows exceptions
                # and returns None on API error — indistinguishable from "no position".
                # That causes lt_size=0.0 → PARTIAL branch → HL leg closed → duplicate entry.
                lt_size = await self._lt.get_position_size_or_raise(asset)
            except Exception as e:
                log_warn(f"CrossVenue.recover: {asset} Lighter check failed: {e}")
                lt_size = -1.0  # unknown

            threshold = max(units * 0.5, 0.001)
            hl_ok  = (hl_size >= threshold)
            lt_ok  = (lt_size >= threshold)
            hl_unk = (hl_size < 0)
            lt_unk = (lt_size < 0)
            now    = int(time.time())

            if hl_ok and lt_ok:
                # ── Both legs live — restore as HOLD ──────────────────────
                pos = CrossVenuePosition(
                    asset=asset,
                    state=CrossVenueState.HOLD,
                    short_venue=short_v,
                    long_venue=long_v,
                    notional_usd=float(r["notional_usd"] or 0),
                    units=units,
                    hl_entry_price=float(r["hl_entry_price"] or 0),
                    lighter_entry_price=float(r["lighter_entry_price"] or 0),
                    hl_rate_at_entry=float(r["hl_rate_at_entry"] or 0),
                    lighter_rate_at_entry=float(r["lighter_rate_at_entry"] or 0),
                    spread_at_entry=float(r["spread_at_entry"] or 0),
                    entered_at=r["entered_at"],
                    entry_fee_usd=float(r["entry_fee_usd"] or 0),
                    hl_funding_collected=float(r["hl_funding_collected"] or 0),
                    lighter_funding_collected=float(r["lighter_funding_collected"] or 0),
                    lighter_last_funding_id=int(
                        r["lighter_last_funding_id"]
                        if "lighter_last_funding_id" in r.keys() else 0
                    ),
                    hl_last_funding_time_ms=int(
                        r["hl_last_funding_time_ms"]
                        if "hl_last_funding_time_ms" in r.keys() else 0
                    ),
                    cycle_id=cycle_id,
                )
                self.positions[asset] = pos
                # Start the Lighter funding poll timer from now — any funding that
                # accrued before recovery is already captured in DB / pos fields.
                self._lt_last_funding_poll[asset] = float(now)
                age_h = (now - int(r["entered_at"] or now)) / 3600
                log(
                    f"CrossVenue.recover: {asset} RESTORED to HOLD — "
                    f"HL={hl_size:.4f} LT={lt_size:.4f} units={units:.4f} "
                    f"age={age_h:.1f}h"
                )

                # ── One-time reconciliation: replace stale rate×time accounting ──
                # If lighter_last_funding_id == 0 but lighter_funding_collected != 0,
                # the persisted amount came from the old rate×time estimator (which
                # over-credits by ~3×). Wipe it and re-poll real settlements via
                # positionFunding API. Next poll_lighter_funding() will populate
                # correctly from entered_at onward (idempotent via funding_id).
                if pos.lighter_last_funding_id == 0 and abs(pos.lighter_funding_collected) > 0:
                    stale = pos.lighter_funding_collected
                    pos.lighter_funding_collected = 0.0
                    self._stats.total_funding_usd -= stale
                    log_warn(
                        f"CrossVenue.recover: {asset} stale lighter_funding "
                        f"${stale:+.5f} dropped (was rate×time estimate); "
                        f"real settlements will be loaded from positionFunding API"
                    )
                    await self._db_update_funding(pos)

            elif not hl_ok and not lt_ok and not hl_unk and not lt_unk:
                # ── No legs on exchange — ghost ───────────────────────────
                async with get_db() as c2:
                    await c2.execute(
                        "UPDATE cross_venue_cycles SET state='ABANDONED', exited_at=?, "
                        "exit_reason='db_ghost_no_exchange_position' WHERE id=?",
                        (now, cycle_id)
                    )
                    await c2.commit()
                log(f"CrossVenue.recover: {asset} ABANDONED (DB ghost)")

            else:
                # ── Partial / unknown — close what exists, mark abandoned ─
                log_warn(
                    f"CrossVenue.recover: {asset} PARTIAL — "
                    f"HL={hl_size:.4f} LT={lt_size:.4f} — closing orphan leg(s)"
                )
                if hl_ok:
                    try:
                        hl_is_buy = (long_v == "hl")  # close: flip direction
                        book = await self._hl.get_l2_book(asset)
                        levels = (book or {}).get("levels", [[], []])
                        bids, asks = levels[0], levels[1]
                        if hl_is_buy:
                            mid = float(asks[0]["px"]) * 1.05 if asks else 999999.0
                        else:
                            mid = float(bids[0]["px"]) * 0.95 if bids else 0.001
                        await self._hl.place_taker(
                            asset, is_buy=hl_is_buy, size=hl_size, price=mid, reduce_only=True
                        )
                        log(f"CrossVenue.recover: {asset} HL orphan closed (taker)")
                    except Exception as e:
                        crash_log(f"CrossVenue.recover.close_hl.{asset}", e)
                if lt_ok:
                    try:
                        # To CLOSE a Lighter position: if we were SHORT on Lighter
                        # (short_venue=="lighter") we need to BUY to close.
                        # If we were LONG on Lighter we need to SELL.
                        lt_is_buy = (short_v == "lighter")
                        lt_bid, lt_ask = await self._lt.get_best_prices(asset)
                        # Aggressive cross price to guarantee IOC fill
                        if lt_is_buy:
                            lt_px = lt_ask * 1.05 if lt_ask > 0 else 999999.0
                        else:
                            lt_px = lt_bid * 0.95 if lt_bid > 0 else 0.0001
                        await self._lt.place_taker(
                            asset, is_buy=lt_is_buy, size=lt_size, price=lt_px,
                            reduce_only=True,
                        )
                        log(f"CrossVenue.recover: {asset} Lighter orphan closed (taker) @ {lt_px}")
                    except Exception as e:
                        crash_log(f"CrossVenue.recover.close_lt.{asset}", e)
                async with get_db() as c2:
                    await c2.execute(
                        "UPDATE cross_venue_cycles SET state='ABANDONED', exited_at=?, "
                        "exit_reason='recover_partial_close' WHERE id=?",
                        (now, cycle_id)
                    )
                    await c2.commit()

        self._recovery_done = True

    # ── Main tick ─────────────────────────────────────────────────────────────

    async def tick(self) -> None:
        """Called every TICK_INTERVAL_S by main_cross.py."""
        now = int(time.time())

        # 0. Portfolio drawdown kill switch — soft halt, manage existing only.
        if not self._portfolio_killed and self._stats.total_pnl_usd <= -PORTFOLIO_DRAWDOWN_KILL_USD:
            self._portfolio_killed = True
            log_warn(
                f"CrossVenue: PORTFOLIO KILL SWITCH | "
                f"session P&L ${self._stats.total_pnl_usd:+.2f} "
                f"<= -${PORTFOLIO_DRAWDOWN_KILL_USD:.2f}. No new entries until restart."
            )
            await self._notify(
                f"🛑 <b>Portfolio drawdown kill</b>\n"
                f"Session P&L: ${self._stats.total_pnl_usd:+.2f}\n"
                f"Threshold: -${PORTFOLIO_DRAWDOWN_KILL_USD:.2f}\n"
                f"Open positions will continue to be managed. No new entries."
            )

        # 1. Manage existing positions (exits first)
        for asset, pos in list(self.positions.items()):
            if pos.state == CrossVenueState.HOLD:
                await self._manage_hold(asset, pos)

        # 2. Open new positions if slots available — skip entirely if killed
        if not self._portfolio_killed:
            open_count = sum(
                1 for p in self.positions.values()
                if p.state in (CrossVenueState.HOLD, CrossVenueState.ENTERING)
            )
            if open_count < CROSS_MAX_POSITIONS:
                _n_slots = CROSS_MAX_POSITIONS - open_count
                # Request extra candidates as fallback for assets on cooldown.
                # With MAX=1, without the buffer, top candidate on cooldown → entire tick idle.
                candidates = self._scanner.best_candidates(
                    n=_n_slots + max(5, len(self._entry_cooldowns)),
                    exclude=set(self.positions.keys()),
                )
                for snap in candidates:
                    if now < self._entry_cooldowns.get(snap.asset, 0):
                        continue
                    await self._enter_position(snap)
                    # At most 1 new entry per tick: break if a position was actually
                    # added (succeeded). If entry was skipped/failed (position deleted or
                    # never added), open_count stays the same and we try the next candidate.
                    new_count = sum(
                        1 for p in self.positions.values()
                        if p.state in (CrossVenueState.HOLD, CrossVenueState.ENTERING)
                    )
                    if new_count > open_count:
                        break

        # 3. Rebalance check
        await self._check_rebalance()

    # ── Entry ─────────────────────────────────────────────────────────────────

    async def _enter_position(self, snap: SpreadSnapshot) -> None:
        asset = snap.asset

        # ── Pre-entry safety checks ───────────────────────────────────────────

        # 1. Whitelist: only liquid perps with reliable Lighter books
        if asset not in CROSS_VENUE_WHITELIST:
            log_warn(f"CrossVenue: {asset} not in whitelist, skipping")
            return

        # 2. Lighter free balance: need room for margin + buffer
        try:
            lt_balance = await self._lt.get_available_balance()
        except Exception as e:
            log_warn(f"CrossVenue: {asset} cannot fetch Lighter balance ({e}), skipping")
            return
        if lt_balance < MIN_LIGHTER_FREE_BALANCE_USD:
            log_warn(
                f"CrossVenue: {asset} Lighter balance ${lt_balance:.2f} "
                f"< ${MIN_LIGHTER_FREE_BALANCE_USD:.0f} minimum — skipping"
            )
            return

        # 3. Lighter book spread: reject thin markets where slippage exceeds profit
        try:
            lt_bid, lt_ask = await self._lt.get_best_prices(asset)
        except Exception as e:
            log_warn(f"CrossVenue: {asset} cannot fetch Lighter order book ({e}), skipping")
            return
        if lt_bid <= 0 or lt_ask <= 0:
            log_warn(f"CrossVenue: {asset} Lighter book is empty, skipping")
            return
        lt_book_spread_pct = (lt_ask - lt_bid) / lt_ask
        if lt_book_spread_pct > MAX_LIGHTER_BOOK_SPREAD_PCT:
            log_warn(
                f"CrossVenue: {asset} Lighter book spread {lt_book_spread_pct*100:.3f}% "
                f"> {MAX_LIGHTER_BOOK_SPREAD_PCT*100:.1f}% max — skipping (thin market)"
            )
            return

        # 4. Anti-spike / warmup gate: reject if spread is far above its rolling mean,
        # OR if scanner has not yet accumulated enough history to distinguish spike
        # from normal rate. BLOCK on cold start (fail-closed) — entering a spike on
        # startup is what caused NEAR entry at 0.091%/h that quickly normalized (BUG-022).
        try:
            is_spike, sigma_reason = self._scanner.is_spike(asset, snap)
            if is_spike:
                if "warming_up" in sigma_reason:
                    log(f"CrossVenue: {asset} spike filter warming up — skipping ({sigma_reason})")
                else:
                    log_warn(f"CrossVenue: {asset} SIGMA SPIKE — skipping ({sigma_reason})")
                # No cooldown on warmup — try again next tick once more history exists
                if "warming_up" not in sigma_reason:
                    self._set_cooldown(asset)
                return
        except Exception as e:
            crash_log(f"CrossVenue.sigma_check.{asset}", e)
            return  # fail-closed on error: do not enter if spike check fails

        # 5. TWAP confirmation gate — primary defence against transient spike entries.
        # Lighter /funding-rates is an instantaneous predicted rate; actual settlement
        # is the hourly TWAP. A 3-min spike at 0.04%/h → settlement ≈ 0.003%/h.
        # Require the 15-min rolling mean spread to be ≥ entry threshold before entry.
        try:
            twap_spread, twap_valid = self._scanner.get_twap_spread(asset)
            if not twap_valid:
                samples = len(self._scanner._rate_history.get(asset) or [])
                log(
                    f"CrossVenue: {asset} TWAP gate warming up — "
                    f"skipping ({samples}/{SPREAD_TWAP_MIN_SAMPLES} samples in window)"
                )
                return  # no cooldown — retry next tick as history accumulates
            if twap_spread < SPREAD_ENTRY_THRESHOLD:
                log_warn(
                    f"CrossVenue: {asset} TWAP {twap_spread*100:.4f}%/h "
                    f"< threshold {SPREAD_ENTRY_THRESHOLD*100:.3f}%/h "
                    f"(instant={snap.spread*100:.4f}%/h, ratio={snap.spread/twap_spread:.1f}×) "
                    f"— transient spike, skipping"
                )
                self._set_cooldown(asset)
                return
            log(
                f"CrossVenue: {asset} TWAP {twap_spread*100:.4f}%/h ✓ "
                f"(instant={snap.spread*100:.4f}%/h, ratio={snap.spread/twap_spread:.1f}×)"
            )
        except Exception as e:
            crash_log(f"CrossVenue.twap_check.{asset}", e)
            return  # fail-closed

        # NOTE: historical stability is now baked into the scanner's ranking
        # (refresh_historical_stats + hot-path ranking). Assets only appear in
        # ranked candidates if they have ≥50% hit ratio against the threshold
        # over the past 24h. No separate gate here — keeping the flow simpler.

        log(
            f"CrossVenue: entering {asset} | spread={snap.spread*100:.4f}%/h "
            f"({snap.spread_pct_annual:.1f}% APR) | "
            f"short={snap.short_venue} long={snap.long_venue} | "
            f"Lighter book spread={lt_book_spread_pct*100:.3f}%"
        )

        # Determine which venue is short and which is long
        hl_is_short   = snap.short_venue == "hl"
        hl_is_buy     = not hl_is_short     # HL: buy if longing, sell if shorting
        lt_is_buy     = not hl_is_buy        # Lighter is the other side

        # Get mark price for sizing (use HL price as canonical)
        mark_price = snap.hl_mark_px or 0.0
        if mark_price <= 0:
            # Fallback 1: HL L2 book mid
            try:
                book = await self._hl.get_l2_book(asset)
                levels = book.get("levels", [[], []])
                bids, asks = levels[0], levels[1]
                mark_price = (
                    (float(bids[0]["px"]) + float(asks[0]["px"])) / 2
                    if bids and asks else 0.0
                )
            except Exception:
                pass
        if mark_price <= 0:
            # Fallback 2: Lighter mid price (lt_bid/lt_ask already fetched above)
            if lt_bid > 0 and lt_ask > 0:
                mark_price = (lt_bid + lt_ask) / 2
                log(f"CrossVenue: {asset} using Lighter mid {mark_price:.6f} as price fallback")
        if mark_price <= 0:
            log_warn(f"CrossVenue: cannot determine price for {asset}, skipping")
            return

        units = CROSS_POSITION_SIZE_USD / mark_price

        # 6. Duplicate-open guard (P1): never open if a position for this asset already
        # exists on EITHER venue. Catches the failure that produced the 2× ADA cycles
        # (two opens within 2 min): a residual/just-opened leg must block a second entry.
        # Fail-closed — if we cannot confirm both venues are flat, we skip.
        try:
            hl_existing = await self._hl.get_perp_position_size(asset)
            lt_existing = await self._lt.get_position_size_or_raise(asset)
        except Exception as e:
            log_warn(f"CrossVenue: {asset} pre-entry position check failed ({e}) — skipping")
            return
        dust = max(units * 0.05, 0.0005)
        if hl_existing > dust or lt_existing > dust:
            log_warn(
                f"CrossVenue: {asset} ABORT entry — existing position "
                f"(HL={hl_existing:.6f} LT={lt_existing:.6f}). Possible orphan/duplicate; "
                f"reconcile sweep will handle it."
            )
            self._set_cooldown(asset)
            return

        pos = CrossVenuePosition(
            asset=asset,
            state=CrossVenueState.ENTERING,
            short_venue=snap.short_venue,
            long_venue=snap.long_venue,
            notional_usd=CROSS_POSITION_SIZE_USD,
            units=units,
            hl_rate_at_entry=snap.hl_rate,
            lighter_rate_at_entry=snap.lighter_rate,
            spread_at_entry=snap.spread,
        )
        self.positions[asset] = pos

        # ── Step 1: Open HL leg ───────────────────────────────────────────────
        hl_label = f"HL {'short' if hl_is_short else 'long'}"
        hl_order_id = await self._hl.maker_chase_entry(
            asset=asset,
            is_buy=hl_is_buy,
            size=units,
            side_label=hl_label,
            timeout_s=HL_ENTRY_MAKER_TIMEOUT_S,  # long maker window — nothing exposed yet
        )

        hl_via_taker = False
        if hl_order_id is None:
            # Maker chase couldn't fill within the timeout — common on ultra-liquid
            # assets (BCH) where the touch moves faster than we can rest. Taker
            # fallback: cross IOC so we still capture the carry. The extra perp-taker
            # cost (+0.03% over maker) is recouped in ~1-2h of hold at ~100% APR.
            log_warn(f"CrossVenue: {asset} HL maker timed out — taker fallback")
            try:
                book = await self._hl.get_l2_book(asset)
                levels = book.get("levels", [[], []])
                if hl_is_buy:
                    raw_px = float(levels[1][0]["px"]) * 1.005 if levels[1] else mark_price * 1.02
                else:
                    raw_px = float(levels[0][0]["px"]) * 0.995 if levels[0] else mark_price * 0.98
                px = self._hl.round_price(raw_px)
                await self._hl.place_taker(asset, is_buy=hl_is_buy, size=units, price=px)
                await asyncio.sleep(1.5)  # let IOC fill settle before reading position
                cl = await self._hl.get_clearinghouse()
                for ap in cl.get("assetPositions", []):
                    p = ap.get("position", {})
                    if p.get("coin") == asset and abs(float(p.get("szi", 0) or 0)) >= units * 0.9:
                        hl_order_id = "taker"
                        hl_via_taker = True
                        break
            except Exception as e:
                crash_log(f"CrossVenue.hl_taker_fallback.{asset}", e)

        if hl_order_id is None:
            log_warn(f"CrossVenue: {asset} HL leg failed (maker+taker) — aborting entry")
            del self.positions[asset]
            await self._register_entry_fail(asset, "HL leg unfillable (maker+taker)")
            return

        pos.hl_order_id = hl_order_id
        pos.hl_entry_price = mark_price  # will be refined below from clearinghouse
        pos.entry_fee_usd += CROSS_POSITION_SIZE_USD * (
            FEE_PERP_TAKER if hl_via_taker else FEE_PERP_MAKER
        )

        # Read actual HL fill price from clearinghouse (entryPx is the real avg fill).
        # mark_price was just the mid at order time — can differ by 0.1-0.3% on volatility.
        try:
            cl_state = await self._hl.get_clearinghouse()
            for ap in cl_state.get("assetPositions", []):
                p = ap.get("position", {})
                if p.get("coin") == asset:
                    entry_px = float(p.get("entryPx", 0) or 0)
                    if entry_px > 0:
                        slippage_pct = abs(entry_px - mark_price) / mark_price * 100
                        pos.hl_entry_price = entry_px
                        log(
                            f"CrossVenue: {asset} HL actual fill={entry_px:.6f} "
                            f"(mark was {mark_price:.6f}, slip={slippage_pct:.3f}%)"
                        )
                    break
        except Exception:
            pass  # mark_price approximation stands

        log(f"CrossVenue: {asset} HL leg filled @ {pos.hl_entry_price:.6f} — now opening Lighter leg")

        # ── Step 2: Open Lighter leg ──────────────────────────────────────────
        # Lighter taker fee = 0%, so: short maker window (15s) → taker IOC fallback.
        # Old 90s pure-maker loop was burning HL cover fees + triggering 4h blacklist.
        lt_label = f"Lighter {'short' if not lt_is_buy else 'long'}"
        lt_order_id = await self._lt.maker_chase_entry(
            asset=asset,
            is_buy=lt_is_buy,
            size=units,
            label=lt_label,
            timeout_s=LT_ENTRY_MAKER_TIMEOUT_S,
        )

        lt_via_taker = False
        if lt_order_id is None:
            # Maker didn't fill in 15s → taker IOC (0% fee on Lighter, no penalty).
            log_warn(f"CrossVenue: {asset} Lighter maker timed out — taker fallback (0% fee)")
            try:
                lt_bid_now, lt_ask_now = await self._lt.get_best_prices(asset)
                lt_taker_px = lt_ask_now * 1.001 if lt_is_buy else lt_bid_now * 0.999
                lt_order_id = await self._lt.place_taker(
                    asset, is_buy=lt_is_buy, size=units, price=lt_taker_px
                )
                if lt_order_id:
                    await asyncio.sleep(5.0)  # ZK settlement: ~3-4s to appear on-chain
                    lt_pos_check = await self._lt.get_position_for_asset(asset)
                    sz_check = abs(float(
                        (lt_pos_check or {}).get("size", 0)
                        or (lt_pos_check or {}).get("base_amount", 0) or 0
                    ))
                    if sz_check >= units * 0.9:
                        lt_via_taker = True
                        log(f"CrossVenue: {asset} Lighter taker fill confirmed sz={sz_check:.6f}")
                    else:
                        # One more ZK cycle
                        await asyncio.sleep(5.0)
                        lt_pos_check2 = await self._lt.get_position_for_asset(asset)
                        sz_check2 = abs(float(
                            (lt_pos_check2 or {}).get("size", 0)
                            or (lt_pos_check2 or {}).get("base_amount", 0) or 0
                        ))
                        if sz_check2 >= units * 0.9:
                            lt_via_taker = True
                            log(f"CrossVenue: {asset} Lighter taker fill confirmed (2nd ZK check) sz={sz_check2:.6f}")
                        else:
                            lt_order_id = None
            except Exception as e:
                crash_log(f"CrossVenue.lt_taker_fallback.{asset}", e)
                lt_order_id = None

        if lt_order_id is None:
            # ZK-lag guard: maker_chase may have timed out while the last order was
            # still in ZK mempool. Wait one settlement window before covering HL —
            # covering prematurely creates an orphan Lighter short (root of PARTIAL bug).
            await asyncio.sleep(6.0)
            lt_pos_final = await self._lt.get_position_for_asset(asset)
            sz_final = abs(float(
                (lt_pos_final or {}).get("size", 0)
                or (lt_pos_final or {}).get("base_amount", 0) or 0
            ))
            if sz_final >= units * 0.9:
                lt_via_taker = True
                lt_order_id = "zk_lag_fill"
                log(f"CrossVenue: {asset} Lighter ZK-lag fill detected sz={sz_final:.6f} — no cover needed")
            else:
                log_warn(f"CrossVenue: {asset} Lighter leg failed — covering HL leg")
                await self._cover_naked_hl_leg(asset, pos)
                del self.positions[asset]
                await self._register_entry_fail(asset, "Lighter ghost — HL leg covered")
                return

        # ── Both legs filled ──────────────────────────────────────────────────
        # Get actual Lighter fill price from live position data (entry_price is the
        # real avg fill — more accurate than order-book mid at order time).
        try:
            lt_pos_actual = await self._lt.get_position_for_asset(asset)
            lt_actual_entry = float((lt_pos_actual or {}).get("entry_price", 0) or 0)
            if lt_actual_entry > 0:
                lt_bid_now, lt_ask_now = await self._lt.get_best_prices(asset)
                lt_mid = (lt_bid_now + lt_ask_now) / 2 if lt_ask_now > 0 else lt_actual_entry
                lt_slip_pct = abs(lt_actual_entry - lt_mid) / lt_mid * 100 if lt_mid > 0 else 0
                pos.lighter_entry_price = lt_actual_entry
                log(
                    f"CrossVenue: {asset} Lighter actual fill={lt_actual_entry:.6f} "
                    f"(mid≈{lt_mid:.6f}, slip={lt_slip_pct:.3f}%)"
                )
            else:
                lt_bid_now, lt_ask_now = await self._lt.get_best_prices(asset)
                pos.lighter_entry_price = lt_ask_now if lt_is_buy else lt_bid_now
        except Exception:
            lt_bid_now, lt_ask_now = await self._lt.get_best_prices(asset)
            pos.lighter_entry_price = lt_ask_now if lt_is_buy else lt_bid_now

        pos.lighter_order_id = lt_order_id
        pos.entry_fee_usd += 0.0  # Lighter maker fee = 0%
        pos.mark_entered()

        # Anchor HL funding idempotency to entry time so we don't accidentally
        # credit userFunding events from before this position was opened.
        pos.hl_last_funding_time_ms = int(time.time() * 1000)

        # Initialize per-position Lighter funding poll timer.
        self._lt_last_funding_poll[asset] = time.time()

        # Persist to DB
        cycle_id = str(uuid.uuid4())
        pos.cycle_id = cycle_id
        await self._db_insert_cycle(pos)

        self._entry_fail_streak[asset] = 0  # successful entry resets fail streak
        self._stats.total_cycles += 1
        log(
            f"CrossVenue: {asset} ENTERED | "
            f"HL @ {pos.hl_entry_price:.4f} | Lighter @ {pos.lighter_entry_price:.4f} | "
            f"units={pos.units:.6f} | spread={pos.spread_at_entry*100:.4f}%/h | "
            f"fees=${pos.entry_fee_usd:.4f}"
        )
        await self._notify(
            f"✅ <b>CrossVenue: {asset} ENTERED</b>\n"
            f"Short: {pos.short_venue} | Long: {pos.long_venue}\n"
            f"Spread: {pos.spread_at_entry*100:.4f}%/h ({pos.spread_at_entry*8760*100:.1f}% APR)\n"
            f"Size: {pos.units:.6f} {asset} (${pos.notional_usd:.0f}/leg)\n"
            f"Entry fees: ${pos.entry_fee_usd:.4f}"
        )

    # ── Hold management ───────────────────────────────────────────────────────

    async def _manage_hold(self, asset: str, pos: CrossVenuePosition) -> None:
        """Evaluate exit conditions for a HOLD position."""
        snap = self._scanner.get_snapshot(asset)
        if snap is None:
            return  # wait for next scan

        hold_h = pos.hold_hours

        # 0. Price divergence guard. If HL and Lighter mids drift apart by > kill%,
        # one venue's oracle is broken or there's a major arb. Either way we lose
        # delta-neutrality. Bail out taker on both legs immediately.
        try:
            hl_mid = snap.hl_mark_px or 0.0
            lt_bid, lt_ask = await self._lt.get_best_prices(asset)
            lt_mid = (lt_bid + lt_ask) / 2 if (lt_bid > 0 and lt_ask > 0) else 0.0
            if hl_mid > 0 and lt_mid > 0:
                divergence = abs(hl_mid - lt_mid) / hl_mid
                if divergence > PRICE_DIVERGENCE_KILL_PCT:
                    log_warn(
                        f"CrossVenue: {asset} PRICE DIVERGENCE {divergence*100:.2f}% "
                        f"(HL={hl_mid:.4f} LT={lt_mid:.4f}) > {PRICE_DIVERGENCE_KILL_PCT*100:.1f}% — "
                        f"HL maker + LT taker exit"
                    )
                    await self._notify(
                        f"⚠️ <b>Price divergence: {asset}</b>\n"
                        f"HL mid: {hl_mid:.4f} | LT mid: {lt_mid:.4f}\n"
                        f"Divergence: {divergence*100:.2f}% — closing HL maker + LT taker"
                    )
                    # Delta-neutral: divergence is not a directional loss. Exit HL maker
                    # (0.015% not 0.045%); Lighter taker is free + instant (HL closes first,
                    # so no naked window). Same asymmetric close as spread_flip.
                    await self._close_position(
                        asset, pos, reason="price_divergence", hl_taker=False, lt_taker=True
                    )
                    return
        except Exception as e:
            crash_log(f"CrossVenue.divergence_check.{asset}", e)

        # Check spread conditions — exit decisions use TWAP, not instant.
        # Settlement is hourly TWAP; instant rate can spike and revert. An exit
        # based on instant could close on a transient blip; an exit blocked by
        # an instant spike could keep us in a losing position. TWAP matches what
        # the exchange actually credits each hour.
        twap_abs, twap_valid = self._scanner.get_twap_spread(asset)
        instant_abs = snap.spread

        if twap_valid:
            decision_abs = twap_abs
            twap_label = f"TWAP={twap_abs*100:.4f}%/h"
        else:
            # Fall back to instant if TWAP cannot be computed (cold history).
            # Conservative: this just means exit uses the same data as before.
            decision_abs = instant_abs
            twap_label = "TWAP=unavailable"

        # Effective spread sign: positive if short_venue still has higher rate
        sign = -1.0 if snap.short_venue != pos.short_venue else 1.0
        effective_spread = sign * decision_abs

        # 1. Hard exit: spread flipped, we're now paying net funding.
        # Funding settles hourly, so we have ~1h of runway — no need to pay HL
        # taker (0.045%). Chase HL maker (→ taker fallback), Lighter stays taker
        # (0% fee, instant, avoids naked-leg window). HL flip costs ~0.0001%/h of
        # funding during the ≤30s chase — negligible vs the 0.03% maker saving.
        # Anti-churn (2026-06-06): two-tier. A mild flip is usually noise that reverts
        # within ~1h — exiting at 0.25h banked ≈$0 funding (hourly) and paid a full
        # round-trip each time (audit: −$0.29 all-time). Wait CROSS_FLIP_MIN_HOLD_HOURS
        # so a revert can save the round-trip. A DEEP flip (≤ EXIT_FLIP × DEEP_MULT) is a
        # real regime reversal — bail immediately to stop paying funding.
        flip_deep = effective_spread <= SPREAD_EXIT_FLIP * CROSS_FLIP_DEEP_MULT
        if effective_spread <= SPREAD_EXIT_FLIP and (flip_deep or hold_h >= CROSS_FLIP_MIN_HOLD_HOURS):
            log(
                f"CrossVenue: {asset} SPREAD FLIP exit | "
                f"effective={effective_spread*100:.4f}%/h ({twap_label}, "
                f"instant={instant_abs*100:.4f}%/h) "
                f"{'DEEP' if flip_deep else f'mild hold={hold_h:.2f}h'}"
            )
            await self._close_position(
                asset, pos, reason="spread_flip", hl_taker=False, lt_taker=True
            )
            return

        # 2. Soft exit: spread too small to justify staying
        if effective_spread <= SPREAD_EXIT_THRESHOLD and hold_h >= CROSS_MIN_HOLD_HOURS:
            log(
                f"CrossVenue: {asset} SOFT EXIT | "
                f"effective={effective_spread*100:.4f}%/h < threshold "
                f"({twap_label}, instant={instant_abs*100:.4f}%/h)"
            )
            await self._close_position(asset, pos, reason="spread_soft_exit", use_taker=False)
            return

        # 3. Max hold time
        if hold_h >= CROSS_MAX_HOLD_HOURS:
            log(f"CrossVenue: {asset} MAX HOLD ({hold_h:.1f}h) — closing")
            await self._close_position(asset, pos, reason="max_hold", use_taker=False)
            return

    # ── Exit ──────────────────────────────────────────────────────────────────

    async def _close_position(
        self,
        asset: str,
        pos: CrossVenuePosition,
        reason: str,
        use_taker: bool = False,
        hl_taker: bool | None = None,
        lt_taker: bool | None = None,
    ) -> None:
        """Close both legs of a cross-venue position.

        Per-leg taker control: `hl_taker`/`lt_taker` override `use_taker` for a
        single leg. Used by spread_flip to exit HL maker (saves 0.045%→0.015%)
        while keeping Lighter taker (Lighter taker is 0% — free *and* instant, so
        no point maker-chasing it, and it avoids a naked-leg window: HL closes
        first, so a slow Lighter maker-chase would leave us directional for up to
        MAKER_CHASE_TIMEOUT_S). HL is chased first while both legs are still on,
        keeping us delta-neutral until HL fills.
        """
        if pos.state == CrossVenueState.EXITING:
            return
        pos.state = CrossVenueState.EXITING

        if hl_taker is None:
            hl_taker = use_taker
        if lt_taker is None:
            lt_taker = use_taker

        hl_is_short  = pos.short_venue == "hl"
        hl_close_buy = hl_is_short   # close short → buy back; close long → sell
        lt_close_buy = not hl_close_buy

        log(f"CrossVenue: closing {asset} | reason={reason} hl_taker={hl_taker} lt_taker={lt_taker}")

        # Close HL leg first — while both legs are still open we stay delta-neutral
        # during the maker chase. Only after HL closes is the Lighter leg directional.
        hl_ok = await self._close_hl_leg(asset, pos, hl_close_buy, hl_taker)

        # Close Lighter leg
        lt_ok = await self._close_lt_leg(asset, pos, lt_close_buy, lt_taker)

        if not hl_ok:
            log_warn(f"CrossVenue: {asset} HL close FAILED — may need manual intervention")
        if not lt_ok:
            log_warn(f"CrossVenue: {asset} Lighter close FAILED — may need manual intervention")

        # Finalize
        pos.mark_exited()
        await self._db_update_cycle(pos, reason)

        self._stats.total_pnl_usd += pos.total_pnl_usd
        # NOTE: total_funding_usd is updated INCREMENTALLY by poll_lighter_funding()
        # and record_hl_funding() — do NOT add pos.net_funding_usd here (double-count).
        self._stats.total_fees_usd += pos.entry_fee_usd + pos.exit_fee_usd
        if hl_ok and lt_ok:
            self._stats.successful_cycles += 1

        # Remove from active positions and set cooldown. After a flip, hold the asset
        # in a longer cooldown so we don't bounce back into the same reversing spread.
        self.positions.pop(asset, None)
        self._lt_last_funding_poll.pop(asset, None)
        self._lt_collateral_at_entry.pop(asset, None)
        if reason == "spread_flip":
            self._set_cooldown(asset, CROSS_FLIP_REENTRY_COOLDOWN_S)
        else:
            self._set_cooldown(asset)

        await self._notify(
            f"🔴 <b>CrossVenue: {asset} CLOSED</b> [{reason}]\n"
            f"Hold: {pos.hold_hours:.1f}h\n"
            f"Net funding: ${pos.net_funding_usd:+.4f}\n"
            f"Fees: ${pos.entry_fee_usd + pos.exit_fee_usd:.4f}\n"
            f"Net P&L: ${pos.total_pnl_usd:+.4f}"
        )

    async def _close_hl_leg(
        self, asset: str, pos: CrossVenuePosition, is_buy: bool, use_taker: bool
    ) -> bool:
        """Close the HL perp leg. Returns True on success."""
        try:
            hl_is_short = pos.short_venue == "hl"
            reduce_label = "close_short" if hl_is_short else "close_long"

            if use_taker:
                book = await self._hl.get_l2_book(asset)
                levels = book.get("levels", [[], []])
                if is_buy:
                    raw_px = float(levels[1][0]["px"]) * 1.005 if levels[1] else pos.hl_entry_price * 1.02
                else:
                    raw_px = float(levels[0][0]["px"]) * 0.995 if levels[0] else pos.hl_entry_price * 0.98
                px = self._hl.round_price(raw_px)
                result = await self._hl.place_taker(
                    asset, is_buy=is_buy, size=pos.units, price=px, reduce_only=True
                )
                pos.exit_fee_usd += pos.notional_usd * FEE_PERP_TAKER
                return result is not None
            else:
                # Maker exit — repost up to MAKER_CHASE_TIMEOUT
                order_id = await self._hl.maker_chase_entry(
                    asset=asset,
                    is_buy=is_buy,
                    size=pos.units,
                    side_label=f"close_hl_{reduce_label}",
                )
                if order_id is None:
                    # Fallback to taker
                    return await self._close_hl_leg(asset, pos, is_buy, use_taker=True)
                pos.exit_fee_usd += pos.notional_usd * FEE_PERP_MAKER
                return True
        except Exception as e:
            crash_log(f"CrossVenue._close_hl_leg.{asset}", e)
            return False

    async def _close_lt_leg(
        self, asset: str, pos: CrossVenuePosition, is_buy: bool, use_taker: bool
    ) -> bool:
        """Close the Lighter perp leg. Returns True on success."""
        try:
            if use_taker:
                bid, ask = await self._lt.get_best_prices(asset)
                price = ask * 1.005 if is_buy else bid * 0.995
                result = await self._lt.place_taker(
                    asset, is_buy=is_buy, size=pos.units, price=price, reduce_only=True
                )
                pos.exit_fee_usd += 0.0  # Lighter 0% taker
                return result is not None
            else:
                order_id = await self._lt.maker_chase_entry(
                    asset=asset,
                    is_buy=is_buy,
                    size=pos.units,
                    label="close_lt",
                    timeout_s=MAKER_CHASE_TIMEOUT_S,
                    closing=True,
                    reduce_only=True,
                )
                if order_id is None:
                    return await self._close_lt_leg(asset, pos, is_buy, use_taker=True)

                # Post-close verification — confirm position actually went to ~0.
                # Belt-and-suspenders against any future fill-detection regressions.
                try:
                    lt_pos_after = await self._lt.get_position_for_asset(asset)
                    remaining = abs(float(
                        (lt_pos_after or {}).get("size", 0)
                        or (lt_pos_after or {}).get("base_amount", 0) or 0
                    ))
                except Exception:
                    remaining = 0.0
                if remaining > pos.units * 0.1:
                    log_warn(
                        f"CrossVenue: {asset} Lighter close reported success but "
                        f"{remaining:.6f} still open — falling back to taker"
                    )
                    return await self._close_lt_leg(asset, pos, is_buy, use_taker=True)

                pos.exit_fee_usd += 0.0  # Lighter 0% maker
                return True
        except Exception as e:
            crash_log(f"CrossVenue._close_lt_leg.{asset}", e)
            return False

    async def _cover_naked_hl_leg(
        self, asset: str, pos: CrossVenuePosition
    ) -> None:
        """Close HL leg with taker when Lighter leg failed to fill (legging cover)."""
        hl_is_short = pos.short_venue == "hl"
        is_buy = hl_is_short  # buy back to close short; sell to close long
        try:
            book = await self._hl.get_l2_book(asset)
            levels = book.get("levels", [[], []])
            if is_buy:
                raw_px = float(levels[1][0]["px"]) * 1.005 if levels[1] else pos.hl_entry_price * 1.02
            else:
                raw_px = float(levels[0][0]["px"]) * 0.995 if levels[0] else pos.hl_entry_price * 0.98
            px = self._hl.round_price(raw_px)
            await self._hl.place_taker(
                asset, is_buy=is_buy, size=pos.units, price=px, reduce_only=True
            )
            log(f"CrossVenue: {asset} naked HL leg covered @ {px:.4f}")
        except Exception as e:
            crash_log(f"CrossVenue._cover_naked_hl_leg.{asset}", e)

    # ── Rebalancing check ─────────────────────────────────────────────────────

    async def _check_rebalance(self) -> None:
        """Alert if margin on either exchange is getting thin. Auto-close if emergency persists."""
        if not self.positions:
            self._emergency_since.clear()
            return
        try:
            hl_margin, lt_margin = await asyncio.gather(
                self._hl_margin_ratio(),
                self._lt.get_margin_ratio(),
                return_exceptions=True,
            )

            now = int(time.time())
            for venue, ratio in [("HL", hl_margin), ("Lighter", lt_margin)]:
                if isinstance(ratio, Exception):
                    continue

                if ratio >= REBALANCE_MARGIN_EMERGENCY:
                    if venue not in self._emergency_since:
                        # First time entering emergency — alert immediately
                        self._emergency_since[venue] = now
                        await self._notify(
                            f"🚨 <b>REBALANCE URGENT: {venue}</b>\n"
                            f"Margin usage: {ratio*100:.1f}%\n"
                            f"Action: bridge USDC to {venue} via Arbitrum NOW.\n"
                            f"HL: hyperliquid.xyz → Withdraw to Arbitrum\n"
                            f"Lighter: lighter.xyz → Deposit from Arbitrum\n"
                            f"Auto-close in {_EMERGENCY_CLOSE_AFTER_S//60} min if unresolved."
                        )
                    else:
                        elapsed = now - self._emergency_since[venue]
                        if elapsed >= _EMERGENCY_CLOSE_AFTER_S:
                            # Emergency unresolved for 30 min — auto-close all positions
                            log_warn(
                                f"CrossVenue: {venue} margin emergency for "
                                f"{elapsed//60}min — emergency closing all positions"
                            )
                            await self._notify(
                                f"🔴 <b>AUTO-CLOSE: {venue} margin emergency {elapsed//60}min</b>\n"
                                f"Closing all cross-venue positions (taker).\n"
                                f"Please bridge USDC and restart the bot manually."
                            )
                            for asset, pos in list(self.positions.items()):
                                if pos.state == CrossVenueState.HOLD:
                                    await self._close_position(
                                        asset, pos, reason="emergency_margin", use_taker=True
                                    )
                            self._emergency_since.pop(venue, None)

                elif ratio >= REBALANCE_MARGIN_WARN:
                    self._emergency_since.pop(venue, None)  # reset if recovered from emergency
                    await self._notify(
                        f"⚠️ <b>Rebalance warning: {venue}</b>\n"
                        f"Margin usage: {ratio*100:.1f}%\n"
                        f"Consider bridging USDC to {venue} soon."
                    )
                else:
                    # Healthy — clear emergency timer if it was set
                    self._emergency_since.pop(venue, None)

        except Exception as e:
            crash_log("CrossVenue._check_rebalance", e)

    async def _hl_margin_ratio(self) -> float:
        """
        Compute HL margin usage ratio: totalMarginUsed / totalEquity.

        On unified HL accounts, USDC sits in spot — NOT in the perp account.
        This means marginSummary.accountValue ≈ $0-2 (residual perp-side P&L),
        while actual capital is in spot. Dividing tiny margin_used by this near-zero
        denominator produces a falsely high ratio and triggers spurious emergencies.

        Fix: if accountValue < margin_used (nonsensical) or < $10 (suspiciously small),
        fall back to the actual USDC spot balance as the denominator.
        """
        try:
            state = await self._hl.get_clearinghouse()
            # Try crossMarginSummary first (more accurate for cross-margin perp positions)
            margin_summary = (
                state.get("crossMarginSummary")
                or state.get("marginSummary")
                or {}
            )
            account_value = float(margin_summary.get("accountValue", 0) or 0)
            margin_used   = float(margin_summary.get("totalMarginUsed", 0) or 0)

            # Unified account guard: if accountValue looks wrong, use spot USDC balance
            if account_value < 10.0 or account_value < margin_used:
                account_value = await self._hl.get_usdc_balance()

            if account_value <= 0:
                return 0.0
            ratio = margin_used / account_value
            from core.logger import log as _log
            _log(
                f"HL margin check: used=${margin_used:.2f} equity=${account_value:.2f} "
                f"ratio={ratio*100:.1f}%"
            )
            return ratio
        except Exception as e:
            crash_log("CrossVenue._hl_margin_ratio", e)
            return 0.0

    # ── Funding payment accounting ────────────────────────────────────────────

    async def record_hl_funding(self, asset: str, amount: float, time_ms: int = 0) -> None:
        """Called by WS handler for HL userFundings events.

        Idempotent against ``hl_last_funding_time_ms`` (the SAME anchor the REST
        poll uses) — only credits events strictly newer than the last one counted.
        BUG-042: HL replays the full funding history (``isSnapshot``) on every WS
        (re)connect; the old blind ``+=`` re-added the entire history on each of the
        ~13 reconnects, inflating hl_funding_collected ~13× (APT: real $0.012 stored
        as $0.157). Same failure class as BUG-017 on the HL-only side. Events without
        a timestamp are dropped here — the REST poll is ground truth and will catch them.
        """
        pos = self.positions.get(asset)
        if pos is None or pos.state != CrossVenueState.HOLD:
            return
        if not time_ms or time_ms <= pos.hl_last_funding_time_ms:
            return
        # HL short earns positive funding, HL long pays negative
        pos.hl_funding_collected += amount
        pos.hl_last_funding_time_ms = time_ms
        self._stats.total_funding_usd += amount
        await self._db_update_funding(pos)

    async def poll_lighter_funding(self) -> None:
        """
        Poll BOTH venues for new funding via REST. Called every LT_FUNDING_POLL seconds.

        HL leg  — reads cumFunding.sinceOpen from clearinghouse REST.  We store the
                   absolute total received since position opened and compute deltas, so
                   this is safe to call even when WebSocket is also delivering events
                   (WS events set hl_funding_collected directly; REST resets to ground
                   truth).  Net effect: if WS works, no stat double-count; if WS is
                   blocked, REST picks up the full amount.

        Lighter leg — Lighter has no funding history endpoint.  We compute theoretical
                      funding per position: sign × rate × notional_usd × elapsed_hours.
                      Rate is read from the scanner snapshot (refreshed every 60s).
                      This is an approximation (rate changes between polls) but is
                      correct on average over hourly settlement periods and works for
                      any number of simultaneous positions.
        """
        if not self.positions:
            return

        # ── HL: real settled payments via userFunding REST ───────────────────
        # Uses HL's userFunding endpoint (same data as the HL UI "Funding" tab).
        # Returns actual USD amounts — not rates — so no approximation needed.
        # Idempotency: hl_last_funding_time_ms tracks the last event time credited.
        # Survives restarts because it's persisted to DB and loaded in recovery.
        # Replaces cumFunding.sinceOpen which resets on every HL leg close/reopen
        # (legging covers, restart recovery) causing chronic undercounting (BUG-038).
        try:
            for asset, pos in list(self.positions.items()):
                if pos.state != CrossVenueState.HOLD:
                    continue
                since_ms = pos.hl_last_funding_time_ms or (int(pos.entered_at or 0) * 1000)
                events = await self._hl.get_user_funding_history(asset, since_ms=since_ms)
                if not events:
                    continue
                new_total = 0.0
                max_time_ms = pos.hl_last_funding_time_ms
                for ev in events:
                    if ev["time_ms"] <= pos.hl_last_funding_time_ms:
                        continue
                    new_total += ev["usdc"]
                    if ev["time_ms"] > max_time_ms:
                        max_time_ms = ev["time_ms"]
                    log(
                        f"HLFunding[settled]: {asset} ${ev['usdc']:+.6f} "
                        f"rate={ev['rate']*100:.4f}%/h szi={ev['szi']}"
                    )
                if max_time_ms > pos.hl_last_funding_time_ms:
                    pos.hl_funding_collected += new_total
                    pos.hl_last_funding_time_ms = max_time_ms
                    self._stats.total_funding_usd += new_total
                    await self._db_update_funding(pos)
        except Exception as e:
            crash_log("CrossVenue.poll_hl_funding_rest", e)

        # ── Lighter: REAL settlement events via positionFunding API ──────────
        # Replaces the inaccurate rate×time accounting that was reading instantaneous
        # rate (premium/8) and overestimating by ~3-3.5×. Lighter settles each hour
        # as TWAP of 60 per-minute premiums — only the API exposes the real number.
        #
        # Idempotency: lighter_last_funding_id tracks the highest funding_id we've
        # credited. Restart-safe — recovery loads it from DB, so re-polling cannot
        # double-credit. API returns ALL fundings for the market; we filter by
        # entered_at and funding_id > last_seen.
        try:
            for asset, pos in list(self.positions.items()):
                if pos.state != CrossVenueState.HOLD:
                    continue
                # `change` field convention: API returns positive when the position
                # EARNED (regardless of side). Confirmed by NEAR SHORT events showing
                # change=+0.001229 matching the exchange UI ($+0.001229).
                fundings = await self._lt.get_position_fundings(
                    asset, since_ts=pos.entered_at or 0
                )
                if not fundings:
                    continue
                new_total = 0.0
                max_id = pos.lighter_last_funding_id
                for f in fundings:
                    fid = f["funding_id"]
                    if fid <= pos.lighter_last_funding_id:
                        continue
                    amount = f["change_usd"]
                    new_total += amount
                    if fid > max_id:
                        max_id = fid
                    log(
                        f"LighterFunding[settled]: {asset} ${amount:+.6f} "
                        f"rate={f['rate']*100:.4f}%/h side={f['side']} "
                        f"id={fid} ts={f['timestamp']}"
                    )
                if max_id > pos.lighter_last_funding_id:
                    pos.lighter_funding_collected += new_total
                    pos.lighter_last_funding_id = max_id
                    self._stats.total_funding_usd += new_total
                    await self._db_update_funding(pos)
        except Exception as e:
            crash_log("CrossVenue.poll_lighter_funding_settled", e)

    # ── Database helpers ──────────────────────────────────────────────────────

    async def _db_insert_cycle(self, pos: CrossVenuePosition) -> None:
        try:
            async with get_db() as conn:
                await conn.execute(
                    """INSERT OR IGNORE INTO cross_venue_cycles
                       (id, asset, short_venue, long_venue, entered_at, notional_usd,
                        units, hl_entry_price, lighter_entry_price,
                        hl_rate_at_entry, lighter_rate_at_entry, spread_at_entry,
                        entry_fee_usd, hl_last_funding_time_ms, state)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        pos.cycle_id, pos.asset,
                        pos.short_venue, pos.long_venue,
                        pos.entered_at, pos.notional_usd,
                        pos.units, pos.hl_entry_price, pos.lighter_entry_price,
                        pos.hl_rate_at_entry, pos.lighter_rate_at_entry,
                        pos.spread_at_entry, pos.entry_fee_usd,
                        pos.hl_last_funding_time_ms,
                        "OPEN",
                    ),
                )
                await conn.commit()
        except Exception as e:
            crash_log("CrossVenue._db_insert_cycle", e)

    async def _db_update_cycle(self, pos: CrossVenuePosition, reason: str) -> None:
        try:
            async with get_db() as conn:
                await conn.execute(
                    """UPDATE cross_venue_cycles SET
                       state='CLOSED', exit_reason=?, exited_at=?,
                       exit_fee_usd=?, hl_funding_collected=?,
                       lighter_funding_collected=?, net_pnl_usd=?
                       WHERE id=?""",
                    (
                        reason, pos.exited_at,
                        pos.exit_fee_usd,
                        pos.hl_funding_collected,
                        pos.lighter_funding_collected,
                        pos.total_pnl_usd,
                        pos.cycle_id,
                    ),
                )
                await conn.commit()
        except Exception as e:
            crash_log("CrossVenue._db_update_cycle", e)

    async def _db_update_funding(self, pos: CrossVenuePosition) -> None:
        try:
            async with get_db() as conn:
                await conn.execute(
                    "UPDATE cross_venue_cycles SET "
                    "hl_funding_collected=?, lighter_funding_collected=?, "
                    "lighter_last_funding_id=?, hl_last_funding_time_ms=? WHERE id=?",
                    (
                        pos.hl_funding_collected,
                        pos.lighter_funding_collected,
                        pos.lighter_last_funding_id,
                        pos.hl_last_funding_time_ms,
                        pos.cycle_id,
                    ),
                )
                await conn.commit()
        except Exception as e:
            crash_log("CrossVenue._db_update_funding", e)

    # ── Drift verification (called every DRIFT_CHECK_INTERVAL_S by main loop) ─

    async def check_position_drift(self) -> None:
        """
        Reconcile each booked HOLD position against actual sizes on both venues.
        Alerts (and optionally cooldowns the asset for safety) if a leg has
        drifted from the booked size beyond DRIFT_TOLERANCE_PCT.

        This catches: partial fills booked as full, silent liquidations,
        manual UI interventions, or one-leg orphan from a missed exit fallback.
        """
        if not self.positions:
            return
        for asset, pos in list(self.positions.items()):
            if pos.state != CrossVenueState.HOLD:
                continue
            try:
                hl_size = await self._hl.get_perp_position_size(asset)
                # use raise-variant so HTTP errors (403/503) propagate to the
                # except below and skip this tick — avoids false drift_orphan close
                lt_size = await self._lt.get_position_size_or_raise(asset)
            except Exception as e:
                crash_log(f"CrossVenue.drift_check.{asset}.fetch", e)
                continue

            expected = pos.units
            if expected <= 0:
                continue
            hl_drift = abs(hl_size - expected) / expected
            lt_drift = abs(lt_size - expected) / expected

            if hl_drift > DRIFT_TOLERANCE_PCT or lt_drift > DRIFT_TOLERANCE_PCT:
                log_warn(
                    f"CrossVenue: {asset} DRIFT | booked={expected:.6f} "
                    f"HL={hl_size:.6f} ({hl_drift*100:.1f}%) "
                    f"LT={lt_size:.6f} ({lt_drift*100:.1f}%)"
                )
                now_f = time.time()
                if now_f - self._last_drift_alert.get(asset, 0) >= self._DRIFT_ALERT_COOLDOWN_S:
                    self._last_drift_alert[asset] = now_f
                    await self._notify(
                        f"⚠️ <b>Position drift: {asset}</b>\n"
                        f"Booked: {expected:.6f}\n"
                        f"HL actual: {hl_size:.6f} (Δ {hl_drift*100:.1f}%)\n"
                        f"LT actual: {lt_size:.6f} (Δ {lt_drift*100:.1f}%)\n"
                        f"Manual check recommended."
                    )

                # If one leg is completely missing → other leg is naked → emergency close
                missing_threshold = 0.5  # leg < 50% of booked = effectively gone
                if hl_size < expected * missing_threshold or lt_size < expected * missing_threshold:
                    log_warn(f"CrossVenue: {asset} leg missing — emergency taker close")
                    await self._close_position(
                        asset, pos, reason="drift_orphan", use_taker=True
                    )
                    continue

                # Over-sized Lighter leg → excess is unhedged directional risk. Trim it
                # back to the booked size with a reduce-only taker immediately, instead of
                # holding it until exit (the gap that left MON 2× unhedged for 15 min on
                # 2026-06-05: a maker_chase cancel-race double-filled the Lighter leg).
                # reduce_only can only shrink the position, never flip it.
                lt_excess = lt_size - expected
                if lt_excess > expected * DRIFT_TOLERANCE_PCT:
                    try:
                        reduce_buy_lt = not (pos.short_venue == "hl")
                        lt_bid, lt_ask = await self._lt.get_best_prices(asset)
                        px = (lt_ask * 1.05 if reduce_buy_lt else lt_bid * 0.95)
                        await self._lt.place_taker(
                            asset, is_buy=reduce_buy_lt, size=lt_excess, price=px, reduce_only=True
                        )
                        log_warn(
                            f"CrossVenue: {asset} Lighter leg OVER-sized "
                            f"({lt_size:.4f} vs booked {expected:.4f}) — trimmed "
                            f"{lt_excess:.4f} reduce-only to restore delta-neutral"
                        )
                        await self._notify(
                            f"🔧 <b>{asset} over-sized leg trimmed</b>\n"
                            f"Lighter {lt_size:.4f} → ~{expected:.4f} (excess {lt_excess:.4f}). "
                            f"Likely a maker double-fill; delta-neutral restored."
                        )
                    except Exception as e:
                        crash_log(f"CrossVenue.drift_trim.{asset}", e)

    # ── Exchange-truth reconciliation (P0) ────────────────────────────────────

    async def _hl_all_positions(self) -> Dict[str, float]:
        """All nonzero HL perp positions → {asset: signed_size}. Raises on error."""
        ch = await self._hl.get_clearinghouse()
        out: Dict[str, float] = {}
        for ap in ch.get("assetPositions", []):
            p = ap.get("position", {})
            coin = p.get("coin")
            szi = float(p.get("szi", 0) or 0)
            if coin and abs(szi) > 1e-9:
                out[coin] = szi
        return out

    async def _lt_all_positions(self) -> Dict[str, float]:
        """All nonzero Lighter positions → {asset: signed_size}. Raises on error."""
        acct = await self._lt.get_account()
        out: Dict[str, float] = {}
        for p in acct.get("positions", []):
            sym = p.get("symbol")
            size = abs(float(p.get("position", 0) or 0))
            sign = int(p.get("sign", 1) or 1)  # 1 long, -1 short
            if sym and size > 1e-9:
                out[sym] = size * sign
        return out

    async def _cancel_all_resting_orders(self) -> None:
        """Cancel every resting HL order (all assets). Used at recovery start so a
        leftover maker close/entry order cannot fill after we snapshot positions."""
        try:
            open_orders = await self._hl.get_open_orders()
        except Exception as e:
            crash_log("CrossVenue._cancel_all_resting_orders.list", e)
            return
        coins = {o.get("coin") for o in open_orders if o.get("coin")}
        for coin in coins:
            try:
                await self._hl.cancel_all(coin)
                log(f"CrossVenue.reconcile: cancelled resting HL orders on {coin}")
            except Exception as e:
                crash_log(f"CrossVenue._cancel_all_resting_orders.{coin}", e)

    async def reconcile_orphans(self) -> None:
        """P0 — Exchange-truth sweep. Treats BOTH exchanges as source of truth and
        flattens any nonzero position that is NOT part of a tracked cross-venue cycle.

        This is the safety net the per-position drift check cannot provide: drift only
        iterates self.positions, so a leg left behind after a cycle is ABANDONED/CLOSED
        (resting-order race, duplicate open, ghost desync) becomes invisible and lives
        on the exchange as naked directional risk until a human notices. The sweep sees
        it because it asks the exchanges, not the bot's own bookkeeping.

        Safety: (1) gated until startup recovery finished; (2) skips any asset we are
        actively tracking (ENTERING/HOLD/EXITING); (3) 2-strike debounce so a transient
        or partial API read can never trigger a wrong flatten.
        """
        if not self._recovery_done:
            return
        try:
            hl_pos = await self._hl_all_positions()
            lt_pos = await self._lt_all_positions()
        except Exception as e:
            # Any fetch error → skip this sweep entirely (never act on partial data)
            crash_log("CrossVenue.reconcile_orphans.fetch", e)
            return

        tracked = {
            a for a, p in self.positions.items()
            if p.state in (
                CrossVenueState.ENTERING, CrossVenueState.HOLD, CrossVenueState.EXITING
            )
        }
        assets = set(hl_pos) | set(lt_pos)
        for asset in assets:
            if asset in tracked:
                self._orphan_strikes.pop(asset, None)
                continue
            hl_sz = hl_pos.get(asset, 0.0)
            lt_sz = lt_pos.get(asset, 0.0)
            if abs(hl_sz) < 1e-9 and abs(lt_sz) < 1e-9:
                self._orphan_strikes.pop(asset, None)
                continue

            # Untracked nonzero leg(s). Debounce before acting.
            strikes = self._orphan_strikes.get(asset, 0) + 1
            self._orphan_strikes[asset] = strikes
            log_warn(
                f"CrossVenue.reconcile: ORPHAN {asset} (untracked) "
                f"HL={hl_sz:.6f} LT={lt_sz:.6f} strike {strikes}/{self._ORPHAN_STRIKE_LIMIT}"
            )
            if strikes < self._ORPHAN_STRIKE_LIMIT:
                continue
            self._orphan_strikes.pop(asset, None)

            await self._notify(
                f"🧹 <b>Orphan flatten: {asset}</b>\n"
                f"Untracked exchange position (no active cycle).\n"
                f"HL: {hl_sz:.6f} | Lighter: {lt_sz:.6f}\n"
                f"Closing both legs reduce-only (taker)."
            )
            # Flatten each nonzero leg with a reduce-only taker order.
            if abs(hl_sz) > 1e-9:
                try:
                    is_buy = hl_sz < 0  # short → buy to close; long → sell
                    book = await self._hl.get_l2_book(asset)
                    levels = book.get("levels", [[], []])
                    if is_buy:
                        raw = float(levels[1][0]["px"]) * 1.01 if levels[1] else 0.0
                    else:
                        raw = float(levels[0][0]["px"]) * 0.99 if levels[0] else 0.0
                    if raw > 0:
                        px = self._hl.round_price(raw)
                        await self._hl.place_taker(
                            asset, is_buy=is_buy, size=abs(hl_sz), price=px, reduce_only=True
                        )
                        log(f"CrossVenue.reconcile: {asset} HL orphan leg flattened (taker)")
                except Exception as e:
                    crash_log(f"CrossVenue.reconcile.flatten_hl.{asset}", e)
            if abs(lt_sz) > 1e-9:
                try:
                    is_buy = lt_sz < 0  # short → buy to close; long → sell
                    lt_bid, lt_ask = await self._lt.get_best_prices(asset)
                    if is_buy:
                        px = lt_ask * 1.05 if lt_ask > 0 else 999999.0
                    else:
                        px = lt_bid * 0.95 if lt_bid > 0 else 0.0001
                    await self._lt.place_taker(
                        asset, is_buy=is_buy, size=abs(lt_sz), price=px, reduce_only=True
                    )
                    log(f"CrossVenue.reconcile: {asset} Lighter orphan leg flattened (taker)")
                except Exception as e:
                    crash_log(f"CrossVenue.reconcile.flatten_lt.{asset}", e)

    # ── Utilities ─────────────────────────────────────────────────────────────

    def _set_cooldown(self, asset: str, cooldown_s: int = CROSS_EXIT_COOLDOWN_S) -> None:
        self._entry_cooldowns[asset] = int(time.time()) + cooldown_s

    async def _register_entry_fail(self, asset: str, reason: str) -> None:
        """Count consecutive entry failures (HL timeout OR Lighter ghost) per asset.
        After STREAK_LIMIT in a row, blacklist the asset for several hours so we
        stop burning cover fees / busy-looping on an asset that won't fill."""
        streak = self._entry_fail_streak.get(asset, 0) + 1
        self._entry_fail_streak[asset] = streak
        if streak >= self._ENTRY_FAIL_STREAK_LIMIT:
            cooldown_s = self._ENTRY_FAIL_LONG_COOLDOWN_S
            self._entry_fail_streak[asset] = 0  # reset after long cooldown
            log_warn(
                f"CrossVenue: {asset} entry-fail streak {streak}× ({reason}) — "
                f"blacklisting {cooldown_s//3600}h to stop fee drain"
            )
            await self._notify(
                f"⚠️ <b>{asset} entry failed ×{streak}</b>\n"
                f"Last: {reason}\n"
                f"Blacklisting {cooldown_s//3600}h — can't fill cleanly as maker."
            )
        else:
            cooldown_s = CROSS_EXIT_COOLDOWN_S
            await self._notify(
                f"⚠️ <b>CrossVenue: {asset} entry failed</b>\n"
                f"{reason}. Streak={streak}/{self._ENTRY_FAIL_STREAK_LIMIT}. "
                f"Cooldown {cooldown_s}s."
            )
        self._entry_cooldowns[asset] = int(time.time()) + cooldown_s

    async def _notify(self, msg: str) -> None:
        if self._notifier:
            try:
                await self._notifier.send_alert(msg)
            except Exception:
                pass
