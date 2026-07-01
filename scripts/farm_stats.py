"""Read-only farm-optics report: per-venue traded volume, Sharpe, and points-per-fee.

Reframes the bot's output for the airdrop/points lens (HL S3 live, Lighter S3 likely):
carry PnL is no longer the only goal — delta-neutral *volume* and *consistency (Sharpe)*
are what earn points. This tells us whether the (small) carry cost is cheap volume.

Each closed cycle trades `notional_usd` per leg, twice (open + close), on EACH venue.
So per cycle: HL volume ≈ 2×notional, Lighter volume ≈ 2×notional.

Usage:  python3 scripts/farm_stats.py
"""
import os
import sqlite3
import math
from datetime import datetime, timezone

DB = os.getenv("DB_PATH", "database/carry.db")


def main() -> None:
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT asset, short_venue, long_venue, notional_usd, entered_at, exited_at, "
        "entry_fee_usd, exit_fee_usd, hl_funding_collected, lighter_funding_collected, "
        "net_pnl_usd FROM cross_venue_cycles WHERE state='CLOSED' AND exited_at IS NOT NULL "
        "ORDER BY entered_at"
    ).fetchall()
    if not rows:
        print("No closed cycles.")
        return

    n = len(rows)
    notional = sum(r["notional_usd"] or 0 for r in rows)
    # Both venues traded on every cycle (delta-neutral); 2 trades/leg (open+close).
    hl_vol = lt_vol = 2.0 * notional
    total_vol = hl_vol + lt_vol
    fees = sum((r["entry_fee_usd"] or 0) + (r["exit_fee_usd"] or 0) for r in rows)
    funding = sum((r["hl_funding_collected"] or 0) + (r["lighter_funding_collected"] or 0) for r in rows)
    net = sum(r["net_pnl_usd"] or 0 for r in rows)

    span_s = (rows[-1]["exited_at"] or rows[-1]["entered_at"]) - rows[0]["entered_at"]
    span_d = max(span_s / 86400.0, 1e-9)

    # Daily-pnl Sharpe (annualised). Group net_pnl by UTC day.
    by_day = {}
    for r in rows:
        d = datetime.fromtimestamp(r["entered_at"], tz=timezone.utc).strftime("%Y-%m-%d")
        by_day[d] = by_day.get(d, 0.0) + (r["net_pnl_usd"] or 0)
    daily = list(by_day.values())
    if len(daily) >= 2:
        mean = sum(daily) / len(daily)
        var = sum((x - mean) ** 2 for x in daily) / (len(daily) - 1)
        sd = math.sqrt(var)
        sharpe = (mean / sd) * math.sqrt(365) if sd > 0 else float("nan")
    else:
        sharpe = float("nan")

    print(f"=== FARM-OPTICS REPORT ({DB}) ===")
    print(f"Window:            {span_d:.1f} days | {n} closed cycles | {len(daily)} active days")
    print(f"Traded volume:     HL ${hl_vol:,.0f}  Lighter ${lt_vol:,.0f}  TOTAL ${total_vol:,.0f}")
    print(f"Volume rate:       ${total_vol/span_d:,.0f}/day  (~${total_vol/span_d*30:,.0f}/mo)")
    print(f"Funding collected: ${funding:+.4f}")
    print(f"Fees burned:       ${fees:.4f}")
    print(f"Net carry P&L:     ${net:+.4f}  ({net/span_d:+.4f}/day)")
    print(f"Cost of volume:    {(-net/total_vol)*1e4:.2f} bps  (we pay this fraction of every $ traded to run the engine; <0 = profit)")
    print(f"Carry Sharpe:      {sharpe:.2f}  (annualised, daily net P&L)")
    print()
    print("VERDICT: if a future S3 airdrop pays > ${:.2f} for ${:,.0f} delta-neutral volume,".format(-net if net < 0 else 0.0, total_vol))
    print("         the carry cost is worth it. Pure-carry alone: ${:+.4f} ({:+.1f}%/yr on $2k).".format(net, (net/span_d*365)/2000*100))
    con.close()


if __name__ == "__main__":
    main()
