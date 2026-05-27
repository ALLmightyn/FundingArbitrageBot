"""Live arbitrage opportunity scanner. Read-only — no orders placed."""
import asyncio
from venues.lighter import LighterClient

POSITION_USD = 300.0   # per leg
FEE_ROUND_TRIP = 0.00030  # HL maker x2 (Lighter is 0%)


async def main():
    lt = LighterClient(l1_address="0x0000000000000000000000000000000000000000")

    all_rates = await lt.get_all_exchange_funding_rates()

    hl      = all_rates.get("hyperliquid", {})
    lighter = all_rates.get("lighter", {})
    binance = all_rates.get("binance", {})
    bybit   = all_rates.get("bybit", {})

    common = set(hl.keys()) & set(lighter.keys())

    results = []
    for asset in common:
        hl_r = hl[asset]
        lt_r = lighter[asset]

        # Net income when delta-neutral:
        # short high-rate side earns its rate (positive = earn, negative = pay)
        # long low-rate side earns the negative of its rate (negative rate = longs earn)
        if hl_r >= lt_r:
            short_v, long_v = "HL", "Lighter"
            net = hl_r - lt_r  # e.g. hl=+0.0001, lt=-0.0002 -> net=+0.0003 (earn both)
        else:
            short_v, long_v = "Lighter", "HL"
            net = lt_r - hl_r

        apr = net * 8760 * 100
        hourly_usd = net * POSITION_USD
        daily_usd  = hourly_usd * 24
        yearly_usd = hourly_usd * 8760
        fee_usd    = POSITION_USD * FEE_ROUND_TRIP
        breakeven_h = fee_usd / hourly_usd if hourly_usd > 0 else 9999

        results.append({
            "net": net, "apr": apr, "asset": asset,
            "hl_r": hl_r, "lt_r": lt_r,
            "short_v": short_v, "long_v": long_v,
            "hourly_usd": hourly_usd, "daily_usd": daily_usd,
            "yearly_usd": yearly_usd, "breakeven_h": breakeven_h,
        })

    results.sort(key=lambda x: x["net"], reverse=True)

    print(f"\nLive exchange rates: HL={len(hl)} assets  Lighter={len(lighter)} assets")
    print(f"Common HL+Lighter assets: {len(common)}")
    print(f"Position size per leg: ${POSITION_USD:.0f}  |  Round-trip fees: {FEE_ROUND_TRIP*100:.3f}% = ${POSITION_USD*FEE_ROUND_TRIP:.2f}")
    print()

    # Above threshold (≥0.01%/h spread)
    above = [r for r in results if r["net"] >= 0.0001]
    below = [r for r in results if 0 < r["net"] < 0.0001]
    negative = [r for r in results if r["net"] <= 0]

    print(f"=== ABOVE THRESHOLD (>=0.010%/h spread) --- {len(above)} assets ===")
    print(f"{'Asset':<7} {'Spread/h':>10} {'APR':>9} {'Break-even':>12} {'Daily/300':>11}  Short ->Long")
    print("-"*78)
    for r in above[:20]:
        be = f"{r['breakeven_h']:.1f}h" if r["breakeven_h"] < 999 else ">999h"
        print(
            f"{r['asset']:<7} {r['net']*100:>9.4f}%  {r['apr']:>8.1f}%  {be:>11}  "
            f"${r['daily_usd']:>7.3f}/day  {r['short_v']} ->{r['long_v']}"
            f"  (HL={r['hl_r']*100:.4f}% LT={r['lt_r']*100:.4f}%)"
        )

    print()
    print(f"=== MARGINAL (0-0.010%/h) --- {len(below)} assets ===")
    for r in below[:5]:
        print(f"  {r['asset']:<7} {r['net']*100:.4f}%/h  {r['apr']:.1f}% APR")

    print()
    print(f"=== NEGATIVE SPREAD (avoid) --- {len(negative)} assets ===")
    neg_names = [r["asset"] for r in negative[:15]]
    print(f"  {', '.join(neg_names)}")

    # Summary for top 5 actionable
    print()
    print("=== TOP 5 ACTIONABLE --- realistic projection at $300/leg ===")
    for i, r in enumerate(above[:5], 1):
        be = r["breakeven_h"]
        hold_days = max(be / 24, 0.25)
        print(f"\n#{i}  {r['asset']}")
        print(f"    Spread: {r['net']*100:.4f}%/h  -> {r['apr']:.1f}% APR")
        print(f"    HL rate: {r['hl_r']*100:+.4f}%/h   Lighter rate: {r['lt_r']*100:+.4f}%/h")
        print(f"    Strategy: SHORT {r['short_v']}, LONG {r['long_v']}")
        print(f"    Income:  ${r['hourly_usd']:.4f}/h  -> ${r['daily_usd']:.3f}/day  -> ${r['yearly_usd']:.1f}/year")
        print(f"    Fees:    ${POSITION_USD*FEE_ROUND_TRIP:.4f} round-trip")
        print(f"    Break-even: {be:.1f}h  |  Min hold recommendation: {max(be*2, 4):.0f}h")

    await lt.close()


asyncio.run(main())
