"""venues/hyperliquid.py — Hyperliquid REST + WebSocket adapter.

Wraps the official hyperliquid-python-sdk with:
  - Post-Only (ALO) maker order placement with Maker Chase repost logic
  - Bulk cancel
  - Funding rate + predicted funding queries
  - Clearinghouse state (margin ratio, positions)
  - WebSocket subscription for real-time orderbook / funding updates
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict, List, Optional, Callable, Awaitable

import aiohttp
import eth_account
from eth_account.signers.local import LocalAccount
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils import constants as hl_constants

from core.constants import (
    HL_REST_URL, HL_TESTNET_REST_URL, HL_WS_URL, HL_TESTNET_WS_URL,
    MAKER_CHASE_MAX_REPOSTS, MAKER_CHASE_REPOST_DELAY_S, MAKER_CHASE_TIMEOUT_S,
)
from core.logger import log, log_warn, log_err, crash_log


class HLClient:
    """
    Thin async wrapper over hyperliquid-python-sdk.

    The SDK's Exchange and Info classes are synchronous — we run them in the
    default executor so they don't block the event loop.
    """

    def __init__(self, private_key: str, testnet: bool = True):
        self._testnet = testnet
        self._rest_url = HL_TESTNET_REST_URL if testnet else HL_REST_URL
        self._ws_url   = HL_TESTNET_WS_URL   if testnet else HL_WS_URL

        base_url = hl_constants.TESTNET_API_URL if testnet else hl_constants.MAINNET_API_URL

        self._account: LocalAccount = eth_account.Account.from_key(private_key)
        self._exchange = Exchange(self._account, base_url)
        self._info     = Info(base_url, skip_ws=True)

        self._address: str = self._account.address
        # asset → szDecimals (populated lazily on first order)
        self._sz_decimals: Dict[str, int] = {}
        # Spot routing caches (populated by _ensure_spot_meta)
        self._spot_meta: Optional[Dict] = None
        self._perp_to_spot: Dict[str, str] = {}      # "BTC" → "@142" or "PURR" → "PURR/USDC"
        self._spot_sz_decimals: Dict[str, int] = {}  # spot pair name → base token szDecimals
        self._perp_universe: List[str] = []
        log(f"HLClient ready | address={self._address[:10]}... | testnet={testnet}")

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _run(self, fn: Callable, *args, **kwargs) -> Any:
        """Run a synchronous SDK call in the executor without blocking the loop."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: fn(*args, **kwargs))

    async def _get_sz_decimals(self, asset: str) -> int:
        """Return szDecimals for asset (cached). Used to round order sizes correctly."""
        if asset not in self._sz_decimals:
            meta = await self.get_meta()
            for u in meta.get("universe", []):
                self._sz_decimals[u["name"]] = int(u.get("szDecimals", 4))
        return self._sz_decimals.get(asset, 4)

    async def round_size(self, asset: str, size: float) -> float:
        """Round size to the asset's szDecimals to avoid float_to_wire errors."""
        decimals = await self._get_sz_decimals(asset)
        return round(size, decimals)

    @staticmethod
    def _price_decimals(px: float) -> int:
        """Derive tick-size decimal places from price magnitude.
        HL tick: BTC~$1 (0dp), ETH~$0.1 (1dp), SOL~$0.01 (2dp), etc.
        Formula: 5 - digits_in_integer_part, floored at 0."""
        return max(0, 5 - len(str(int(abs(px)))))

    def round_price(self, px: float) -> float:
        """Round price to HL tick size to avoid 'Price must be divisible by tick size'."""
        return round(px, self._price_decimals(px))

    # ── Spot routing ─────────────────────────────────────────────────────────

    async def _ensure_spot_meta(self) -> None:
        """Lazily fetch spot meta and build perp→spot pair mapping.
        Maps perp asset (e.g. 'BTC') → spot pair name (e.g. '@142').
        Match rules:
          - Canonical name in perp universe (PURR, HYPE) → match directly.
          - U-prefixed token (UBTC, UETH, USOL) → strip U, match perp.
        Skips quote != USDC. Validates against actual perp universe.
        """
        if self._spot_meta is not None:
            return
        # Need perp universe first
        if not self._perp_universe:
            meta = await self.get_meta()
            self._perp_universe = [u["name"] for u in meta.get("universe", [])]
            for u in meta.get("universe", []):
                self._sz_decimals[u["name"]] = int(u.get("szDecimals", 4))
        spot_meta = await self._run(self._info.spot_meta)
        self._spot_meta = spot_meta
        tokens = {t["index"]: t for t in spot_meta.get("tokens", [])}
        perp_set = set(self._perp_universe)
        for u in spot_meta.get("universe", []):
            base_idx, quote_idx = u["tokens"][0], u["tokens"][1]
            base = tokens.get(base_idx, {})
            quote = tokens.get(quote_idx, {})
            if quote.get("name") != "USDC":
                continue
            base_name = base.get("name", "")
            sz_dec = int(base.get("szDecimals", 4))
            # Canonical perp name (PURR, HYPE) match
            if base_name in perp_set:
                self._perp_to_spot[base_name] = u["name"]
                self._spot_sz_decimals[u["name"]] = sz_dec
                continue
            # U-prefix wrapped match (UBTC → BTC)
            if base_name.startswith("U") and len(base_name) >= 4:
                stripped = base_name[1:]
                if stripped in perp_set:
                    self._perp_to_spot[stripped] = u["name"]
                    self._spot_sz_decimals[u["name"]] = sz_dec
        log(f"Spot routing: {len(self._perp_to_spot)} perp→spot pairs available")

    async def get_spot_pair(self, perp_asset: str) -> Optional[str]:
        """Return spot pair name for perp asset, or None if no spot pair exists."""
        await self._ensure_spot_meta()
        return self._perp_to_spot.get(perp_asset)

    async def round_spot_size(self, spot_pair: str, size: float) -> float:
        """Round size to spot pair's base token szDecimals."""
        await self._ensure_spot_meta()
        decimals = self._spot_sz_decimals.get(spot_pair, 4)
        return round(size, decimals)

    # ── Account info ─────────────────────────────────────────────────────────

    async def get_clearinghouse(self) -> Dict:
        """Return full perp clearinghouse state for our address."""
        return await self._run(self._info.user_state, self._address)

    async def get_perp_position_size(self, asset: str) -> float:
        """Return absolute size of current perp position for asset (0.0 if flat)."""
        ch = await self.get_clearinghouse()
        for ap in ch.get("assetPositions", []):
            p = ap.get("position", {})
            if p.get("coin") == asset:
                return abs(float(p.get("szi", 0) or 0))
        return 0.0

    async def get_spot_balances(self) -> List[Dict]:
        """Return spot balances for our address."""
        state = await self._run(self._info.spot_user_state, self._address)
        return state.get("balances", [])

    async def get_usdc_balance(self) -> float:
        """
        Return available USDC for trading.
        On unified accounts, funds sit in spot as USDC and are used as
        cross-margin collateral for perps — clearinghouseState shows $0.
        Falls back to marginSummary.accountValue for non-unified accounts.
        """
        # Try spot USDC first (unified account)
        try:
            balances = await self.get_spot_balances()
            for b in balances:
                if b.get("coin") == "USDC":
                    total = float(b.get("total", 0) or 0)
                    hold  = float(b.get("hold", 0) or 0)
                    return max(0.0, total - hold)
        except Exception:
            pass
        # Fallback: perp accountValue (non-unified)
        try:
            state = await self.get_clearinghouse()
            return float(state.get("marginSummary", {}).get("accountValue", 0) or 0)
        except Exception:
            return 0.0

    # ── Market data ───────────────────────────────────────────────────────────

    async def get_meta(self) -> Dict:
        """Return perp metadata (assets, leverage tiers, margin tables)."""
        return await self._run(self._info.meta)

    async def get_all_mids(self) -> Dict[str, str]:
        """Return mid prices for all perp assets: {asset: mid_px_str}."""
        return await self._run(self._info.all_mids)

    async def get_funding_rates(self) -> List[Dict]:
        """
        Return current funding data per asset.
        Each item: {coin, fundingRate, premium, openInterest, markPx, oraclePx, ...}
        """
        meta_and_asset_ctxs = await self._run(self._info.meta_and_asset_ctxs)
        # meta_and_asset_ctxs = [meta_dict, [assetCtx, ...]]
        meta   = meta_and_asset_ctxs[0]
        ctxs   = meta_and_asset_ctxs[1]
        assets = [u["name"] for u in meta["universe"]]
        def _f(v, default=0.0) -> float:
            try:
                return float(v) if v is not None else default
            except (TypeError, ValueError):
                return default

        result = []
        for asset, ctx in zip(assets, ctxs):
            if ctx is None:
                continue
            mark = _f(ctx.get("markPx"))
            oracle = _f(ctx.get("oraclePx"))
            if mark == 0.0 and oracle == 0.0:
                continue  # skip delisted / inactive assets
            result.append({
                "asset":        asset,
                "funding":      _f(ctx.get("funding")),
                "openInterest": _f(ctx.get("openInterest")),
                "markPx":       mark,
                "oraclePx":     oracle,
                "premium":      _f(ctx.get("premium")),
            })
        return result

    async def get_predicted_fundings(self) -> List[Dict]:
        """
        Return predicted funding rates for next period via raw POST.
        Returns: [{coin: str, fundingRate: str}, ...]
        """
        try:
            result = await self._run(self._info.post, "/info", {"type": "predictedFundings"})
            return result if isinstance(result, list) else []
        except Exception:
            # Fallback: derive from current funding in meta_and_asset_ctxs (already fetched)
            return []

    async def get_l2_book(self, asset: str) -> Dict:
        """Return L2 orderbook snapshot for a perp asset."""
        return await self._run(self._info.l2_snapshot, asset)

    # ── Order placement ───────────────────────────────────────────────────────

    async def place_maker(
        self,
        asset: str,
        is_buy: bool,
        size: float,
        price: float,
        reduce_only: bool = False,
        is_spot: bool = False,
    ) -> Dict:
        """Place a Post-Only (ALO) limit order on perp or spot.
        For spot, asset must be the spot pair name (e.g. '@142' or 'PURR/USDC')
        and reduce_only must be False (spot has no concept of reduce-only).
        """
        size = await self.round_spot_size(asset, size) if is_spot else await self.round_size(asset, size)
        order_type = {"limit": {"tif": "Alo"}}
        ro = False if is_spot else reduce_only
        return await self._run(
            self._exchange.order, asset, is_buy, size, price, order_type, reduce_only=ro,
        )

    async def place_taker(
        self,
        asset: str,
        is_buy: bool,
        size: float,
        price: float,
        reduce_only: bool = False,
        is_spot: bool = False,
    ) -> Dict:
        """Place an IOC order (taker). Emergency cover / Iron Dome fallback only."""
        size = await self.round_spot_size(asset, size) if is_spot else await self.round_size(asset, size)
        order_type = {"limit": {"tif": "Ioc"}}
        ro = False if is_spot else reduce_only
        return await self._run(
            self._exchange.order, asset, is_buy, size, price, order_type, reduce_only=ro,
        )

    async def cancel_order(self, asset: str, order_id: int) -> Dict:
        return await self._run(self._exchange.cancel, asset, order_id)

    async def cancel_all(self, asset: str) -> None:
        """Cancel all open orders on a given asset."""
        try:
            open_orders = await self.get_open_orders()
            to_cancel = [o for o in open_orders if o.get("coin") == asset]
            for o in to_cancel:
                await self.cancel_order(asset, o["oid"])
        except Exception as e:
            crash_log(f"cancel_all.{asset}", e)

    async def get_open_orders(self) -> List[Dict]:
        return await self._run(self._info.open_orders, self._address)

    async def set_leverage(self, asset: str, leverage: int, is_cross: bool = False) -> Dict:
        return await self._run(self._exchange.update_leverage, leverage, asset, is_cross)

    # ── Maker Chase (reuse pattern from legacy SpreadManager) ─────────────────

    async def maker_chase_entry(
        self,
        asset: str,
        is_buy: bool,
        size: float,
        side_label: str = "",
        is_spot: bool = False,
        reduce_only: bool = False,
        timeout_s: Optional[float] = None,
    ) -> Optional[str]:
        """
        Place a post-only order at the touch price and repost up to
        MAKER_CHASE_MAX_REPOSTS times if the book moves.

        Returns the filled order ID (str) or None if not filled within timeout.
        Chase logic:
          1. Get current best bid/ask from L2.
          2. Place ALO at touch (best_bid for buy, best_ask for sell).
          3. Poll every MAKER_CHASE_REPOST_DELAY_S for fill or price move.
          4. If price moved >1 tick → cancel + repost.
          5. After MAX_REPOSTS or MAKER_CHASE_TIMEOUT_S → give up, return None.
        """
        label = f"{asset}{'↑BUY' if is_buy else '↓SELL'} {side_label}"
        size = await self.round_spot_size(asset, size) if is_spot else await self.round_size(asset, size)
        if size <= 0:
            log_warn(f"MakerChase {label}: size rounded to 0, skipping")
            return None
        deadline = time.time() + (timeout_s if timeout_s is not None else MAKER_CHASE_TIMEOUT_S)
        order_id: Optional[str] = None
        last_price: Optional[float] = None
        reposts = 0

        # Snapshot position size before entering chase — used to detect phantom fills after cancel.
        # Skipped for spot (requires separate balance query, less critical due to taker fallback).
        _pre_size: float = 0.0
        if not is_spot:
            try:
                _pre_size = await self.get_perp_position_size(asset)
            except Exception:
                pass

        while time.time() < deadline and reposts <= MAKER_CHASE_MAX_REPOSTS:
            # Get current touch price
            book = await self.get_l2_book(asset)
            levels = book.get("levels", [[], []])
            bid_levels, ask_levels = levels[0], levels[1]

            if is_buy:
                if not bid_levels:
                    log_warn(f"MakerChase {label}: empty bid side, waiting...")
                    await asyncio.sleep(MAKER_CHASE_REPOST_DELAY_S)
                    continue
                touch_price = float(bid_levels[0]["px"])
            else:
                if not ask_levels:
                    log_warn(f"MakerChase {label}: empty ask side, waiting...")
                    await asyncio.sleep(MAKER_CHASE_REPOST_DELAY_S)
                    continue
                touch_price = float(ask_levels[0]["px"])

            # Repost if price moved or first placement
            if order_id is not None and last_price is not None:
                if abs(touch_price - last_price) < 1e-6:
                    # Price unchanged — check if order is still open
                    open_orders = await self.get_open_orders()
                    our_order = next((o for o in open_orders if str(o.get("oid")) == order_id), None)
                    if our_order is None:
                        # Order disappeared → filled!
                        log_ok = lambda m: log(f"✓ {m}")
                        log(f"MakerChase {label}: FILLED @ {last_price:.4f}")
                        return order_id
                    await asyncio.sleep(MAKER_CHASE_REPOST_DELAY_S)
                    continue
                else:
                    # Price moved — cancel and repost
                    log(f"MakerChase {label}: price moved {last_price:.4f}→{touch_price:.4f}, reposting ({reposts+1}/{MAKER_CHASE_MAX_REPOSTS})")
                    try:
                        await self.cancel_order(asset, int(order_id))
                    except Exception:
                        pass
                    # Phantom fill check: exchange may have filled the order in the same ms as cancel
                    if not is_spot:
                        try:
                            post_sz = await self.get_perp_position_size(asset)
                            if abs(post_sz - _pre_size) > size * 0.5:
                                log(f"MakerChase {label}: phantom fill on repost-cancel "
                                    f"(sz {_pre_size:.6f}→{post_sz:.6f})")
                                return order_id
                        except Exception:
                            pass
                    order_id = None
                    reposts += 1

            if reposts > MAKER_CHASE_MAX_REPOSTS:
                log_warn(f"MakerChase {label}: max reposts reached, giving up")
                return None

            # Place new ALO order
            try:
                touch_price = self.round_price(touch_price)
                result = await self.place_maker(asset, is_buy, size, touch_price,
                                               reduce_only=reduce_only, is_spot=is_spot)
                status = result.get("response", {}).get("data", {}).get("statuses", [{}])[0]
                if "resting" in status:
                    order_id = str(status["resting"]["oid"])
                    last_price = touch_price
                    log(f"MakerChase {label}: resting @ {touch_price:.4f} oid={order_id}")
                elif "filled" in status:
                    log(f"MakerChase {label}: instant fill @ {touch_price:.4f}")
                    return str(status["filled"]["oid"])
                elif "error" in status:
                    log_warn(f"MakerChase {label}: order error: {status['error']}")
                    await asyncio.sleep(MAKER_CHASE_REPOST_DELAY_S)
                    continue
            except Exception as e:
                crash_log(f"maker_chase_entry.{label}", e)
                await asyncio.sleep(MAKER_CHASE_REPOST_DELAY_S)
                continue

            await asyncio.sleep(MAKER_CHASE_REPOST_DELAY_S)

        if order_id:
            try:
                await self.cancel_order(asset, int(order_id))
            except Exception:
                pass
            # Phantom fill check after timeout-cancel: position may have appeared on exchange
            if not is_spot:
                try:
                    post_sz = await self.get_perp_position_size(asset)
                    if abs(post_sz - _pre_size) > size * 0.5:
                        log(f"MakerChase {label}: phantom fill on timeout-cancel "
                            f"(sz {_pre_size:.6f}→{post_sz:.6f})")
                        return order_id
                except Exception:
                    pass
        log_warn(f"MakerChase {label}: timed out, order not filled")
        return None

    # ── WebSocket subscription ────────────────────────────────────────────────

    async def ws_listen(
        self,
        subscriptions: List[Dict],
        on_message: Callable[[Dict], Awaitable[None]],
    ) -> None:
        """
        Subscribe to HL WebSocket channels and call on_message for each event.
        Auto-reconnects on disconnect.

        subscriptions example:
          [{"type": "allMids"}, {"type": "userFundings", "user": address}]
        """
        import websockets  # lazy import — optional dep at ws time

        backoff = 5
        fails = 0
        MAX_FAILS = 5  # give up after 5 consecutive failures (network blocked)
        while True:
            try:
                log(f"WS: connecting to {self._ws_url}")
                async with websockets.connect(
                    self._ws_url,
                    ping_interval=None,
                    close_timeout=5,
                ) as ws:
                    for sub in subscriptions:
                        await ws.send(json.dumps({"method": "subscribe", "subscription": sub}))
                    log(f"WS: subscribed to {len(subscriptions)} channels")
                    backoff = 5
                    fails = 0  # reset on successful connect

                    async def _heartbeat():
                        while True:
                            await asyncio.sleep(25)
                            await ws.send(json.dumps({"method": "ping"}))

                    hb_task = asyncio.create_task(_heartbeat())
                    try:
                        async for raw in ws:
                            try:
                                msg = json.loads(raw)
                                await on_message(msg)
                            except Exception as e:
                                crash_log("ws_listen.on_message", e)
                    finally:
                        hb_task.cancel()

            except Exception as e:
                fails += 1
                if fails >= MAX_FAILS:
                    log_warn(
                        f"WS: {fails} consecutive failures ({e}). "
                        f"HL WebSocket appears blocked (VPN/proxy needed). "
                        f"Funding payments will be tracked via REST polling instead. "
                        f"WS disabled."
                    )
                    return  # exit loop silently — bot continues without WS
                log_warn(f"WS: disconnected ({e}), reconnecting in {backoff}s... ({fails}/{MAX_FAILS})")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
