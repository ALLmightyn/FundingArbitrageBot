"""Raw funding rate inspection."""
import asyncio, os, config, aiohttp
from venues.hyperliquid import HLClient
from venues.lighter import LighterClient

async def main():
    hl = HLClient(config.HL_PRIVATE_KEY, testnet=config.TESTNET)
    lt = LighterClient(
        l1_address=os.getenv("LIGHTER_L1_ADDRESS", ""),
        account_index=int(os.getenv("LIGHTER_ACCOUNT_INDEX", "0") or 0),
        api_key_index=int(os.getenv("LIGHTER_API_KEY_INDEX", "0") or 0),
        api_private_key=os.getenv("LIGHTER_API_PRIVATE_KEY", ""),
    )

    async with aiohttp.ClientSession() as sess:
        async with sess.get("https://mainnet.zklighter.elliot.ai/api/v1/funding-rates") as r:
            data = await r.json()

    # Group by exchange
    by_exchange = {}
    for e in data.get("funding_rates", []):
        exch = e.get("exchange", "?")
        by_exchange.setdefault(exch, []).append(e)

    print("=== Exchanges in /funding-rates ===")
    for exch, entries in by_exchange.items():
        print(f"  {exch}: {len(entries)} assets")

    print()
    print("=== Lighter own rates (top 10 by abs rate) ===")
    lt_entries = sorted(by_exchange.get("lighter", []), key=lambda x: abs(x.get("rate", 0)), reverse=True)
    for e in lt_entries[:10]:
        r_pct = float(e["rate"]) * 100
        apr = r_pct * 8760
        print(f"  {e['symbol']:<20} rate={r_pct:.4f}%   APR={apr:.0f}%   raw={e['rate']}")

    print()
    print("=== HL rates via Lighter oracle ===")
    hl_via_lt = sorted(by_exchange.get("hyperliquid", []), key=lambda x: abs(x.get("rate", 0)), reverse=True)
    for e in hl_via_lt[:5]:
        r_pct = float(e["rate"]) * 100
        print(f"  {e['symbol']:<20} rate={r_pct:.4f}%   raw={e['rate']}")

    print()
    print("=== HL get_funding_rates (live, first 5) ===")
    hl_rates_raw = await hl.get_funding_rates()
    items = sorted(hl_rates_raw, key=lambda x: abs(float(x.get("funding", 0))), reverse=True)
    for item in items[:5]:
        coin = item.get("coin", "?")
        f = float(item.get("funding", 0))
        print(f"  {coin:<10} funding={f:.6f}  = {f*100:.4f}%/h  APR={f*8760*100:.0f}%")

    print()
    print("=== KEY: Lighter rate period check (BTC comparison) ===")
    btc_lt = next((e for e in by_exchange.get("lighter", []) if "BTC" in e.get("symbol", "")), None)
    btc_hl = next((e for e in by_exchange.get("hyperliquid", []) if "BTC" in e.get("symbol", "")), None)
    btc_hl_live = next((x for x in hl_rates_raw if x.get("coin") == "BTC"), None)
    print(f"  BTC Lighter: {btc_lt}")
    print(f"  BTC HL via Lighter oracle: {btc_hl}")
    print(f"  BTC HL live: funding={btc_hl_live.get('funding') if btc_hl_live else 'N/A'}")

asyncio.run(main())
