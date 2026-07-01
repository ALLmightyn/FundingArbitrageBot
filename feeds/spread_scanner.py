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
    SPREAD_TWAP_WINDOW_S,
    SPREAD_TWAP_MIN_SAMPLES,
    STABILITY_LOOKBACK_HOURS,
    STABILITY_MIN_HIT_RATIO,
    PERSISTENCE_FEE_MULT,
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

        # Rolling history of (timestamp, hl_rate, lt_rate) per asset.
        # Used for both the sigma spike filter and the TWAP entry gate.
        self._rate_history: Dict[str, Deque[Tuple[int, float, float]]] = {}

        # Historical stats per asset (refreshed hourly from HL fundingHistory).
        # Drives the primary ranking: we want assets that have HISTORICALLY paid
        # a sustained spread, not assets that are spiking right now.
        # Schema: {asset: {"avg_spread", "hit_ratio", "short_venue", "samples", "updated_at"}}
        self._hist_stats: Dict[str, Dict] = {}
        self._hist_last_refresh: int = 0

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

                # Record (ts, hl_rate, lt_rate) for spike filter + TWAP gate
                hist = self._rate_history.setdefault(
                    asset, deque(maxlen=SPIKE_HISTORY_MAX_SAMPLES)
                )
                hist.append((now, hl_rate, lt_rate))

            self.snapshots = new_snapshots
            self._last_scan = now

            # ── Ranking: PRIMARY signal is historical sustained spread, NOT instant. ──
            # Instant rate from /funding-rates is mostly spike noise. We rank by the
            # 24h realised spread (refresh_historical_stats), then require instant to
            # be in the same direction with at least entry threshold — a sanity check
            # that the carry is still alive right now.
            if not self._hist_stats:
                # Cold start: no historical stats yet → fall back to instant ranking,
                # but limit to top candidates so TWAP gate + stability gate can filter.
                candidates = [s for s in self.snapshots.values() if s.spread >= SPREAD_ENTRY_THRESHOLD]
                self.ranked = sorted(candidates, key=lambda s: s.spread, reverse=True)[:SCAN_TOP_N_ASSETS]
                if self.ranked:
                    top = self.ranked[0]
                    log(
                        f"SpreadScanner[cold]: {len(self.ranked)} instant candidates | "
                        f"top={top.asset} {top.spread*100:.4f}%/h "
                        f"(awaiting historical refresh)"
                    )
            else:
                # Hot path: rank by historical score, gate by instant alignment.
                ranked: List[Tuple[float, SpreadSnapshot]] = []
                skipped: List[str] = []
                for asset, stats in self._hist_stats.items():
                    if stats["avg_spread"] < SPREAD_ENTRY_THRESHOLD:
                        skipped.append(f"{asset}(avg<thr)")
                        continue
                    if stats["hit_ratio"] < STABILITY_MIN_HIT_RATIO:
                        skipped.append(
                            f"{asset}(hits={int(stats['hit_ratio']*stats['samples'])}"
                            f"/{stats['samples']}={stats['hit_ratio']*100:.0f}%<{STABILITY_MIN_HIT_RATIO*100:.0f}%)"
                        )
                        continue
                    # Persistence gate: longest consecutive carry run must out-earn the
                    # round-trip fee with margin, else break-even is never reached.
                    min_run_earn = FEE_CROSS_ROUND_TRIP * PERSISTENCE_FEE_MULT
                    if stats.get("run_earn", 0.0) < min_run_earn:
                        skipped.append(
                            f"{asset}(run={stats.get('max_run',0)}h "
                            f"earn={stats.get('run_earn',0.0)*100:.3f}%<{min_run_earn*100:.3f}%)"
                        )
                        continue
                    snap = self.snapshots.get(asset)
                    if snap is None:
                        skipped.append(f"{asset}(no_snap)")
                        continue
                    # Direction sanity: instant should agree with historical dominant side.
                    if snap.short_venue != stats["dominant_short"]:
                        skipped.append(f"{asset}(dir_flip)")
                        continue
                    # Instant magnitude should be at least 50% of the historical avg —
                    # the carry must still be alive (not in a temporary slump).
                    if snap.spread < stats["avg_spread"] * 0.5:
                        skipped.append(
                            f"{asset}(instant={snap.spread*100:.4f}%<50%avg)"
                        )
                        continue
                    ranked.append((stats["score"], snap))

                ranked.sort(key=lambda x: x[0], reverse=True)
                self.ranked = [snap for _, snap in ranked[:SCAN_TOP_N_ASSETS]]

                if self.ranked:
                    top = self.ranked[0]
                    ts = self._hist_stats[top.asset]
                    log(
                        f"SpreadScanner: {len(self.ranked)} sustained carries | "
                        f"top={top.asset} hist_avg={ts['avg_spread']*100:.4f}%/h "
                        f"hits={int(ts['hit_ratio']*ts['samples'])}/{ts['samples']} "
                        f"instant={top.spread*100:.4f}%/h short={top.short_venue}"
                    )
                else:
                    log("SpreadScanner: no historically sustained carries available")
                if skipped:
                    log(f"SpreadScanner: filtered out — {', '.join(skipped[:8])}")

        except Exception as e:
            crash_log("SpreadScanner.scan_once", e)

        return self.ranked

    async def run_forever(self) -> None:
        """Background loop — launch as asyncio.create_task(scanner.run_forever())."""
        log(f"SpreadScanner: starting, interval={SPREAD_SCAN_INTERVAL_S}s")
        # Hourly historical refresh — settlements happen on the hour, so we re-rank
        # after each settlement window. First refresh runs immediately on startup
        # to bootstrap ranking before the cold-start fallback kicks in.
        HIST_REFRESH_INTERVAL_S = 3600
        await self.refresh_historical_stats()
        last_hist_refresh = int(time.time())
        while True:
            await self.scan_once()
            now = int(time.time())
            if now - last_hist_refresh >= HIST_REFRESH_INTERVAL_S:
                await self.refresh_historical_stats()
                last_hist_refresh = now
            await asyncio.sleep(SPREAD_SCAN_INTERVAL_S)

    def get_snapshot(self, asset: str) -> Optional[SpreadSnapshot]:
        return self.snapshots.get(asset)

    def best_candidates(
        self, n: int = 3, exclude: Optional[set] = None
    ) -> List[SpreadSnapshot]:
        excl = exclude or set()
        return [s for s in self.ranked if s.asset not in excl][:n]

    def warmup_complete(self, asset: str) -> bool:
        """True once we have enough rate history to run the spike filter reliably."""
        hist = self._rate_history.get(asset)
        return hist is not None and len(hist) >= SPIKE_HISTORY_MIN_SAMPLES

    def is_spike(self, asset: str, snap: SpreadSnapshot) -> Tuple[bool, str]:
        """
        Return (is_spike, reason). True if current signed spread exceeds
        rolling_mean ± SPIKE_SIGMA_THRESHOLD * sigma.

        Returns (True, "warming_up") — BLOCK entry — if not enough history.
        Fail-closed on cold start: root cause of NEAR spike entry (BUG-022).
        """
        hist = self._rate_history.get(asset)
        if hist is None or len(hist) < SPIKE_HISTORY_MIN_SAMPLES:
            return True, f"warming_up ({len(hist) if hist else 0}/{SPIKE_HISTORY_MIN_SAMPLES} samples)"

        signed_vals = [h - l for (_, h, l) in hist]
        current_signed = snap.hl_rate - snap.lighter_rate
        n = len(signed_vals)
        mean = sum(signed_vals) / n
        var = sum((x - mean) ** 2 for x in signed_vals) / n
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

    async def refresh_historical_stats(self) -> None:
        """
        Pull 24h hourly settlement history from BOTH venues for every whitelisted
        asset, compute the realised hourly spread per asset, and update self._hist_stats.

        This is the PRIMARY ranking signal. The old logic ranked by instant rate
        (snap.spread), which mostly surfaces spikes that have already started to
        revert. By ranking on historical sustained spread we put real carries
        (10-50%/h consistent) ahead of phantom spikes (0.05%/h for 3 min, 0.001%/h
        the rest of the hour).

        Refresh is hourly: settlement events happen on the hour, so polling more
        often wastes API calls. We launch all requests in parallel — ~21 assets
        × 2 venues × 1 call each ≈ 42 calls in ~1-2s total.
        """
        try:
            assets = sorted(CROSS_VENUE_WHITELIST)
            # Launch HL + LT history requests in parallel
            hl_tasks  = {a: asyncio.create_task(self._hl.get_funding_history(a, hours=24)) for a in assets}
            lt_tasks  = {a: asyncio.create_task(self._lt.get_funding_history(a, hours=24)) for a in assets}

            hl_data = {a: await t for a, t in hl_tasks.items()}
            lt_data = {a: await t for a, t in lt_tasks.items()}

            new_stats: Dict[str, Dict] = {}
            for asset in assets:
                hl_hist = hl_data.get(asset, [])
                lt_hist = lt_data.get(asset, [])
                if not hl_hist or not lt_hist:
                    continue

                # Index LT entries by hour-bucket timestamp (settlement is on hourly boundary)
                lt_by_hour = {(int(e["timestamp"]) // 3600) * 3600: float(e["rate"]) for e in lt_hist}

                paired_spreads: List[Tuple[int, float, float]] = []  # (ts, hl_rate, lt_rate)
                for h in hl_hist:
                    ts_sec = int(h["time"]) // 1000
                    bucket = (ts_sec // 3600) * 3600
                    hl_r = float(h["fundingRate"])
                    if bucket in lt_by_hour:
                        paired_spreads.append((ts_sec, hl_r, lt_by_hour[bucket]))

                if len(paired_spreads) < 6:  # need ≥6 paired hours to trust stats
                    continue

                # Signed spread for each hour: hl_rate - lt_rate. Sign matters for
                # determining short_venue, but we score on the *absolute* magnitude
                # of the median spread, then derive direction from the sign.
                # Sort chronologically so consecutive-run analysis is valid.
                paired_spreads.sort(key=lambda t: t[0])
                signed = [hl - lt for (_, hl, lt) in paired_spreads]
                abs_vals = [abs(x) for x in signed]
                n = len(signed)

                # Mean signed → tells us net direction; if mean is positive, hl_rate
                # was on average higher → we'd short HL.
                mean_signed = sum(signed) / n
                mean_abs    = sum(abs_vals) / n

                # Hit ratio: fraction of hours where realised spread was above threshold
                # in the dominant direction. If dominant is hl_short (mean > 0):
                # we want positive signed values above threshold.
                if mean_signed >= 0:
                    dominant_short = "hl"
                    hits = sum(1 for s in signed if s >= SPREAD_ENTRY_THRESHOLD)
                else:
                    dominant_short = "lighter"
                    hits = sum(1 for s in signed if -s >= SPREAD_ENTRY_THRESHOLD)

                hit_ratio = hits / n

                # Persistence (persistence_gate.md): break-even needs CONSECUTIVE hours.
                # Walk the directional hourly series, find the longest run of hours above
                # threshold in the dominant direction, and sum the spread earned in it.
                # run_earn = best contiguous cumulative carry available — compare to fee.
                directional = signed if mean_signed >= 0 else [-s for s in signed]
                max_run = 0
                run_earn = 0.0
                cur_run = 0
                cur_earn = 0.0
                for s in directional:
                    if s >= SPREAD_ENTRY_THRESHOLD:
                        cur_run += 1
                        cur_earn += s
                        if cur_earn > run_earn:
                            run_earn = cur_earn
                            max_run = cur_run
                    else:
                        cur_run = 0
                        cur_earn = 0.0

                # Score: emphasise BOTH average magnitude AND consistency.
                # Two coins with the same 0.03%/h mean but one with 22/24 hits
                # and the other with 12/24 are very different bets.
                score = mean_abs * hit_ratio

                new_stats[asset] = {
                    "avg_spread":     mean_abs,
                    "mean_signed":    mean_signed,
                    "hit_ratio":      hit_ratio,
                    "samples":        n,
                    "dominant_short": dominant_short,
                    "score":          score,
                    "max_run":        max_run,
                    "run_earn":       run_earn,
                    "updated_at":     int(time.time()),
                }

            self._hist_stats = new_stats
            self._hist_last_refresh = int(time.time())

            # Log top-5 by historical score
            top = sorted(new_stats.items(), key=lambda kv: kv[1]["score"], reverse=True)[:5]
            if top:
                pretty = " | ".join(
                    f"{a}={s['avg_spread']*100:.4f}%/h hits={int(s['hit_ratio']*s['samples'])}/{s['samples']}"
                    for a, s in top
                )
                log(f"HistoricalStats: top-5 sustained carries — {pretty}")
            else:
                log_warn("HistoricalStats: no assets met paired-history minimum")
        except Exception as e:
            crash_log("SpreadScanner.refresh_historical_stats", e)

    async def is_stable_carry(
        self,
        asset: str,
        short_venue: str,
        lookback_hours: int = STABILITY_LOOKBACK_HOURS,
        min_hit_ratio: float = STABILITY_MIN_HIT_RATIO,
    ) -> Tuple[bool, str]:
        """
        Return (is_stable, reason). True if the asset has SHOWN a profitable
        carry historically — not just right now.

        Method: pull HL fundingHistory (real hourly TWAP settlements) and compare
        each settlement's signed spread against SPREAD_ENTRY_THRESHOLD. The signed
        spread direction depends on which venue we'd short. If at least min_hit_ratio
        of recent hourly settlements were above threshold in the SAME direction,
        the carry is sustained — not a transient spike.

        Why this matters: NEAR right now shows instant 0.05%/h spread but actual
        HL settlements averaged 0.0007%/h over 24h (max 0.0013%/h). Without this
        check, every transient spike looks like a 500% APR opportunity. With it,
        we only enter assets that have a real, sustained spread.

        Lighter side: we use our own _rate_history as a proxy (no public Lighter
        history API). After ~24h of uptime we have full coverage; on cold start
        we relax: just require HL side to be stable.
        """
        try:
            hl_history = await self._hl.get_funding_history(asset, hours=lookback_hours)
            if not hl_history:
                return False, "no_hl_history"
            if len(hl_history) < lookback_hours // 2:
                return False, f"insufficient_hl_history ({len(hl_history)}/{lookback_hours})"

            # Build (time_ms, hl_rate) from settlements
            hl_settled = [(int(h["time"]), float(h["fundingRate"])) for h in hl_history]
            # Sign: if we'd short HL, we want hl_rate > lt_rate (HL pays us, we pay LT less).
            # Easier: directly check |hl - lt_estimated| above threshold in same direction.
            # Use our _rate_history matched by timestamp for the LT side.
            hist = self._rate_history.get(asset)
            lt_by_ts: List[Tuple[int, float]] = (
                [(ts, lt) for (ts, _hl, lt) in hist] if hist else []
            )

            hits = 0
            checked = 0
            for hl_ts_ms, hl_r in hl_settled:
                hl_ts = hl_ts_ms // 1000
                # Find nearest LT sample within 10 min of this settlement time
                lt_r: Optional[float] = None
                if lt_by_ts:
                    closest = min(lt_by_ts, key=lambda x: abs(x[0] - hl_ts))
                    if abs(closest[0] - hl_ts) <= 600:
                        lt_r = closest[1]
                if lt_r is None:
                    # No LT sample for this hour → don't count (cold start covers ≪24h)
                    continue
                checked += 1
                spread = (hl_r - lt_r) if short_venue == "hl" else (lt_r - hl_r)
                if spread >= SPREAD_ENTRY_THRESHOLD:
                    hits += 1

            if checked < lookback_hours // 4:
                # Too few overlapping samples — we just started. Fall back to
                # checking only the HL side: was it consistently producing rates
                # in our favoured direction (positive if we short HL, negative if we short LT)?
                hl_only_hits = sum(
                    1 for _, r in hl_settled
                    if (r >= SPREAD_ENTRY_THRESHOLD/2 if short_venue == "hl"
                        else r <= -SPREAD_ENTRY_THRESHOLD/2)
                )
                ratio = hl_only_hits / len(hl_settled)
                ok = ratio >= min_hit_ratio
                return ok, (
                    f"hl_only_fallback hits={hl_only_hits}/{len(hl_settled)} "
                    f"ratio={ratio:.2f} {'✓' if ok else '✗'} (LT history coverage too low)"
                )

            ratio = hits / checked
            ok = ratio >= min_hit_ratio
            return ok, (
                f"hits={hits}/{checked} ratio={ratio:.2f} "
                f"{'✓' if ok else '✗ unsustained spike'}"
            )
        except Exception as e:
            crash_log(f"SpreadScanner.is_stable_carry.{asset}", e)
            return False, "error"

    def get_twap_spread(
        self, asset: str, window_s: int = SPREAD_TWAP_WINDOW_S
    ) -> Tuple[float, bool]:
        """
        Return (twap_abs_spread, is_valid).

        Computes mean |hl_rate - lt_rate| over the last window_s seconds.
        is_valid=False if fewer than SPREAD_TWAP_MIN_SAMPLES in the window —
        caller must treat this as "not enough data, block entry" (fail-closed).

        This is the primary defence against transient-spike entries: Lighter's
        /funding-rates returns an instantaneous predicted rate (current premium/8),
        but actual settlement is the TWAP of 60 per-minute premiums over the hour.
        A 3-min spike at 0.04%/h normalises to ~0.003%/h TWAP within the hour.
        Requiring the 15-min rolling mean to be above threshold before entry
        ensures we only enter when the spread is genuinely sustained.
        """
        hist = self._rate_history.get(asset)
        if not hist:
            return 0.0, False
        cutoff = int(time.time()) - window_s
        in_window = [(h, l) for (ts, h, l) in hist if ts >= cutoff]
        if len(in_window) < SPREAD_TWAP_MIN_SAMPLES:
            return 0.0, False
        twap = sum(abs(h - l) for (h, l) in in_window) / len(in_window)
        return twap, True
