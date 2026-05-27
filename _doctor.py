"""_doctor.py — Connectivity diagnostics. NO TRADES.

Tests:
  1. HL REST API
  2. HL WebSocket
  3. Lighter REST API
  4. Lighter SDK signing (the 'nonce' issue)
  5. Telegram API
  6. Proxy env vars detected
"""
import asyncio, os, time
from dotenv import load_dotenv
load_dotenv()

OK   = "[OK]   "
FAIL = "[FAIL] "
WARN = "[WARN] "


async def test_hl_rest():
    try:
        from venues.hyperliquid import HLClient
        c = HLClient(os.getenv("HL_PRIVATE_KEY"), testnet=False)
        state = await c.get_clearinghouse()
        return f"{OK} HL REST: connected (time={state.get('time')})"
    except Exception as e:
        return f"{FAIL} HL REST: {type(e).__name__}: {e}"


async def test_hl_ws():
    try:
        import websockets
        async with asyncio.timeout(8):
            async with websockets.connect(
                "wss://api.hyperliquid.xyz/ws", close_timeout=3
            ) as ws:
                return f"{OK} HL WebSocket: connected"
    except asyncio.TimeoutError:
        return f"{FAIL} HL WebSocket: timeout (likely blocked/proxied)"
    except Exception as e:
        return f"{FAIL} HL WebSocket: {type(e).__name__}: {e}"


async def test_lighter_rest():
    try:
        from venues.lighter import LighterClient
        lt = LighterClient(
            l1_address=os.getenv("LIGHTER_L1_ADDRESS"),
            account_index=int(os.getenv("LIGHTER_ACCOUNT_INDEX")),
            api_key_index=int(os.getenv("LIGHTER_API_KEY_INDEX")),
            api_private_key=os.getenv("LIGHTER_API_PRIVATE_KEY"),
        )
        acct = await lt.get_account()
        return f"{OK} Lighter REST: balance=${float(acct.get('available_balance',0)):.2f}"
    except Exception as e:
        return f"{FAIL} Lighter REST: {type(e).__name__}: {e}"


async def test_lighter_signer():
    """The 'couldn't get nonce' error was caused by double /api/v1 path."""
    try:
        from lighter.signer_client import SignerClient
        from core.constants import LIGHTER_SDK_URL
        client = SignerClient(
            url=LIGHTER_SDK_URL,
            api_private_keys={int(os.getenv("LIGHTER_API_KEY_INDEX")): os.getenv("LIGHTER_API_PRIVATE_KEY")},
            account_index=int(os.getenv("LIGHTER_ACCOUNT_INDEX")),
        )
        err = client.check_client()
        await client.close()
        if err:
            return f"{FAIL} Lighter signing: check_client returned {err}"
        return f"{OK} Lighter signing: private key matches registered public key"
    except Exception as e:
        return f"{FAIL} Lighter signing: {type(e).__name__}: {e}"


async def test_telegram():
    tok = os.getenv("TELEGRAM_TOKEN", "")
    cid = os.getenv("TELEGRAM_CHAT_ID", "")
    proxy = os.getenv("TELEGRAM_PROXY", "").strip() or None
    if not tok:
        return f"{WARN} Telegram: TOKEN not set"
    try:
        import aiohttp
        async with aiohttp.ClientSession() as s:
            async with asyncio.timeout(8):
                r = await s.get(
                    f"https://api.telegram.org/bot{tok}/getMe",
                    proxy=proxy,
                )
                d = await r.json()
                if d.get("ok"):
                    name = d["result"].get("username", "?")
                    return f"{OK} Telegram: @{name} (proxy={'YES' if proxy else 'NO'})"
                return f"{FAIL} Telegram: API rejected token"
    except asyncio.TimeoutError:
        return f"{FAIL} Telegram: timeout (api.telegram.org blocked — set TELEGRAM_PROXY)"
    except Exception as e:
        return f"{FAIL} Telegram: {type(e).__name__}: {e}"


def check_proxy_env():
    suspects = [
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
        "http_proxy", "https_proxy", "all_proxy",
        "NO_PROXY", "no_proxy",
    ]
    found = {k: os.getenv(k) for k in suspects if os.getenv(k)}
    if found:
        lines = [f"{WARN} Proxy env vars detected (may interfere with WS):"]
        for k, v in found.items():
            lines.append(f"        {k}={v}")
        return "\n".join(lines)
    return f"{OK} No system proxy env vars set"


async def main():
    print("=" * 70)
    print("FundingArbitrageBot — connectivity doctor")
    print("=" * 70)
    print(check_proxy_env())
    print()

    results = await asyncio.gather(
        test_hl_rest(),
        test_hl_ws(),
        test_lighter_rest(),
        test_lighter_signer(),
        test_telegram(),
        return_exceptions=True,
    )
    for r in results:
        print(r if isinstance(r, str) else f"{FAIL} {r}")

    print()
    print("=" * 70)
    print("Verdict: bot needs REST APIs (HL+Lighter) and Lighter signing.")
    print("HL WS and Telegram are nice-to-have but bot works without them.")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
