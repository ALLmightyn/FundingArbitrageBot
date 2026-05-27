"""strategies/funding_carry.py — Funding Rate Carry state machine.

State flow per position:
  SCAN → QUALIFY → ENTER_MAKER → HOLD → EXIT_MAKER → COOLDOWN → SCAN

Perp-first entry protocol (Iron Dome spec):
  1. Place perp short maker (ALO at best ask).
  2. On perp fill → immediately place spot buy maker (ALO at best bid).
  3. If spot not filled within MAKER_CHASE_TIMEOUT_S → taker cover.
  4. If legging event → IronDome.cover_naked_leg().
"""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import Dict, List, Optional, Set, Tuple

from core.constants import (
    MAX_POSITIONS,
    POSITION_SIZE_USD,
    PERP_LEVERAGE,
    MIN_HOLD_HOURS,
    MAX_HOLD_HOURS,
    FUNDING_HOLD_MIN,
    FUNDING_ENTRY_THRESHOLD,
    FEE_ROUND_TRIP_ALL_MAKER,
    EXIT_COOLDOWN_SECONDS,
    MAKER_CHASE_TIMEOUT_S,
    SPOT_TAKER_FALLBACK_S,
    FEE_PERP_MAKER,
    FEE_PERP_TAKER,
    FEE_SPOT_MAKER,
    FEE_SPOT_TAKER,
)
from core.logger import log, log_warn, log_err, crash_log
from core.models import CarryPosition, CarryState, SessionStats, AssetState
from database.db import get_db
from feeds.funding_scanner import FundingScanner
from risk.iron_dome import IronDome
from venues.hyperliquid import HLClient


