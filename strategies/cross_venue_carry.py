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
    CROSS_EXIT_COOLDOWN_S,
    MAKER_CHASE_TIMEOUT_S,
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
                candidates = self._scanner.best_candidates(
                    n=CROSS_MAX_POSITIONS - open_count,
                    exclude=set(self.positions.keys()),
                )
                for snap in candidates:
                    if now < self._entry_cooldowns.get(snap.asset, 0):
                        continue
                    await self._enter_position(snap)

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

        # 4. Anti-spike: reject if spread is far above its rolling mean (sigma filter).
        # Funding spikes usually mean-revert within a few hours — entering at the peak
        # locks you into fees with no expected funding income after the snap-back.
        try:
            is_spike, sigma_reason = self._scanner.is_spike(asset, snap)
            if is_spike:
                log_warn(f"CrossVenue: {asset} SIGMA SPIKE — skipping ({sigma_reason})")
                self._set_cooldown(asset)
                return
        except Exception as e:
            crash_log(f"CrossVenue.sigma_check.{asset}", e)
            # On error, do not block entry — fail open

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
            # Fallback: get from order book
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
            log_warn(f"CrossVenue: cannot determine price for {asset}, skipping")
            return

        units = CROSS_POSITION_SIZE_USD / mark_price

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
            label=hl_label,
        )

        if hl_order_id is None:
            log_warn(f"CrossVenue: {asset} HL leg failed — aborting entry")
            self._set_cooldown(asset)
            del self.positions[asset]
            return

        pos.hl_order_id = hl_order_id
        pos.hl_entry_price = mark_price  # approximate; actual fill logged by HL
        pos.entry_fee_usd += CROSS_POSITION_SIZE_USD * FEE_PERP_MAKER
        log(f"CrossVenue: {asset} HL leg filled — now opening Lighter leg")

        # ── Step 2: Open Lighter leg ──────────────────────────────────────────
        lt_label = f"Lighter {'short' if not lt_is_buy else 'long'}"
        lt_order_id = await self._lt.maker_chase_entry(
            asset=asset,
            is_buy=lt_is_buy,
            size=units,
            label=lt_label,
            timeout_s=MAKER_CHASE_TIMEOUT_S,
        )

        if lt_order_id is None:
            log_warn(f"CrossVenue: {asset} Lighter leg failed — covering HL leg")
            await self._cover_naked_hl_leg(asset, pos)
            self._set_cooldown(asset)
            del self.positions[asset]
            await self._notify(
                f"⚠️ <b>CrossVenue: {asset} entry failed</b>\n"
                f"Lighter leg timed out — HL leg covered. Cooldown {CROSS_EXIT_COOLDOWN_S}s."
            )
            return

        # ── Both legs filled ──────────────────────────────────────────────────
        lt_bid, lt_ask = await self._lt.get_best_prices(asset)
        pos.lighter_entry_price = lt_ask if lt_is_buy else lt_bid
        pos.lighter_order_id = lt_order_id
        pos.entry_fee_usd += 0.0  # Lighter maker fee = 0%
        pos.mark_entered()

        # Persist to DB
        cycle_id = str(uuid.uuid4())
        pos.cycle_id = cycle_id
        await self._db_insert_cycle(pos)

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
                        f"(HL={hl_mid:.4f} LT={lt_mid:.4f}) > {PRICE_DIVERGENCE_KILL_PCT*100:.1f}% — taker exit"
                    )
                    await self._notify(
                        f"⚠️ <b>Price divergence: {asset}</b>\n"
                        f"HL mid: {hl_mid:.4f} | LT mid: {lt_mid:.4f}\n"
                        f"Divergence: {divergence*100:.2f}% — closing taker"
                    )
                    await self._close_position(asset, pos, reason="price_divergence", use_taker=True)
                    return
        except Exception as e:
            crash_log(f"CrossVenue.divergence_check.{asset}", e)

        # Check spread conditions
        current_spread = snap.spread
        # Effective spread sign: positive if short_venue still has higher rate
        if snap.short_venue != pos.short_venue:
            # Spread flipped sign — the venue we shorted now has LOWER rate
            effective_spread = -current_spread
        else:
            effective_spread = current_spread

        # 1. Hard exit: spread flipped, we're now paying net funding
        if effective_spread <= SPREAD_EXIT_FLIP and hold_h >= 0.25:
            log(f"CrossVenue: {asset} SPREAD FLIP exit | effective={effective_spread*100:.4f}%/h")
            await self._close_position(asset, pos, reason="spread_flip", use_taker=True)
            return

        # 2. Soft exit: spread too small to justify staying
        if effective_spread <= SPREAD_EXIT_THRESHOLD and hold_h >= CROSS_MIN_HOLD_HOURS:
            log(f"CrossVenue: {asset} SOFT EXIT | spread={effective_spread*100:.4f}%/h < threshold")
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
    ) -> None:
        """Close both legs of a cross-venue position."""
        if pos.state == CrossVenueState.EXITING:
            return
        pos.state = CrossVenueState.EXITING

        hl_is_short  = pos.short_venue == "hl"
        hl_close_buy = hl_is_short   # close short → buy back; close long → sell
        lt_close_buy = not hl_close_buy

        log(f"CrossVenue: closing {asset} | reason={reason} taker={use_taker}")

        # Close HL leg
        hl_ok = await self._close_hl_leg(asset, pos, hl_close_buy, use_taker)

        # Close Lighter leg
        lt_ok = await self._close_lt_leg(asset, pos, lt_close_buy, use_taker)

        if not hl_ok:
            log_warn(f"CrossVenue: {asset} HL close FAILED — may need manual intervention")
        if not lt_ok:
            log_warn(f"CrossVenue: {asset} Lighter close FAILED — may need manual intervention")

        # Finalize
        pos.mark_exited()
        await self._db_update_cycle(pos, reason)

        self._stats.total_pnl_usd += pos.total_pnl_usd
        self._stats.total_funding_usd += pos.net_funding_usd
        self._stats.total_fees_usd += pos.entry_fee_usd + pos.exit_fee_usd
        if hl_ok and lt_ok:
            self._stats.successful_cycles += 1

        # Remove from active positions and set cooldown
        self.positions.pop(asset, None)
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
                    label=f"close_hl_{reduce_label}",
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
                )
                if order_id is None:
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
        """Compute HL margin usage ratio from clearinghouse state."""
        try:
            state = await self._hl.get_clearinghouse()
            margin_summary = state.get("marginSummary", {})
            account_value = float(margin_summary.get("accountValue", 0) or 0)
            margin_used = float(margin_summary.get("totalMarginUsed", 0) or 0)
            if account_value <= 0:
                return 0.0
            return margin_used / account_value
        except Exception as e:
            crash_log("CrossVenue._hl_margin_ratio", e)
            return 0.0

    # ── Funding payment accounting ────────────────────────────────────────────

    async def record_hl_funding(self, asset: str, amount: float) -> None:
        """Called by WS handler for HL userFundings events."""
        pos = self.positions.get(asset)
        if pos is None or pos.state != CrossVenueState.HOLD:
            return
        # HL short earns positive funding, HL long pays negative
        pos.hl_funding_collected += amount
        self._stats.total_funding_usd += amount
        await self._db_update_funding(pos)

    async def poll_lighter_funding(self) -> None:
        """
        Poll Lighter for new funding payments.
        Called periodically (e.g. every 5 min) since Lighter has 1h settlement.
        """
        if not self.positions:
            return
        try:
            lt_account = await self._lt.get_account()
            # TODO: implement parsing of Lighter's funding payment history once
            # the positionFunding endpoint format is confirmed.
            # For now, approximate from PnL delta.
        except Exception as e:
            crash_log("CrossVenue.poll_lighter_funding", e)

    # ── Database helpers ──────────────────────────────────────────────────────

    async def _db_insert_cycle(self, pos: CrossVenuePosition) -> None:
        try:
            async with get_db() as conn:
                await conn.execute(
                    """INSERT OR IGNORE INTO cross_venue_cycles
                       (id, asset, short_venue, long_venue, entered_at, notional_usd,
                        units, hl_entry_price, lighter_entry_price,
                        hl_rate_at_entry, lighter_rate_at_entry, spread_at_entry,
                        entry_fee_usd, state)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        pos.cycle_id, pos.asset,
                        pos.short_venue, pos.long_venue,
                        pos.entered_at, pos.notional_usd,
                        pos.units, pos.hl_entry_price, pos.lighter_entry_price,
                        pos.hl_rate_at_entry, pos.lighter_rate_at_entry,
                        pos.spread_at_entry, pos.entry_fee_usd,
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
                    "UPDATE cross_venue_cycles SET hl_funding_collected=? WHERE id=?",
                    (pos.hl_funding_collected, pos.cycle_id),
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
                lt_pos  = await self._lt.get_position_for_asset(asset)
                lt_size = abs(float(lt_pos.get("size", 0) or 0)) if lt_pos else 0.0
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

    # ── Utilities ─────────────────────────────────────────────────────────────

    def _set_cooldown(self, asset: str) -> None:
        self._entry_cooldowns[asset] = int(time.time()) + CROSS_EXIT_COOLDOWN_S

    async def _notify(self, msg: str) -> None:
        if self._notifier:
            try:
                await self._notifier.send_alert(msg)
            except Exception:
                pass
