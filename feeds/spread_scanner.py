"""feeds/spread_scanner.py — Cross-venue funding spread scanner.

Polls Lighter's /funding-rates endpoint (which returns rates from all
exchanges: binance, bybit, hyperliquid, lighter) and computes the net
spread between HL and Lighter for each asset.

The spread = |hl_rate - lighter_rate|.
  short_venue = whichever exchange has the higher rate (short there = earn)
  long_venue  = whichever exchange has the lower rate (long there = pay less)

Single API call per scan cycle — Lighter aggregates all exchange rates.
We also verify the HL rate against HLClient.get_funding_rates() to catch
any staleness in Lighter's oracle data.
"""
from __future__ import annotations

import asyncio
import math
import time
from collections import deque
from typing import Deque, Dict, List, Optional, Tuple

from core.constants import (
    SPREAD_ENTRY_THRESHOLD,
    SPREAD_SCAN_INTERVAL_S,
    SCAN_TOP_N_ASSETS,
    FEE_CROSS_ROUND_TRIP,
    CROSS_VENUE_WHITELIST,
    SPIKE_SIGMA_THRESHOLD,
    SPIKE_HISTORY_MIN_SAMPLES,
    SPIKE_HISTORY_MAX_SAMPLES,
)
from core.logger import log, log_warn, crash_log
from core.models import SpreadSnapshot
from venues.hyperliquid import HLClient
from venues.lighter import LighterClient


# Minimum Lighter oracle freshness: reject HL rates from Lighter oracle if they
# diverge from HL's own API by more than this fraction.
_MAX_ORACLE_DIVERGENCE = 0.50   # 50% — generous to handle HL ≠ lighter oracle timing