class CarryStrategy:
    """
    Manages up to MAX_POSITIONS concurrent delta-neutral carry positions.
    Each position is tracked in self.positions (asset → CarryPosition).
    """

    def __init__(
        self,
        client: HLClient,
        scanner: FundingScanner,
        iron_dome: IronDome,
        stats: SessionStats,
    ):
        self._client    = client
        self._scanner   = scanner
        self._dome      = iron_dome
        self._stats     = stats

        # asset → active CarryPosition
        self.positions: Dict[str, CarryPosition] = {}
        # asset → timestamp: skip opening until this time (failed entry cooldown)
        self._entry_cooldowns: Dict[str, int] = {}
        # BUG-017: dedup funding events. HL WS replays the full userFundings history
        # on every reconnect; (asset, paid_at_ms) uniquely identifies one HL event.
        self._seen_fundings: Set[Tuple[str, int]] = set()

    # ── Main tick (called by carry_loop) ──────────────────────────────────────

    async def tick(self) -> None:
        """
        One iteration of the strategy loop.
        1. Service existing positions (HOLD checks, EXIT triggers).
        2. Fill vacant slots with new positions from FundingScanner.
        """
        if not self._dome.is_active():
            remaining = self._dome.kill_resume_at - int(time.time())
            log_warn(f"CarryStrategy: kill-switch active, {remaining//60}min remaining")
            return

        # 1. Service active positions
        for asset in list(self.positions.keys()):
            await self._service_position(asset)

        # 2. Open new positions if slots available
        active_count = sum(
            1 for p in self.positions.values()
            if p.state not in (CarryState.COOLDOWN, CarryState.KILLED)
        )
        slots_available = MAX_POSITIONS - active_count

        if slots_available > 0:
            now = int(time.time())
            occupied = set(self.positions.keys())
            # Exclude cooldown assets so scanner returns valid fallbacks instead of skipping top-N
            cooling = {a for a, t in self._entry_cooldowns.items() if now < t}
            candidates = self._scanner.best_candidates(n=slots_available, exclude=occupied | cooling)
            for asset_state in candidates:
                asset = asset_state.asset
                await self._open_position(asset_state)

    # ── Position lifecycle ────────────────────────────────────────────────────

    async def _open_position(self, asset_state: AssetState) -> None:
        """Attempt to open a new carry position for the given asset.
        True delta-neutral: short N units on perp + long N units on spot pair.
        Skips asset entirely if no spot pair exists (impossible to hedge).
        """
        asset = asset_state.asset
        snap  = asset_state.funding_snapshot
        if snap is None:
            return

        # ── Spot pair availability gate ──────────────────────────────────────
        spot_pair = await self._client.get_spot_pair(asset)
        if spot_pair is None:
            log_warn(f"CarryStrategy: {asset} has NO spot pair — delta-neutral impossible, skipping forever")
            self._entry_cooldowns[asset] = int(time.time()) + 86400  # 24h ban
            return

        # ── Get spot mid price for sizing + sanity checks ────────────────────
        try:
            spot_book = await self._client.get_l2_book(spot_pair)
            spot_levels = spot_book.get("levels", [[], []])
            if not spot_levels[0] or not spot_levels[1]:
                log_warn(f"CarryStrategy: {asset} spot {spot_pair} book empty — skipping")
                self._entry_cooldowns[asset] = int(time.time()) + EXIT_COOLDOWN_SECONDS
                return
            spot_bid = float(spot_levels[0][0]["px"])
            spot_ask = float(spot_levels[1][0]["px"])
            spot_mid = (spot_bid + spot_ask) / 2
            # Sanity 1: spread must be reasonable
            spot_spread_pct = (spot_ask - spot_bid) / spot_bid
            if spot_spread_pct > 0.02:
                log_warn(f"CarryStrategy: {asset} spot spread {spot_spread_pct*100:.2f}% > 2% — skipping")
                self._entry_cooldowns[asset] = int(time.time()) + EXIT_COOLDOWN_SECONDS
                return
            # Sanity 2: spot mid must track perp mark within 5% (catch dislocated testnet pairs)
            if snap.mark_price > 0:
                divergence = abs(spot_mid - snap.mark_price) / snap.mark_price
                if divergence > 0.05:
                    log_warn(f"CarryStrategy: {asset} spot/perp divergence {divergence*100:.1f}% "
                             f"(spot={spot_mid:.4f} perp={snap.mark_price:.4f}) — skipping")
                    self._entry_cooldowns[asset] = int(time.time()) + EXIT_COOLDOWN_SECONDS
                    return
        except Exception as e:
            crash_log(f"open_position.spot_book.{asset}", e)
            self._entry_cooldowns[asset] = int(time.time()) + EXIT_COOLDOWN_SECONDS
            return

        # ── Sizing in base-asset UNITS (true delta-neutral) ──────────────────
        # N units short on perp + N units long on spot → flat delta.
        units = POSITION_SIZE_USD / max(snap.mark_price, 0.001)
        perp_notional = units * snap.mark_price
        spot_notional = units * spot_mid

        log(f"CarryStrategy: opening {asset} | predicted={snap.predicted_rate_1h*100:.4f}%/h "
            f"| size={units:.6f} {asset} | perp@{snap.mark_price:.4f} spot[{spot_pair}]@{spot_mid:.4f}")

        pos = CarryPosition(
            asset=asset,
            state=CarryState.ENTER_MAKER,
            spot_size_usd=spot_notional,
            perp_size_usd=perp_notional,
            perp_leverage=PERP_LEVERAGE,
            entry_funding_rate=snap.predicted_rate_1h,
            cycle_id=str(uuid.uuid4()),
        )
        # Stash spot pair on the position for later exit
        pos.spot_pair = spot_pair
        pos.units = units
        self.positions[asset] = pos

        # Set leverage before first order
        try:
            await self._client.set_leverage(asset, PERP_LEVERAGE, is_cross=False)
        except Exception as e:
            crash_log(f"open_position.set_leverage.{asset}", e)

        # ── PERP FIRST protocol ───────────────────────────────────────────────
        perp_oid = await self._client.maker_chase_entry(
            asset, is_buy=False, size=units, side_label="PERP_SHORT", timeout_s=90.0
        )

        if perp_oid is None:
            log_warn(f"CarryStrategy: {asset} perp entry failed (timeout/max reposts) — skipping")
            del self.positions[asset]
            self._entry_cooldowns[asset] = int(time.time()) + EXIT_COOLDOWN_SECONDS
            return

        pos.perp_order_id    = perp_oid
        pos.perp_entry_price = snap.mark_price
        pos.fee_paid_usd    += perp_notional * FEE_PERP_MAKER
        log(f"CarryStrategy: {asset} perp SHORT filled, now entering spot {spot_pair}...")

        # ── SPOT LEG (real spot, not perp long) ──────────────────────────────
        # Tight 20s window: if maker doesn't fill, we taker-buy immediately.
        # Slippage on spot taker < risk of holding a naked perp short.
        spot_oid = await self._client.maker_chase_entry(
            spot_pair, is_buy=True, size=units, side_label="SPOT_BUY",
            is_spot=True, timeout_s=SPOT_TAKER_FALLBACK_S,
        )

        spot_entry_fee = 0.0
        if spot_oid is None:
            # Taker fallback: hit the market to restore delta-neutrality
            log_warn(f"CarryStrategy: {asset} spot maker timeout ({SPOT_TAKER_FALLBACK_S}s) "
                     f"— taker fallback on {spot_pair}")
            spot_taker_ok = False
            try:
                spot_book = await self._client.get_l2_book(spot_pair)
                spot_lvls = spot_book.get("levels", [[], []])
                # Buy at ask + 0.5% slippage to guarantee fill
                if spot_lvls[1]:
                    fallback_px = self._client.round_price(float(spot_lvls[1][0]["px"]) * 1.005)
                else:
                    fallback_px = self._client.round_price(spot_mid * 1.01)
                taker_result = await self._client.place_taker(
                    spot_pair, is_buy=True, size=units, price=fallback_px, is_spot=True,
                )
                t_status = taker_result.get("response", {}).get("data", {}).get("statuses", [{}])[0]
                if "filled" in t_status:
                    fill_px = float(t_status["filled"].get("avgPx", fallback_px))
                    pos.spot_entry_price = fill_px
                    spot_entry_fee = spot_notional * FEE_SPOT_TAKER
                    spot_taker_ok = True
                    log(f"CarryStrategy: {asset} spot TAKER fill @ {fill_px:.4f} — delta-neutral restored")
                else:
                    log_err(f"CarryStrategy: {asset} spot taker also failed: {t_status}")
            except Exception as e:
                crash_log(f"open_position.spot_taker_fallback.{asset}", e)

            if not spot_taker_ok:
                log_err(f"CarryStrategy: {asset} spot taker failed — triggering legging cover")
                await self._dome.cover_naked_leg(asset, pos, filled_leg="perp")
                self._entry_cooldowns[asset] = int(time.time()) + EXIT_COOLDOWN_SECONDS
                if self._dome.killed:
                    pos.state = CarryState.KILLED
                else:
                    del self.positions[asset]
                return
        else:
            pos.spot_order_id = spot_oid
            pos.spot_entry_price = spot_mid
            spot_entry_fee = spot_notional * FEE_SPOT_MAKER

        pos.fee_paid_usd += spot_entry_fee
        pos.mark_entered()

        log(
            f"CarryStrategy: {asset} ENTERED — "
            f"perp@{pos.perp_entry_price:.4f} spot@{pos.spot_entry_price:.4f} "
            f"funding={snap.predicted_rate_1h*100:.4f}%/h"
        )

        await self._save_cycle_open(pos)
        self._stats.total_cycles += 1

    async def _service_position(self, asset: str) -> None:
        """Check a HOLD position for exit conditions."""
        pos = self.positions.get(asset)
        if pos is None or pos.state != CarryState.HOLD:
            return

        # Funding watchdog
        exit_reason = await self._dome.check_funding_exit(asset, pos)

        # Time-based exit
        if exit_reason is None and pos.hold_hours >= MAX_HOLD_HOURS:
            exit_reason = "max_hold"

        # Min hold guard (don't exit early unless flip)
        if exit_reason and exit_reason != "funding_flip" and pos.hold_hours < MIN_HOLD_HOURS:
            log(f"CarryStrategy: {asset} exit deferred (hold_hours={pos.hold_hours:.1f}h < {MIN_HOLD_HOURS}h min)")
            return

        if exit_reason:
            await self._close_position(asset, pos, reason=exit_reason)

    async def _close_position(self, asset: str, pos: CarryPosition, reason: str) -> None:
        """Execute maker exit for both legs (perp close → spot sell).
        Tracks exit fees on both legs into pos.fee_paid_usd.
        Uses pos.units (base-asset size) and pos.spot_pair (real spot routing).
        """
        log(f"CarryStrategy: closing {asset}, reason={reason}")
        pos.state = CarryState.EXIT_MAKER

        snap = self._scanner.get_snapshot(asset)
        mark = snap.mark_price if snap else pos.perp_entry_price
        spot_pair = getattr(pos, "spot_pair", None) or await self._client.get_spot_pair(asset)
        units = getattr(pos, "units", None) or (pos.perp_size_usd / max(pos.perp_entry_price, 0.001))

        # If funding flip → use emergency taker exit
        if reason == "funding_flip":
            await self._dome.emergency_flat(asset, pos, reason=reason)
            # Exit fees: taker on both legs
            pos.fee_paid_usd += units * mark * FEE_PERP_TAKER
            if spot_pair:
                pos.fee_paid_usd += pos.spot_size_usd * FEE_SPOT_TAKER
            await self._finalize_close(asset, pos, reason)
            return

        # ── Perp close (reduce-only maker) ──────────────────────────────────
        perp_close_oid = await self._client.maker_chase_entry(
            asset, is_buy=True, size=units, side_label="PERP_CLOSE", reduce_only=True
        )
        perp_close_was_taker = False
        if perp_close_oid is None:
            log_warn(f"CarryStrategy: {asset} perp close timed out — using taker fallback")
            book = await self._client.get_l2_book(asset)
            levels = book.get("levels", [[], []])
            ask_px = self._client.round_price(float(levels[1][0]["px"]) * 1.003 if levels[1] else mark * 1.005)
            await self._client.place_taker(asset, is_buy=True, size=units, price=ask_px, reduce_only=True)
            perp_close_was_taker = True

        # ── Spot sell (real spot pair) ──────────────────────────────────────
        spot_close_was_taker = False
        if spot_pair:
            spot_book = await self._client.get_l2_book(spot_pair)
            spot_levels = spot_book.get("levels", [[], []])
            spot_mid = (float(spot_levels[0][0]["px"]) + float(spot_levels[1][0]["px"])) / 2 \
                       if (spot_levels[0] and spot_levels[1]) else pos.spot_entry_price

            spot_close_oid = await self._client.maker_chase_entry(
                spot_pair, is_buy=False, size=units, side_label="SPOT_SELL", is_spot=True
            )
            if spot_close_oid is None:
                log_warn(f"CarryStrategy: {asset} spot close timed out — using taker fallback")
                bid_px = self._client.round_price(
                    float(spot_levels[0][0]["px"]) * 0.997 if spot_levels[0] else spot_mid * 0.995
                )
                await self._client.place_taker(spot_pair, is_buy=False, size=units, price=bid_px, is_spot=True)
                spot_close_was_taker = True

        # ── Track exit fees ─────────────────────────────────────────────────
        perp_close_value = units * mark
        spot_close_value = pos.spot_size_usd  # approximation using entry notional
        pos.fee_paid_usd += perp_close_value * (FEE_PERP_TAKER if perp_close_was_taker else FEE_PERP_MAKER)
        if spot_pair:
            pos.fee_paid_usd += spot_close_value * (FEE_SPOT_TAKER if spot_close_was_taker else FEE_SPOT_MAKER)

        await self._finalize_close(asset, pos, reason)

    async def _finalize_close(self, asset: str, pos: CarryPosition, reason: str) -> None:
        pnl = pos.funding_collected_usd - pos.fee_paid_usd
        pos.mark_exited(pnl)
        pos.cooldown_until = int(time.time()) + EXIT_COOLDOWN_SECONDS

        self._stats.successful_cycles += 1 if pnl > 0 else 0
        self._stats.total_funding_usd += pos.funding_collected_usd
        self._stats.total_fees_usd    += pos.fee_paid_usd
        self._stats.total_pnl_usd     += pnl
        self._stats.update_drawdown()

        log(
            f"CarryStrategy: {asset} CLOSED | "
            f"funding=${pos.funding_collected_usd:.4f} fees=${pos.fee_paid_usd:.4f} "
            f"net=${pnl:+.4f} hold={pos.hold_hours:.1f}h reason={reason}"
        )
        await self._save_cycle_close(pos, reason)
        del self.positions[asset]

    # ── Funding payment accounting ─────────────────────────────────────────────

    async def record_funding_payment(
        self, asset: str, amount_usd: float, rate: float, paid_at_ms: int = 0,
    ) -> None:
        """Called by main loop when HL confirms a funding payment was received.

        BUG-017 dedup: HL replays userFundings history on every WS reconnect.
        We dedup by (asset, paid_at_ms) — the HL event timestamp is canonical.
        Falls back to current time only when paid_at_ms missing (legacy path).
        """
        # 1. In-memory dedup (fast path — covers WS replays within one session)
        if paid_at_ms:
            key = (asset, paid_at_ms)
            if key in self._seen_fundings:
                return
            self._seen_fundings.add(key)

        pos = self.positions.get(asset)
        if not (pos and pos.state == CarryState.HOLD):
            return

        # Temporal guard: HL WS snapshot can include funding from BEFORE we entered.
        # Only count events whose timestamp is past our entry — anything earlier
        # belongs to a prior cycle (or another wallet user) and must not inflate PnL.
        if paid_at_ms and pos.entered_at and paid_at_ms < pos.entered_at * 1000:
            return

        # 2. DB-level dedup (covers cross-session replays after restart).
        #    Backed by UNIQUE(asset, paid_at) index — INSERT OR IGNORE is atomic.
        inserted = await self._save_funding_payment(pos, amount_usd, rate, paid_at_ms)
        if not inserted:
            return  # already in DB → don't double-count in stats / pos accumulator

        pos.funding_collected_usd += amount_usd
        self._stats.total_funding_usd += amount_usd
        log(f"CarryStrategy: {asset} funding +${amount_usd:.4f} (rate={rate*100:.4f}%/h)")

    # ── DB persistence ────────────────────────────────────────────────────────

    async def _save_cycle_open(self, pos: CarryPosition) -> None:
        try:
            async with get_db() as conn:
                await conn.execute(
                    """INSERT INTO funding_cycles
                       (id, asset, state, entered_at, spot_size_usd, perp_size_usd,
                        perp_leverage, spot_entry, perp_entry, entry_funding_rate)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (
                        pos.cycle_id, pos.asset, "OPEN", pos.entered_at,
                        pos.spot_size_usd, pos.perp_size_usd, pos.perp_leverage,
                        pos.spot_entry_price, pos.perp_entry_price, pos.entry_funding_rate,
                    ),
                )
                await conn.commit()
        except Exception as e:
            crash_log("strategy._save_cycle_open", e)

    async def _save_cycle_close(self, pos: CarryPosition, reason: str) -> None:
        try:
            async with get_db() as conn:
                await conn.execute(
                    """UPDATE funding_cycles SET
                       state='CLOSED', exited_at=?, hold_hours=?,
                       funding_collected=?, fee_paid=?, net_pnl=?, exit_reason=?
                       WHERE id=?""",
                    (
                        pos.exited_at, pos.hold_hours,
                        pos.funding_collected_usd, pos.fee_paid_usd, pos.total_pnl_usd,
                        reason, pos.cycle_id,
                    ),
                )
                await conn.commit()
        except Exception as e:
            crash_log("strategy._save_cycle_close", e)

    async def _save_funding_payment(
        self, pos: CarryPosition, amount: float, rate: float, paid_at_ms: int = 0,
    ) -> bool:
        """Insert funding event with INSERT OR IGNORE against UNIQUE(asset, paid_at).
        Returns True if a new row was inserted, False if it was a duplicate.
        """
        paid_at = paid_at_ms if paid_at_ms else int(time.time() * 1000)
        try:
            async with get_db() as conn:
                cur = await conn.execute(
                    "INSERT OR IGNORE INTO funding_payments "
                    "(cycle_id, asset, paid_at, amount_usd, funding_rate_1h) VALUES (?,?,?,?,?)",
                    (pos.cycle_id, pos.asset, paid_at, amount, rate),
                )
                await conn.commit()
                return (cur.rowcount or 0) > 0
        except Exception as e:
            crash_log("strategy._save_funding_payment", e)
            return False
