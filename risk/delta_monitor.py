"""risk/delta_monitor.py — Margin ratio polling and auto-deleverage.

Polls clearinghouseState every MARGIN_POLL_INTERVAL_SECONDS.
Triggers IronDome deleverage if margin_ratio hits danger thresholds.
"""
from __future__ import annotations

import asyncio
import time
from typing import Dict, Optional, TYPE_CHECKING

from core.constants import (
    MARGIN_POLL_INTERVAL_SECONDS,
    MARGIN_RATIO_WARN,
    MARGIN_RATIO_EMERGENCY,
    DELTA_DRIFT_PCT,
)
from core.logger import log, log_warn, log_err, crash_log
from core.models import CarryPosition, CarryState

if TYPE_CHECKING:
    from venues.hyperliquid import HLClient
    from risk.iron_dome import IronDome


class DeltaMonitor:
    """
    Watches margin ratios and delta drift for all active positions.

    margin_ratio = maintenance_margin / account_equity
    At MARGIN_RATIO_WARN  (0.50) → deleverage 33% of the position
    At MARGIN_RATIO_EMERGENCY (0.75) → emergency flat (taker close all)
    """

    def __init__(self, client: "HLClient", iron_dome: "IronDome"):
        self._client = client
        self._dome = iron_dome
        self._last_poll: int = 0

    async def poll_once(self, positions: Dict[str, CarryPosition]) -> None:
        """Check clearinghouse state and trigger deleverage if needed."""
        try:
            ch_state = await self._client.get_clearinghouse()
            # ch_state has: marginSummary, crossMarginSummary, assetPositions, ...
            margin_summary = ch_state.get("crossMarginSummary", {})
            account_value  = float(margin_summary.get("accountValue", 1))
            total_maint_mm = float(margin_summary.get("totalMaintainedMargin", 0))

            if account_value <= 0:
                return

            global_margin_ratio = total_maint_mm / account_value

            # Build per-asset position lookup from HL response
            hl_positions: Dict[str, Dict] = {}
            for ap in ch_state.get("assetPositions", []):
                pos = ap.get("position", {})
                coin = pos.get("coin", "")
                if coin:
                    hl_positions[coin] = pos

            for asset, carry_pos in list(positions.items()):
                if carry_pos.state not in (CarryState.HOLD, CarryState.EXIT_MAKER):
                    continue

                # Per-asset perp position
                hl_pos = hl_positions.get(asset)
                if hl_pos is None:
                    continue

                # margin_ratio for this isolated position
                pos_value   = abs(float(hl_pos.get("positionValue", 0)))
                maint_margin = abs(float(hl_pos.get("marginUsed", 0)))  # SDK field
                if pos_value <= 0:
                    continue

                margin_ratio = maint_margin / pos_value if pos_value > 0 else 0.0
                carry_pos.margin_ratio = margin_ratio

                # Delta drift: compare spot vs perp notional
                unrealized_pnl = float(hl_pos.get("unrealizedPnl", 0))
                perp_notional  = abs(float(hl_pos.get("positionValue", 0)))
                spot_notional  = carry_pos.spot_size_usd  # we track this locally
                if spot_notional > 0:
                    drift = abs(perp_notional - spot_notional) / spot_notional
                    carry_pos.delta_drift_pct = drift
                    if drift > DELTA_DRIFT_PCT:
                        log_warn(
                            f"DeltaMonitor: {asset} drift={drift:.2%} > {DELTA_DRIFT_PCT:.2%}"
                            f" — scheduling rebalance"
                        )

                # Deleverage decision
                if margin_ratio >= MARGIN_RATIO_EMERGENCY:
                    log_err(
                        f"DeltaMonitor EMERGENCY: {asset} margin_ratio={margin_ratio:.2%} "
                        f">= {MARGIN_RATIO_EMERGENCY:.2%} — EMERGENCY FLAT"
                    )
                    await self._dome.emergency_flat(asset, carry_pos, reason="margin_emergency")

                elif margin_ratio >= MARGIN_RATIO_WARN:
                    log_warn(
                        f"DeltaMonitor WARN: {asset} margin_ratio={margin_ratio:.2%} "
                        f">= {MARGIN_RATIO_WARN:.2%} — deleverage 33%"
                    )
                    await self._dome.deleverage_partial(asset, carry_pos, fraction=0.33)

        except Exception as e:
            crash_log("DeltaMonitor.poll_once", e)

    async def run_forever(self, positions: Dict[str, CarryPosition]) -> None:
        """Background loop — share the positions dict from the strategy."""
        log(f"DeltaMonitor: starting, poll interval={MARGIN_POLL_INTERVAL_SECONDS}s")
        while True:
            await asyncio.sleep(MARGIN_POLL_INTERVAL_SECONDS)
            await self.poll_once(positions)
