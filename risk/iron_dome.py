"""risk/iron_dome.py — Legging cover, funding flip watchdog, kill-switch, auto-deleverage.

The IronDome is the central risk controller. All protective actions route through here
so they are logged, counted, and gated by the kill-switch state.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Dict, List, Optional, TYPE_CHECKING

from core.constants import (
    FUNDING_EXIT_FLIP,
    FUNDING_EXIT_SOFT,
    LEGGING_EVENT_LIMIT,
    LEGGING_WINDOW_SECONDS,
    KILL_SWITCH_COOLDOWN_SECONDS,
    MAKER_CHASE_TIMEOUT_S,
    MAKER_EXIT_TIMEOUT_S,
    FEE_PERP_TAKER,
    FEE_SPOT_TAKER,
)
from core.logger import log, log_warn, log_err, crash_log
from core.models import CarryPosition, CarryState, SessionStats
from database.db import get_db

if TYPE_CHECKING:
    from venues.hyperliquid import HLClient
    from feeds.funding_scanner import FundingScanner
    from notifier.telegram import TelegramNotifier


class IronDome:
    """
    Central risk controller. Handles:
      1. Legging cover protocol (perp-first, spot taker fallback)
      2. Funding flip watchdog (polls predicted fundings)
      3. Kill-switch (after N legging events in window → 4h cooldown)
      4. Emergency flat (taker close both legs instantly)
      5. Partial deleverage (reduce position by fraction)
    """

    def __init__(
        self,
        client: "HLClient",
        scanner: "FundingScanner",
        stats: SessionStats,
        notifier: Optional["TelegramNotifier"] = None,
    ):
        self._client   = client
        self._scanner  = scanner
        self._stats    = stats
        self._notifier = notifier

        # Kill-switch state
        self.killed: bool = False
        self.kill_resume_at: int = 0

        # Legging event tracking: list of timestamps
        self._legging_timestamps: List[int] = []

    # ── Kill-switch ───────────────────────────────────────────────────────────

    def is_active(self) -> bool:
        """Returns True if IronDome allows new orders (kill-switch not active)."""
        if not self.killed:
            return True
        if int(time.time()) >= self.kill_resume_at:
            self.killed = False
            log("IronDome: kill-switch LIFTED, resuming operations")
            return True
        return False

    async def _maybe_trigger_kill_switch(self, asset: str) -> None:
        """Check if legging event count exceeds limit; arm kill-switch if so."""
        now = int(time.time())
        self._legging_timestamps = [t for t in self._legging_timestamps if now - t < LEGGING_WINDOW_SECONDS]
        self._legging_timestamps.append(now)

        if len(self._legging_timestamps) >= LEGGING_EVENT_LIMIT:
            self.killed = True
            self.kill_resume_at = now + KILL_SWITCH_COOLDOWN_SECONDS
            self._stats.kill_switch_activations += 1
            log_err(
                f"IronDome: KILL-SWITCH ARMED — {len(self._legging_timestamps)} legging events "
                f"in {LEGGING_WINDOW_SECONDS}s. Resume at {self.kill_resume_at}"
            )
            await self._log_kill_switch(asset)
            if self._notifier:
                await self._notifier.send_alert(
                    f"🛑 KILL-SWITCH ARMED\n"
                    f"{len(self._legging_timestamps)} legging events in {LEGGING_WINDOW_SECONDS//60}min\n"
                    f"Cooldown {KILL_SWITCH_COOLDOWN_SECONDS//3600}h"
                )

    # ── Legging cover ─────────────────────────────────────────────────────────

    async def cover_naked_leg(
        self,
        asset: str,
        carry_pos: CarryPosition,
        filled_leg: str,   # 'perp' or 'spot'
    ) -> bool:
        """
        Called when one leg filled but the other did not.
        Attempts taker cover to restore delta-neutral.
        Returns True if cover succeeded.
        """
        self._stats.legging_events += 1
        carry_pos.legging_events += 1
        log_err(f"IronDome.cover_naked_leg: {asset} filled_leg={filled_leg}")

        cover_ok = False
        cover_cost = 0.0

        if filled_leg == "perp":
            # We're short perp but no spot long → cover by closing the perp (reduce-only taker)
            # NOTE: True spot buy on HL requires separate spot market routing (TODO: mainnet).
            # For now: close the naked perp short via reduce-only taker to eliminate exposure.
            try:
                book = await self._client.get_l2_book(asset)
                levels = book.get("levels", [[], []])
                # To close a short, buy at ask + slippage
                raw_px = float(levels[1][0]["px"]) * 1.003 if levels[1] else carry_pos.perp_entry_price * 1.005
                ask_px = self._client.round_price(raw_px)
                perp_size = carry_pos.perp_size_usd / max(carry_pos.perp_entry_price, 0.001)
                result = await self._client.place_taker(asset, is_buy=True, size=perp_size, price=ask_px, reduce_only=True)
                status = result.get("response", {}).get("data", {}).get("statuses", [{}])[0]
                if "filled" in status:
                    fill_px = float(status["filled"].get("avgPx", ask_px))
                    cover_cost = perp_size * fill_px * FEE_PERP_TAKER
                    carry_pos.fee_paid_usd += cover_cost
                    cover_ok = True
                    log(f"IronDome: perp reduce-only cover OK @ {fill_px:.2f}, cost=${cover_cost:.4f}")
                else:
                    log_err(f"IronDome: perp cover FAILED: {status}")
            except Exception as e:
                crash_log("iron_dome.cover_naked_leg.perp_close", e)

        elif filled_leg == "spot":
            # We're long spot but no perp short → cover by shorting perp TAKER
            book = await self._client.get_l2_book(asset)
            levels = book.get("levels", [[], []])
            raw_bid = float(levels[0][0]["px"]) * 0.998 if levels[0] else carry_pos.spot_entry_price * 0.995
            bid_px = self._client.round_price(raw_bid)
            size = carry_pos.perp_size_usd / max(bid_px, 0.001)
            try:
                result = await self._client.place_taker(asset, is_buy=False, size=size, price=bid_px)
                status = result.get("response", {}).get("data", {}).get("statuses", [{}])[0]
                if "filled" in status:
                    fill_px = float(status["filled"].get("avgPx", bid_px))
                    cover_cost = size * fill_px * FEE_PERP_TAKER
                    carry_pos.perp_entry_price = fill_px
                    carry_pos.fee_paid_usd += cover_cost
                    cover_ok = True
                    log(f"IronDome: perp taker cover OK @ {fill_px:.4f}, cost=${cover_cost:.4f}")
                else:
                    log_err(f"IronDome: perp taker cover FAILED: {status}")
            except Exception as e:
                crash_log("iron_dome.cover_naked_leg.perp", e)

        await self._log_legging_event(asset, filled_leg, cover_ok, cover_cost, carry_pos.cycle_id)
        await self._maybe_trigger_kill_switch(asset)
        return cover_ok

    # ── Funding watchdog ──────────────────────────────────────────────────────

    async def check_funding_exit(self, asset: str, carry_pos: CarryPosition) -> Optional[str]:
        """
        Check if funding conditions warrant exit.
        Returns exit reason string if we should exit, None otherwise.
        """
        snap = self._scanner.get_snapshot(asset)
        if snap is None:
            return None

        predicted = snap.predicted_rate_1h

        if predicted <= FUNDING_EXIT_FLIP:
            log_err(f"IronDome.funding_watchdog: {asset} predicted={predicted:.6f} NEGATIVE → IMMEDIATE EXIT")
            return "funding_flip"

        if predicted < FUNDING_EXIT_SOFT:
            log_warn(f"IronDome.funding_watchdog: {asset} predicted={predicted:.6f} below soft threshold → EXIT")
            return "funding_soft_exit"

        return None

    # ── Emergency flat (taker, both legs) ────────────────────────────────────

    async def emergency_flat(
        self,
        asset: str,
        carry_pos: CarryPosition,
        reason: str = "emergency",
    ) -> None:
        """Taker-close both legs as fast as possible. Uses spot_pair for spot leg."""
        log_err(f"IronDome.emergency_flat: {asset} reason={reason}")
        carry_pos.state = CarryState.EXIT_MAKER

        units = carry_pos.units or (carry_pos.perp_size_usd / max(carry_pos.perp_entry_price, 0.001))
        spot_pair = carry_pos.spot_pair

        # Close perp short first (more liquid)
        try:
            book = await self._client.get_l2_book(asset)
            levels = book.get("levels", [[], []])
            ask_px = self._client.round_price(float(levels[1][0]["px"]) * 1.005 if levels[1] else carry_pos.perp_entry_price * 1.01)
            await self._client.place_taker(asset, is_buy=True, size=units, price=ask_px, reduce_only=True)
            log(f"IronDome: emergency perp close sent for {asset}")
        except Exception as e:
            crash_log(f"emergency_flat.perp.{asset}", e)

        # Sell spot — only if we have a spot pair
        if spot_pair:
            try:
                spot_book = await self._client.get_l2_book(spot_pair)
                spot_levels = spot_book.get("levels", [[], []])
                bid_px = self._client.round_price(
                    float(spot_levels[0][0]["px"]) * 0.995 if spot_levels[0] else carry_pos.spot_entry_price * 0.99
                )
                await self._client.place_taker(spot_pair, is_buy=False, size=units, price=bid_px, is_spot=True)
                log(f"IronDome: emergency spot sell sent for {asset} ({spot_pair})")
            except Exception as e:
                crash_log(f"emergency_flat.spot.{asset}", e)

        carry_pos.state = CarryState.COOLDOWN
        self._stats.deleverage_events += 1

        if self._notifier:
            await self._notifier.send_alert(
                f"🚨 EMERGENCY FLAT\n"
                f"Asset: {asset}\nReason: {reason}\n"
                f"Both legs taker-closed"
            )

    # ── Partial deleverage ────────────────────────────────────────────────────

    async def deleverage_partial(
        self,
        asset: str,
        carry_pos: CarryPosition,
        fraction: float = 0.33,
    ) -> None:
        """Reduce position size by `fraction` using taker orders."""
        log_warn(f"IronDome.deleverage_partial: {asset} reducing by {fraction:.0%}")

        perp_reduce = carry_pos.perp_size_usd * fraction
        spot_reduce = carry_pos.spot_size_usd * fraction

        try:
            book = await self._client.get_l2_book(asset)
            levels = book.get("levels", [[], []])

            # Close fraction of perp short
            ask_px = self._client.round_price(float(levels[1][0]["px"]) * 1.003 if levels[1] else carry_pos.perp_entry_price * 1.005)
            perp_size = perp_reduce / max(carry_pos.perp_entry_price, 0.001)
            await self._client.place_taker(asset, is_buy=True, size=perp_size, price=ask_px, reduce_only=True)

            # Sell fraction of spot long via real spot pair
            spot_pair = carry_pos.spot_pair
            if spot_pair:
                spot_book = await self._client.get_l2_book(spot_pair)
                spot_levels = spot_book.get("levels", [[], []])
                bid_px = self._client.round_price(
                    float(spot_levels[0][0]["px"]) * 0.997 if spot_levels[0] else carry_pos.spot_entry_price * 0.995
                )
                spot_size = spot_reduce / max(carry_pos.spot_entry_price, 0.001)
                await self._client.place_taker(spot_pair, is_buy=False, size=spot_size, price=bid_px, is_spot=True)

            carry_pos.perp_size_usd -= perp_reduce
            carry_pos.spot_size_usd -= spot_reduce
            self._stats.deleverage_events += 1
            log(f"IronDome: deleverage complete — perp={carry_pos.perp_size_usd:.2f} spot={carry_pos.spot_size_usd:.2f}")

        except Exception as e:
            crash_log(f"deleverage_partial.{asset}", e)

        await self._log_deleverage(asset, carry_pos.margin_ratio, carry_pos.cycle_id, fraction)

    # ── DB logging ────────────────────────────────────────────────────────────

    async def _log_legging_event(
        self, asset: str, filled_leg: str, cover_ok: bool, cost: float, cycle_id: Optional[str]
    ) -> None:
        try:
            async with get_db() as conn:
                action = "taker_cover" if cover_ok else "cover_failed"
                await conn.execute(
                    "INSERT INTO legging_events (asset, occurred_at, leg_filled, cover_action, cover_cost_usd, cycle_id) VALUES (?,?,?,?,?,?)",
                    (asset, int(time.time()), filled_leg, action, cost, cycle_id),
                )
                await conn.commit()
        except Exception as e:
            crash_log("iron_dome._log_legging_event", e)

    async def _log_deleverage(
        self, asset: str, margin_ratio: float, cycle_id: Optional[str], fraction: float
    ) -> None:
        try:
            async with get_db() as conn:
                await conn.execute(
                    "INSERT INTO deleverage_events (asset, occurred_at, trigger, margin_ratio, size_before_usd, size_after_usd, cycle_id) VALUES (?,?,?,?,?,?,?)",
                    (asset, int(time.time()), "margin_warn", margin_ratio, 0, 0, cycle_id),
                )
                await conn.commit()
        except Exception as e:
            crash_log("iron_dome._log_deleverage", e)

    async def _log_kill_switch(self, asset: str) -> None:
        try:
            async with get_db() as conn:
                await conn.execute(
                    "INSERT INTO kill_switch_log (triggered_at, trigger_reason, assets_affected, resume_at) VALUES (?,?,?,?)",
                    (int(time.time()), "legging_limit", json.dumps([asset]), self.kill_resume_at),
                )
                await conn.commit()
        except Exception as e:
            crash_log("iron_dome._log_kill_switch", e)
