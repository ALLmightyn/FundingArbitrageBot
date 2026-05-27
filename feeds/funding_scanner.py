"""feeds/funding_scanner.py — Polls Hyperliquid predicted funding rates.

Runs every SCAN_INTERVAL_SECONDS and maintains a ranked list of the
top N assets sorted by predicted_rate_1h descending.
"""
from __future__ import annotations

import asyncio
import time
from typing import Dict, List, Optional

from core.constants import (
    SCAN_TOP_N_ASSETS,
    SCAN_INTERVAL_SECONDS,
    FUNDING_ENTRY_THRESHOLD,
    FEE_ROUND_TRIP_ALL_MAKER,
)
from core.logger import log, log_warn, crash_log
from core.models import AssetState, FundingSnapshot
from venues.hyperliquid import HLClient


class FundingScanner:
    """
    Periodically polls HL for:
      1. Current funding rates per asset (meta_and_asset_ctxs)
      2. Predicted next-hour funding rates (predicted_fundings)

    Maintains `self.ranked` — a sorted list of AssetState ordered by
    predicted_rate_1h descending (best carry opportunity first).
    """

    def __init__(self, client: HLClient):
        self._client = client
        # asset → AssetState
        self.states: Dict[str, AssetState] = {}
        self.ranked: List[AssetState] = []
        self._last_scan: int = 0

    async def scan_once(self) -> List[AssetState]:
        """
        Fetch and process one round of funding data.
        Updates self.states and self.ranked. Returns the ranked list.
        """
        now = int(time.time())
        try:
            # Fetch current and predicted funding in parallel
            funding_data, predicted_data = await asyncio.gather(
                self._client.get_funding_rates(),
                self._client.get_predicted_fundings(),
                return_exceptions=True,
            )

            if isinstance(funding_data, Exception):
                log_warn(f"FundingScanner: funding_data error: {funding_data}")
                return self.ranked

            if isinstance(predicted_data, Exception):
                log_warn(f"FundingScanner: predicted_data error: {predicted_data}")
                predicted_data = []

            # Build predicted lookup: asset → predicted_rate_1h (HL perp only)
            # API format: [[asset_name, [[venue, {fundingRate, ...}], ...]], ...]
            predicted_map: Dict[str, float] = {}
            for entry in predicted_data:
                if not isinstance(entry, (list, tuple)) or len(entry) < 2:
                    continue
                coin = entry[0]
                venues = entry[1]
                for venue_entry in venues:
                    if not isinstance(venue_entry, (list, tuple)) or len(venue_entry) < 2:
                        continue
                    venue_name, venue_data = venue_entry[0], venue_entry[1]
                    if venue_name == "HlPerp" and isinstance(venue_data, dict):
                        try:
                            predicted_map[coin] = float(venue_data.get("fundingRate", 0))
                        except (TypeError, ValueError):
                            pass
                        break

            # Update states
            for item in funding_data[:SCAN_TOP_N_ASSETS]:
                asset     = item["asset"]
                current_r = item["funding"]          # hourly rate
                predicted = predicted_map.get(asset, current_r)  # fallback to current
                mark_px   = item["markPx"]
                oracle_px = item["oraclePx"]
                basis_pct = ((mark_px - oracle_px) / oracle_px) if oracle_px > 0 else 0.0

                snap = FundingSnapshot(
                    asset=asset,
                    funding_rate_1h=current_r,
                    predicted_rate_1h=predicted,
                    funding_8h_avg=current_r,   # refined in hold phase via payment history
                    mark_price=mark_px,
                    spot_price=oracle_px,
                    basis_pct=basis_pct,
                    sampled_at=now,
                )

                if asset not in self.states:
                    self.states[asset] = AssetState(asset=asset)
                state = self.states[asset]
                state.funding_snapshot = snap
                state.last_updated = now

            # Re-rank: descending by predicted_rate_1h, only assets above entry threshold.
            # Fee amortization is done at cycle close — scanner just gates on min EV.
            min_rate = FUNDING_ENTRY_THRESHOLD
            candidates = [
                s for s in self.states.values()
                if s.funding_snapshot is not None
                and s.funding_snapshot.predicted_rate_1h >= min_rate
            ]
            self.ranked = sorted(
                candidates,
                key=lambda s: s.funding_snapshot.predicted_rate_1h,  # type: ignore
                reverse=True,
            )

            self._last_scan = now
            if self.ranked:
                top = self.ranked[0].funding_snapshot
                log(
                    f"FundingScanner: {len(self.ranked)} candidates | "
                    f"top={top.asset} predicted={top.predicted_rate_1h*100:.4f}%/h "
                    f"({top.predicted_annualized_pct:.1f}% APR)"
                )
            else:
                log("FundingScanner: no assets above entry threshold this cycle")

        except Exception as e:
            crash_log("FundingScanner.scan_once", e)

        return self.ranked

    async def run_forever(self) -> None:
        """Background loop — call as asyncio.create_task(scanner.run_forever())."""
        log(f"FundingScanner: starting, interval={SCAN_INTERVAL_SECONDS}s")
        while True:
            await self.scan_once()
            await asyncio.sleep(SCAN_INTERVAL_SECONDS)

    def get_snapshot(self, asset: str) -> Optional[FundingSnapshot]:
        s = self.states.get(asset)
        return s.funding_snapshot if s else None

    def best_candidates(self, n: int = 3, exclude: set | None = None) -> List[AssetState]:
        """Return top-n candidates not in the exclude set."""
        excl = exclude or set()
        return [s for s in self.ranked if s.asset not in excl][:n]
