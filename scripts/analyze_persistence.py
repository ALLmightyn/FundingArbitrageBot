"""
One-off analysis: for each whitelist asset, pull 24h paired funding settlements
and compute the MAX CONSECUTIVE run of hours where the spread stayed above
threshold — the metric that actually determines break-even safety.

avg_spread + hit_ratio tell you "how often / how big" but NOT "for how long in a
row". Break-even needs N consecutive hours; a scattered 11/24 can still be a loss.
"""
from __future__ import annotations
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # loads .env  # noqa
from core.constants import CROSS_VENUE_WHITELIST, FEE_CROSS_ROUND_TRIP, SPREAD_ENTRY_THRESHOLD
from venues.hyperliquid import HLClient
from venues.lighter import LighterClient
from config import HL_PRIVATE_KEY, TESTNET


def _lt_cfg():
    return (
        os.getenv("LIGHTER_L1_ADDRESS", "").strip(),
        int(os.getenv("LIGHTER_ACCOUNT_INDEX", "0") or 0),
        int(os.getenv("LIGHTER_API_KEY_INDEX", "0") or 0),
        os.getenv("LIGHTER_API_PRIVATE_KEY", "").strip(),
    )


def max_consecutive(series, floor):
    """Longest consecutive run of values >= floor, and cumulative sum over best run."""
    best_len = best_cum = 0
    cur_len = cur_cum = 0.0
    for v in series:
        if v >= floor:
            cur_len += 1
            cur_cum += v
            if cur_cum > best_cum:
                best_cum = cur_cum
                best_len = cur_len
        else:
            cur_len = cur_cum = 0
    return best_len, best_cum


async def main():
    hl = HLClient(HL_PRIVATE_KEY, testnet=TESTNET)
    addr, ai, ki, pk = _lt_cfg()
    lt = LighterClient(l1_address=addr, account_index=ai, api_key_index=ki, api_private_key=pk)

    assets = sorted(CROSS_VENUE_WHITELIST)
    hl_t = {a: asyncio.create_task(hl.get_funding_history(a, hours=24)) for a in assets}
    lt_t = {a: asyncio.create_task(lt.get_funding_history(a, hours=24)) for a in assets}

    rows = []
    fee = FEE_CROSS_ROUND_TRIP  # 0.0003 round-trip
    for a in assets:
        try:
            hl_hist = await hl_t[a]
            lt_hist = await lt_t[a]
        except Exception as e:
            continue
        if not hl_hist or not lt_hist:
            continue
        lt_by_hour = {(int(e["timestamp"]) // 3600) * 3600: float(e["rate"]) for e in lt_hist}
        paired = []
        for h in hl_hist:
            ts = int(h["time"]) // 1000
            bucket = (ts // 3600) * 3600
            if bucket in lt_by_hour:
                paired.append((ts, float(h["fundingRate"]) - lt_by_hour[bucket]))
        if len(paired) < 6:
            continue
        paired.sort()
        signed = [s for _, s in paired]
        n = len(signed)
        mean_signed = sum(signed) / n
        # dominant direction series (positive = profitable in dominant short)
        dir_series = signed if mean_signed >= 0 else [-s for s in signed]
        short = "HL" if mean_signed >= 0 else "LT"
        avg = sum(abs(x) for x in signed) / n
        hits = sum(1 for v in dir_series if v >= SPREAD_ENTRY_THRESHOLD)

        # max consecutive run above ENTRY_THRESHOLD, and cumulative spread in best run
        run_len, run_cum = max_consecutive(dir_series, SPREAD_ENTRY_THRESHOLD)
        # break-even hours at this token's average spread
        be_hours = fee / avg if avg > 0 else 999
        # does the best consecutive run actually pay off the round-trip fee?
        safe = run_cum >= fee
        rows.append((a, avg, hits, n, run_len, run_cum, be_hours, safe, short))

    rows.sort(key=lambda r: r[4], reverse=True)  # by max consecutive run
    print(f"{'Asset':<7}{'avg%/h':>8}{'hits':>7}{'maxRun':>8}{'runEarn%':>10}{'BE_hrs':>8}{'short':>7}  verdict")
    print("-" * 72)
    for a, avg, hits, n, rl, rc, be, safe, short in rows:
        v = "✅ PAYS FEE" if safe else "❌ loss"
        print(f"{a:<7}{avg*100:>7.4f} {hits:>3}/{n:<3}{rl:>7}h{rc*100:>9.4f} {be:>7.1f}{short:>7}  {v}")


if __name__ == "__main__":
    asyncio.run(main())
