"""Read-only audit of live leverage/margin rules on HL + Lighter for our account."""
import asyncio, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # loads .env
from config import HL_PRIVATE_KEY, TESTNET
from venues.hyperliquid import HLClient
from venues.lighter import LighterClient


async def main():
    hl = HLClient(HL_PRIVATE_KEY, testnet=TESTNET)
    print(f"=== HL (testnet={TESTNET}) ===")
    ch = await hl.get_clearinghouse()
    ms = ch.get("marginSummary", {})
    cms = ch.get("crossMarginSummary", {})
    print("marginSummary:", json.dumps(ms))
    print("crossMarginSummary:", json.dumps(cms))
    print("crossMaintenanceMarginUsed:", ch.get("crossMaintenanceMarginUsed"))
    print("withdrawable:", ch.get("withdrawable"))
    for ap in ch.get("assetPositions", []):
        p = ap.get("position", {})
        print(f"\nPOS {p.get('coin')}:")
        print("  szi:", p.get("szi"), "entryPx:", p.get("entryPx"))
        print("  leverage:", json.dumps(p.get("leverage")))
        print("  liquidationPx:", p.get("liquidationPx"))
        print("  marginUsed:", p.get("marginUsed"), "positionValue:", p.get("positionValue"))
        print("  maxLeverage:", p.get("maxLeverage"))
        print("  unrealizedPnl:", p.get("unrealizedPnl"), "returnOnEquity:", p.get("returnOnEquity"))
        print("  cumFunding:", json.dumps(p.get("cumFunding")))

    # asset meta: max leverage + margin tiers for our traded assets
    meta = await hl.get_meta()
    print("\n=== HL asset max leverage (universe) ===")
    for u in meta.get("universe", []):
        if u.get("name") in ("APT", "BCH", "NEAR", "TAO", "BTC", "ETH", "SOL"):
            print(f"  {u.get('name')}: maxLeverage={u.get('maxLeverage')} szDecimals={u.get('szDecimals')} onlyIsolated={u.get('onlyIsolated')}")

    # ── Lighter ──
    print("\n\n=== LIGHTER ===")
    import os as _os
    lt = LighterClient(
        l1_address=_os.getenv("LIGHTER_L1_ADDRESS", "").strip(),
        account_index=int(_os.getenv("LIGHTER_ACCOUNT_INDEX")) if _os.getenv("LIGHTER_ACCOUNT_INDEX", "").strip() else None,
        api_key_index=int(_os.getenv("LIGHTER_API_KEY_INDEX")) if _os.getenv("LIGHTER_API_KEY_INDEX", "").strip() else None,
        api_private_key=_os.getenv("LIGHTER_API_PRIVATE_KEY", "").strip() or None,
    )
    acct = await lt.get_account()
    # dump top-level scalar fields + positions
    scalars = {k: v for k, v in acct.items() if not isinstance(v, (list, dict))}
    print("account scalars:", json.dumps(scalars, indent=1))
    for k, v in acct.items():
        if isinstance(v, list) and v and isinstance(v[0], dict):
            print(f"\n{k} (list of {len(v)}):")
            for item in v[:5]:
                print("  ", json.dumps(item))
    try:
        mr = await lt.get_margin_ratio()
        print("\nget_margin_ratio():", mr)
    except Exception as e:
        print("margin_ratio err:", e)
    await lt.close()


asyncio.run(main())