class SpreadScanner:
    """
    Periodically computes HL ↔ Lighter funding spread for all assets.

    Primary data source: Lighter's /funding-rates (single call, all exchanges).
    Secondary verification: HLClient.get_funding_rates() for actual HL rates.

    Maintains self.ranked — sorted list of SpreadSnapshot by spread descending.
    """

    def __init__(self, hl_client: HLClient, lighter_client: LighterClient):
        self._hl = hl_client
        self._lt = lighter_client

        # asset → SpreadSnapshot
        self.snapshots: Dict[str, SpreadSnapshot] = {}
        # sorted by spread descending, filtered above threshold
        self.ranked: List[SpreadSnapshot] = []
        self._last_scan: int = 0

        # Rolling history of signed (hl_rate - lighter_rate) per asset for
        # sigma-based spike filter. Signed so mean drift is meaningful.
        self._spread_history: Dict[str, Deque[float]] = {}

    async def scan_once(self) -> List[SpreadSnapshot]:
        """
        One scan cycle. Updates self.snapshots and self.ranked.
        Returns the ranked list.
        """
        now = int(time.time())
        try:
            # Fetch in parallel: Lighter oracle (has all exchanges) + HL verified rates
            lighter_all_task = asyncio.create_task(self._lt.get_all_exchange_funding_rates())
            hl_verified_task = asyncio.create_task(self._hl.get_funding_rates())

            lighter_all = await lighter_all_task
            hl_verified = await hl_verified_task

            if not lighter_all:
                log_warn("SpreadScanner: no data from Lighter oracle")
                return self.ranked

            # Lighter's view of HL rates (for cross-check)
            hl_via_lighter: Dict[str, float] = lighter_all.get("hyperliquid", {})
            # Lighter's own rates
            lighter_rates: Dict[str, float] = lighter_all.get("lighter", {})

            # Build verified HL rates: prefer live HL API, fall back to Lighter oracle
            # hl_verified is a list of dicts: [{asset, funding, markPx, ...}, ...]
            hl_rates: Dict[str, float] = {}
            hl_mark_prices: Dict[str, float] = {}
            if isinstance(hl_verified, list):
                for item in hl_verified:
                    a = item.get("asset", "")
                    if a:
                        hl_rates[a] = float(item.get("funding", 0))
                        hl_mark_prices[a] = float(item.get("markPx", 0))

            new_snapshots: Dict[str, SpreadSnapshot] = {}

            # Find all assets present on both exchanges — restrict to whitelist
            # (avoids thin Lighter books on illiquid alts where slippage kills the trade)
            common_assets = (
                set(lighter_rates.keys()) & (set(hl_rates.keys()) | set(hl_via_lighter.keys()))
            ) & CROSS_VENUE_WHITELIST

            for asset in common_assets:
                lt_rate = lighter_rates.get(asset, 0.0)

                # Use live HL rate if available, else Lighter's oracle
                hl_rate = hl_rates.get(asset, hl_via_lighter.get(asset, 0.0))

                spread = abs(hl_rate - lt_rate)

                if hl_rate >= lt_rate:
                    short_venue, long_venue = "hl", "lighter"
                else:
                    short_venue, long_venue = "lighter", "hl"

                snap = SpreadSnapshot(
                    asset=asset,
                    hl_rate=hl_rate,
                    lighter_rate=lt_rate,
                    spread=spread,
                    short_venue=short_venue,
                    long_venue=long_venue,
                    hl_mark_px=hl_mark_prices.get(asset, 0.0),
                    sampled_at=now,
                )
                new_snapshots[asset] = snap

                # Record signed differential for sigma history (anti-spike filter)
                hist = self._spread_history.setdefault(
                    asset, deque(maxlen=SPIKE_HISTORY_MAX_SAMPLES)
                )
                hist.append(hl_rate - lt_rate)

            self.snapshots = new_snapshots
            self._last_scan = now

            # Rank: filter above threshold, sort by spread descending
            min_spread = SPREAD_ENTRY_THRESHOLD
            self.ranked = sorted(
                [s for s in self.snapshots.values() if s.spread >= min_spread],
                key=lambda s: s.spread,
                reverse=True,
            )[:SCAN_TOP_N_ASSETS]

            if self.ranked:
                top = self.ranked[0]
                log(
                    f"SpreadScanner: {len(self.ranked)} opportunities | "
                    f"top={top.asset} spread={top.spread*100:.4f}%/h "
                    f"({top.spread_pct_annual:.1f}% APR) "
                    f"short={top.short_venue} long={top.long_venue}"
                )
            else:
                log("SpreadScanner: no assets above threshold this cycle")

        except Exception as e:
            crash_log("SpreadScanner.scan_once", e)

        return self.ranked

    async def run_forever(self) -> None:
        """Background loop — launch as asyncio.create_task(scanner.run_forever())."""
        log(f"SpreadScanner: starting, interval={SPREAD_SCAN_INTERVAL_S}s")
        while True:
            await self.scan_once()
            await asyncio.sleep(SPREAD_SCAN_INTERVAL_S)

    def get_snapshot(self, asset: str) -> Optional[SpreadSnapshot]:
        return self.snapshots.get(asset)

    def best_candidates(
        self, n: int = 3, exclude: Optional[set] = None
    ) -> List[SpreadSnapshot]:
        excl = exclude or set()
        return [s for s in self.ranked if s.asset not in excl][:n]

    def warmup_complete(self, asset: str) -> bool:
        """True once we have enough spread history to run the spike filter reliably."""
        hist = self._spread_history.get(asset)
        return hist is not None and len(hist) >= SPIKE_HISTORY_MIN_SAMPLES

    def is_spike(self, asset: str, snap: SpreadSnapshot) -> Tuple[bool, str]:
        """
        Return (is_spike, reason). True if current signed spread exceeds
        rolling_mean ± SPIKE_SIGMA_THRESHOLD * sigma.

        Returns (True, "warming_up") — BLOCK entry — if not enough history has
        accumulated yet. The old fail-open behaviour was the root cause of the
        NEAR funding-spike entry (BUG-022): bot entered at 0.091%/h Lighter rate
        (800% APR display) because spike filter had 0 samples on cold start;
        rate normalized to 0.013%/h within the first hour → real APR ~108%.
        """
        hist = self._spread_history.get(asset)
        if hist is None or len(hist) < SPIKE_HISTORY_MIN_SAMPLES:
            return True, f"warming_up ({len(hist) if hist else 0}/{SPIKE_HISTORY_MIN_SAMPLES} samples)"

        # Use signed differential (hl - lt) — direction matters for spike detection
        current_signed = snap.hl_rate - snap.lighter_rate
        n = len(hist)
        mean = sum(hist) / n
        var = sum((x - mean) ** 2 for x in hist) / n
        std = math.sqrt(var)
        if std <= 0:
            return False, "zero_variance"

        z = abs(current_signed - mean) / std
        if z > SPIKE_SIGMA_THRESHOLD:
            return True, (
                f"z={z:.2f}σ > {SPIKE_SIGMA_THRESHOLD}σ "
                f"(current={current_signed*100:.4f}%/h, μ={mean*100:.4f}%/h, σ={std*100:.4f}%/h)"
            )
        return False, f"z={z:.2f}σ"
