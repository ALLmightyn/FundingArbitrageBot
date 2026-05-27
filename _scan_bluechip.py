"""Blue-chip asset scan + portfolio projection. Read-only."""
import asyncio
from venues.lighter import LighterClient

BLUE_CHIPS = ["BTC","ETH","SOL","BNB","XRP","DOGE","ADA","AVAX","LINK","DOT",
               "NEAR","ATOM","UNI","AAVE","MKR","OP","ARB","SUI","APT","INJ",
               "XMR","LTC","BCH","ETC","ALGO","MATIC","FIL"]

POSITION_USD = 300.0
FEE_RT = 0.00030

async def main():
    lt = LighterClient(l1_address="0x0000000000000000000000000000000000000000")
    all_rates = await lt.get_all_exchange_funding_rates()
    hl = all_rates.get("hyperliquid", {})
    lighter = all_rates.get("lighter", {})

    print("\n=== ALL ASSETS ABOVE 0 SPREAD (sorted by net rate) ===")
    print(f"{'Asset':<7} {'Net/h':>9}  {'APR':>8}  {'Daily':>9}  {'Break-even':>11}  Short->Long  HL rate    LT rate")
    print("-"*90)

    all_results = []
    for asset in set(hl.keys()) & set(lighter.keys()):
        hl_r = hl[asset]
        lt_r = lighter[asset]
        if hl_r >= lt_r:
            short_v, long_v = "HL", "LT"
            net = hl_r - lt_r
        else:
            short_v, long_v = "LT", "HL"
            net = lt_r - hl_r
        if net <= 0:
            continue
        all_results.append((net, asset, hl_r, lt_r, short_v, long_v))

    all_results.sort(reverse=True)
    for net, asset, hl_r, lt_r, short_v, long_v in all_results:
        hourly = net * POSITION_USD
        daily  = hourly * 24
        apr    = net * 8760 * 100
        be_h   = (POSITION_USD * FEE_RT) / hourly if hourly > 0 else 9999
        mark = " [BLUE]" if asset in BLUE_CHIPS else ""
        print(
            f"{asset:<7} {net*100:>8.4f}%  {apr:>7.0f}%  ${daily:>7.3f}/d  {be_h:>8.1f}h  "
            f"{short_v}->{long_v}  {hl_r*100:>+8.4f}%  {lt_r*100:>+8.4f}%{mark}"
        )

    # Summary blue chips only
    blue = [(n,a,h,l,sv,lv) for n,a,h,l,sv,lv in all_results if a in BLUE_CHIPS]
    print(f"\n=== BLUE-CHIP OPPORTUNITIES ({len(blue)} found) ===")
    for net, asset, hl_r, lt_r, short_v, long_v in blue:
        hourly = net * POSITION_USD
        daily  = hourly * 24
        apr    = net * 8760 * 100
        be_h   = (POSITION_USD * FEE_RT) / hourly if hourly > 0 else 9999
        print(f"  {asset:<7} {net*100:.4f}%/h  {apr:.0f}% APR  ${daily:.3f}/day (break-even {be_h:.1f}h)  SHORT {short_v} LONG {long_v}")

    # Portfolio simulation: 2 positions, $300/leg each
    top2 = [r for r in blue[:2]] if len(blue) >= 2 else blue
    if not top2:
        top2 = all_results[:2]
    print(f"\n=== PORTFOLIO SIMULATION: top-2 blue chips, $300/leg each ===")
    total_daily = 0.0
    for net, asset, hl_r, lt_r, short_v, long_v in top2:
        daily = net * POSITION_USD * 24
        total_daily += daily
        print(f"  {asset}: ${daily:.3f}/day")
    total_annual = total_daily * 365
    capital_deployed = POSITION_USD * 2 * 2  # 2 positions x 2 legs
    annual_pct = total_annual / capital_deployed * 100
    fees_annual = POSITION_USD * FEE_RT * 2 * 365 / 30  # assuming monthly cycles
    print(f"  Total: ${total_daily:.3f}/day -> ${total_annual:.0f}/year")
    print(f"  Capital deployed: ${capital_deployed:.0f} (${POSITION_USD:.0f}/leg x 2 positions x 2 exchanges)")
    print(f"  Net APY on capital: {annual_pct:.1f}%")

    await lt.close()

asyncio.run(main())
