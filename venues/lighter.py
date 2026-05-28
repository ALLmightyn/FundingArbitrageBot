"""venues/lighter.py — Lighter perpetuals exchange client.

Public (read-only) endpoints use raw aiohttp — no SDK required.
Order placement requires the Lighter Python SDK:
    uv pip install git+https://github.com/elliottech/zklighter-perps-python.git

Lighter uses a ZK-based signing binary (platform-specific .dll/.so/.dylib)
wrapped by the Python SDK. Standard ECDSA cannot substitute it.

Environment variables needed for trading:
    LIGHTER_ACCOUNT_INDEX   — integer account index (visible in lighter.xyz UI)
    LIGHTER_L1_ADDRESS      — your Ethereum address registered on Lighter
    LIGHTER_API_KEY_INDEX   — API key slot index (start with 2; 0-1 reserved)
    LIGHTER_API_PRIVATE_KEY — API key private key (generate in lighter.xyz → Settings)
"""
from __future__ import annotations

import asyncio
import time
from typing import Dict, List, Optional, Any

import aiohttp

from core.constants import LIGHTER_BASE_URL, LIGHTER_SDK_URL, LIGHTER_EXPLORER_URL
from core.logger import log, log_warn, crash_log

_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=12)


class LighterClient:
    """
    Async client for Lighter perpetuals exchange.

    Two modes:
      read_only  — no credentials needed; supports funding rates, order book, account query.
      trading    — requires account_index, api_key_index, api_private_key (via lighter SDK).
    """

    def __init__(
        self,
        l1_address: str,
        account_index: Optional[int] = None,
        api_key_index: Optional[int] = None,
        api_private_key: Optional[str] = None,
    ):
        self._address = l1_address.lower()
        self._account_index = account_index
        self._api_key_index = api_key_index
        self._api_private_key = api_private_key

        self._session: Optional[aiohttp.ClientSession] = None

        # Market metadata cache: normalized symbol → market dict
        # Symbol normalisation: "BTC-PERP" → "BTC", "BTC/USDC" → "BTC"
        self._markets: Dict[str, dict] = {}
        self._market_id_by_symbol: Dict[str, int] = {}

        # SDK SignerClient — lazily initialised on first trading call
        self._signer: Any = None

        # Monotonic counter for client_order_index (unique per order)
        self._order_counter: int = 0

    # ── HTTP helpers ──────────────────────────────────────────────────────────

    async def _session_(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=_REQUEST_TIMEOUT)
        return self._session

    async def _get(self, path: str, params: Optional[dict] = None) -> dict:
        session = await self._session_()
        url = f"{LIGHTER_BASE_URL}/{path.lstrip('/')}"
        async with session.get(url, params=params or {}) as resp:
            resp.raise_for_status()
            return await resp.json(content_type=None)

    async def _get_explorer(self, path: str) -> dict:
        session = await self._session_()
        url = f"{LIGHTER_EXPLORER_URL}/{path.lstrip('/')}"
        async with session.get(url) as resp:
            resp.raise_for_status()
            return await resp.json(content_type=None)

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    # ── Market metadata ───────────────────────────────────────────────────────

    @staticmethod
    def _normalize_symbol(raw: str) -> str:
        """BTC-PERP → BTC, BTC/USDC → BTC, BTC → BTC."""
        return raw.split("-")[0].split("/")[0].upper()

    async def _ensure_markets(self) -> None:
        """Load and cache market metadata once per session."""
        if self._markets:
            return
        try:
            data = await self._get("/orderBookDetails")
            for mkt in data.get("order_book_details", []):
                raw_sym = mkt.get("symbol", "")
                if not raw_sym:
                    continue
                if mkt.get("market_type") != "perp":
                    continue
                if mkt.get("status") != "active":
                    continue
                base = self._normalize_symbol(raw_sym)
                self._markets[base] = mkt
                self._market_id_by_symbol[base] = int(mkt["market_id"])
            log(f"LighterClient: loaded {len(self._markets)} active perp markets")
        except Exception as e:
            crash_log("LighterClient._ensure_markets", e)

    def market_id(self, asset: str) -> Optional[int]:
        return self._market_id_by_symbol.get(asset.upper())

    def size_decimals(self, asset: str) -> int:
        mkt = self._markets.get(asset.upper(), {})
        return int(mkt.get("supported_size_decimals", 4))

    def price_decimals(self, asset: str) -> int:
        mkt = self._markets.get(asset.upper(), {})
        return int(mkt.get("supported_price_decimals", 2))

    def round_size(self, asset: str, size: float) -> float:
        return round(size, self.size_decimals(asset))

    def round_price(self, asset: str, price: float) -> float:
        return round(price, self.price_decimals(asset))

    # ── Public market data ────────────────────────────────────────────────────

    async def get_funding_rates(self) -> Dict[str, float]:
        """
        Lighter's own current funding rate per asset.
        Returns {asset: rate_per_hour_decimal}, e.g. {"BTC": 0.0003}.
        Positive = longs pay shorts (earns when short).
        """
        try:
            data = await self._get("/funding-rates")
            result: Dict[str, float] = {}
            for entry in data.get("funding_rates", []):
                if entry.get("exchange") != "lighter":
                    continue
                raw = entry.get("symbol", "")
                base = self._normalize_symbol(raw)
                if base:
                    result[base] = float(entry.get("rate", 0))
            return result
        except Exception as e:
            crash_log("LighterClient.get_funding_rates", e)
            return {}

    async def get_all_exchange_funding_rates(self) -> Dict[str, Dict[str, float]]:
        """
        Funding rates from ALL exchanges in Lighter's oracle in one API call.
        Returns {exchange: {asset: rate_per_hour}, ...}
        Exchanges present: "binance", "bybit", "hyperliquid", "lighter".

        This is the primary data source for the SpreadScanner — avoids a
        separate HL API call because Lighter already aggregates all rates.
        """
        try:
            data = await self._get("/funding-rates")
            result: Dict[str, Dict[str, float]] = {}
            for entry in data.get("funding_rates", []):
                exchange = entry.get("exchange", "")
                raw = entry.get("symbol", "")
                base = self._normalize_symbol(raw)
                rate = float(entry.get("rate", 0))
                if exchange and base:
                    result.setdefault(exchange, {})[base] = rate
            return result
        except Exception as e:
            crash_log("LighterClient.get_all_exchange_funding_rates", e)
            return {}

    async def get_order_book(self, asset: str, depth: int = 5) -> dict:
        """Return {"bids": [...], "asks": [...]} for a Lighter perp market."""
        try:
            await self._ensure_markets()
            mid = self.market_id(asset)
            if mid is None:
                log_warn(f"LighterClient: unknown market {asset}")
                return {"bids": [], "asks": []}
            data = await self._get(
                "/orderBookOrders",
                params={"market_id": mid, "limit": min(depth, 250)},
            )
            return {
                "bids": data.get("bids", []),
                "asks": data.get("asks", []),
            }
        except Exception as e:
            crash_log(f"LighterClient.get_order_book.{asset}", e)
            return {"bids": [], "asks": []}

    async def get_best_prices(self, asset: str) -> tuple[float, float]:
        """Return (best_bid, best_ask) for asset perp. Returns (0, 0) on failure."""
        book = await self.get_order_book(asset, depth=1)
        bids, asks = book.get("bids", []), book.get("asks", [])
        bid = float(bids[0]["price"]) if bids else 0.0
        ask = float(asks[0]["price"]) if asks else 0.0
        return bid, ask

    # ── Account & position queries ────────────────────────────────────────────

    async def get_account(self) -> dict:
        """
        Fetch full account state including collateral, available_balance,
        margin requirements, and positions.
        """
        try:
            by = "index" if self._account_index is not None else "l1_address"
            value = (
                str(self._account_index)
                if self._account_index is not None
                else self._address
            )
            data = await self._get("/account", params={"by": by, "value": value})
            accounts = data.get("accounts", [])
            return accounts[0] if accounts else {}
        except Exception as e:
            crash_log("LighterClient.get_account", e)
            return {}

    async def get_positions(self) -> Dict[str, dict]:
        """
        Fetch open positions from Lighter explorer.
        Returns {market_index_str: {entry_price, pnl, side, size}, ...}
        """
        try:
            param = (
                str(self._account_index)
                if self._account_index is not None
                else self._address
            )
            data = await self._get_explorer(f"/accounts/{param}/positions")
            return data.get("positions", {})
        except Exception as e:
            crash_log("LighterClient.get_positions", e)
            return {}

    async def get_margin_ratio(self) -> float:
        """
        Maintenance margin / collateral ratio.
        0.0 = no risk, 1.0 = at liquidation boundary.
        """
        try:
            acct = await self.get_account()
            collateral = float(acct.get("collateral", 0) or 0)
            maint = float(acct.get("cross_maintenance_margin_requirement", 0) or 0)
            return (maint / collateral) if collateral > 0 else 0.0
        except Exception as e:
            crash_log("LighterClient.get_margin_ratio", e)
            return 0.0

    async def get_available_balance(self) -> float:
        """Return USDC available for new positions."""
        try:
            acct = await self.get_account()
            return float(acct.get("available_balance", 0) or 0)
        except Exception as e:
            crash_log("LighterClient.get_available_balance", e)
            return 0.0

    async def get_position_for_asset(self, asset: str) -> Optional[dict]:
        """
        Return the current perp position for asset, or None if no position.
        Matches by market_index from our market metadata cache.
        """
        try:
            await self._ensure_markets()
            mid = self.market_id(asset)
            if mid is None:
                return None
            positions = await self.get_positions()
            # positions keyed by market_index as string
            return positions.get(str(mid))
        except Exception as e:
            crash_log(f"LighterClient.get_position_for_asset.{asset}", e)
            return None

    # ── SDK initialisation ────────────────────────────────────────────────────

    def _init_signer(self) -> None:
        """Lazy-initialise the lighter SDK SignerClient (lighter-sdk package)."""
        if self._signer is not None:
            return
        if any(
            x is None
            for x in [self._account_index, self._api_key_index, self._api_private_key]
        ):
            raise ValueError(
                "Trading requires LIGHTER_ACCOUNT_INDEX, LIGHTER_API_KEY_INDEX, "
                "LIGHTER_API_PRIVATE_KEY in .env"
            )
        try:
            from lighter.signer_client import SignerClient  # type: ignore
        except ImportError:
            raise ImportError(
                "Lighter SDK not installed. Run:\n"
                "  python _fetch_sdk.py\n"
                "Or: uv pip install lighter-sdk (if PyPI accessible)"
            )
        # SignerClient.__init__ loads the DLL synchronously — safe to call here
        # Pass bare host URL; SDK appends /api/v1 internally
        self._signer = SignerClient(
            url=LIGHTER_SDK_URL,
            account_index=self._account_index,
            api_private_keys={self._api_key_index: self._api_private_key},
        )
        log("LighterClient: SignerClient initialised (lighter-sdk)")

    # ── Amount / price scaling ─────────────────────────────────────────────────

    def _scale_size(self, asset: str, size: float) -> int:
        """Convert human-readable size to Lighter raw integer (10^size_decimals)."""
        decimals = self.size_decimals(asset)
        return int(round(size * (10 ** decimals)))

    def _scale_price(self, asset: str, price: float) -> int:
        """Convert human-readable price to Lighter raw integer (10^price_decimals)."""
        decimals = self.price_decimals(asset)
        return int(round(price * (10 ** decimals)))

    def _next_client_order_index(self) -> int:
        """Monotonic unique order ID for client_order_index."""
        self._order_counter += 1
        return self._order_counter

    # ── Open order lookup for cancellation ────────────────────────────────────
    # NOTE: GET /orders requires auth we don't pass via plain REST — use SDK
    # cancel_all_orders instead (see cancel_order below).

    # ── Order placement ───────────────────────────────────────────────────────

    async def place_maker(
        self,
        asset: str,
        is_buy: bool,
        size: float,
        price: float,
        reduce_only: bool = False,
    ) -> Optional[str]:
        """
        Place a Post-Only (maker) limit order. 0% fee.
        Returns str(client_order_index) as order_id (used for cancel), or None.
        """
        self._init_signer()
        await self._ensure_markets()
        mkt = self._markets.get(asset.upper())
        if mkt is None:
            log_warn(f"LighterClient.place_maker: unknown market {asset}")
            return None
        try:
            sz_raw = self._scale_size(asset, self.round_size(asset, size))
            px_raw = self._scale_price(asset, self.round_price(asset, price))
            client_idx = self._next_client_order_index()

            _, resp, error = await self._signer.create_order(
                market_index=int(mkt["market_id"]),
                client_order_index=client_idx,
                base_amount=sz_raw,
                price=px_raw,
                is_ask=not is_buy,  # is_ask=True → sell/short; False → buy/long
                order_type=self._signer.ORDER_TYPE_LIMIT,
                time_in_force=self._signer.ORDER_TIME_IN_FORCE_POST_ONLY,
                reduce_only=reduce_only,
            )
            if error:
                log_warn(f"LighterClient.place_maker: {asset} SDK error: {error}")
                return None
            tx = resp.tx_hash if resp else ""
            log(
                f"LighterClient: {'BUY' if is_buy else 'SELL'} "
                f"{self.round_size(asset, size)} {asset} @ {self.round_price(asset, price)} "
                f"POST_ONLY client_idx={client_idx} tx={tx[:10] if tx else '?'}"
            )
            return str(client_idx)
        except Exception as e:
            crash_log(f"LighterClient.place_maker.{asset}", e)
            return None

    async def place_taker(
        self,
        asset: str,
        is_buy: bool,
        size: float,
        price: float,
        reduce_only: bool = False,
    ) -> Optional[str]:
        """
        Place an IOC (market) order. 0% taker fee on Lighter.
        Returns str(client_order_index) or None.
        """
        self._init_signer()
        await self._ensure_markets()
        mkt = self._markets.get(asset.upper())
        if mkt is None:
            log_warn(f"LighterClient.place_taker: unknown market {asset}")
            return None
        try:
            sz_raw = self._scale_size(asset, self.round_size(asset, size))
            px_raw = self._scale_price(asset, self.round_price(asset, price))
            client_idx = self._next_client_order_index()

            _, resp, error = await self._signer.create_order(
                market_index=int(mkt["market_id"]),
                client_order_index=client_idx,
                base_amount=sz_raw,
                price=px_raw,
                is_ask=not is_buy,
                order_type=self._signer.ORDER_TYPE_MARKET,
                time_in_force=self._signer.ORDER_TIME_IN_FORCE_IMMEDIATE_OR_CANCEL,
                order_expiry=self._signer.DEFAULT_IOC_EXPIRY,
                reduce_only=reduce_only,
            )
            if error:
                log_warn(f"LighterClient.place_taker: {asset} SDK error: {error}")
                return None
            tx = resp.tx_hash if resp else ""
            log(
                f"LighterClient: TAKER {'BUY' if is_buy else 'SELL'} "
                f"{self.round_size(asset, size)} {asset} @ {self.round_price(asset, price)} "
                f"reduce_only={reduce_only} tx={tx[:10] if tx else 'FAILED'}"
            )
            return str(client_idx)
        except Exception as e:
            crash_log(f"LighterClient.place_taker.{asset}", e)
            return None

    async def cancel_order(self, asset: str, order_id: str) -> bool:
        """
        Cancel all open orders via SDK cancel_all_orders (IMMEDIATE).

        Why cancel_all instead of cancel_order:
          GET /orders endpoint requires REST auth we don't pass, so we cannot
          look up the book-level order_index needed by cancel_order(order_index=...).
          cancel_all_orders is a single SDK tx that reliably purges all resting
          orders — safe for our bot because we run one position at a time.
        """
        self._init_signer()
        try:
            # CANCEL_ALL_TIF_IMMEDIATE = 0 — SDK ctypes binding requires an int, None crashes.
            # Previous comment claiming None was required was wrong (was a server-error misread).
            _, resp, error = await self._signer.cancel_all_orders(
                time_in_force=self._signer.CANCEL_ALL_TIF_IMMEDIATE,
                timestamp_ms=int(time.time() * 1000),
            )
            if error:
                log_warn(f"LighterClient.cancel_order: {asset} cancel_all error: {error}")
                return False
            log(f"LighterClient: cancel_all_orders OK (triggered by {asset} repost)")
            return True
        except Exception as e:
            crash_log(f"LighterClient.cancel_order.{asset}", e)
            return False

    # ── Maker-chase entry (mirrors HLClient.maker_chase_entry) ────────────────

    async def maker_chase_entry(
        self,
        asset: str,
        is_buy: bool,
        size: float,
        label: str = "",
        timeout_s: float = 30.0,
        max_reposts: int = 10,
        closing: bool = False,
        reduce_only: bool = False,
    ) -> Optional[str]:
        """
        Rest a post-only limit order at best bid/ask. If not filled after
        repost_delay, cancel and repost at new touch price. Up to max_reposts.
        Returns client_order_index str on fill, or None if timed out.

        Lighter 0% fee means reposts are truly free — can chase aggressively.

        closing=True   → we're CLOSING a position. Fill detection compares against
                         the position size BEFORE this chase started, looking for a
                         drop of >= 90% of `size`. The old logic (size_on_exch >=
                         size*0.9) would always trigger immediately on close because
                         the open position already satisfies the threshold, returning
                         "FILLED" while leaving the close order resting → orphan.
        reduce_only=True → passes reduce_only to place_maker. Safe to enable for
                           closing orders so any unintended fill cannot flip the
                           position direction.
        """
        await self._ensure_markets()
        start = time.monotonic()
        repost_delay = timeout_s / max(max_reposts, 1)
        label_str = f"[{label}] " if label else ""

        # Snapshot size BEFORE we place anything — needed for close-fill detection.
        try:
            _pre_pos = await self.get_position_for_asset(asset)
            pre_size = abs(float(
                (_pre_pos or {}).get("size", 0) or (_pre_pos or {}).get("base_amount", 0) or 0
            ))
        except Exception:
            pre_size = 0.0

        for attempt in range(max_reposts):
            elapsed = time.monotonic() - start
            if elapsed >= timeout_s:
                log_warn(f"LighterClient.maker_chase: {label_str}{asset} timed out after {elapsed:.0f}s")
                # Best-effort cancel of any remaining resting order before returning.
                try:
                    await self.cancel_order(asset, "timeout_cleanup")
                except Exception:
                    pass
                return None

            bid, ask = await self.get_best_prices(asset)
            if bid <= 0 or ask <= 0:
                log_warn(f"LighterClient.maker_chase: {label_str}{asset} no book data")
                await asyncio.sleep(2.0)
                continue

            price = ask if is_buy else bid
            order_id = await self.place_maker(asset, is_buy, size, price, reduce_only=reduce_only)
            if order_id is None:
                log_warn(f"LighterClient.maker_chase: {label_str}{asset} place_maker failed (attempt {attempt+1})")
                await asyncio.sleep(2.0)
                continue

            log(f"LighterClient.maker_chase: {label_str}{asset} attempt {attempt+1} @ {price} client_idx={order_id}")

            await asyncio.sleep(repost_delay)

            # Check fill by position size change
            pos = await self.get_position_for_asset(asset)
            if pos is not None:
                size_on_exch = abs(float(pos.get("size", 0) or pos.get("base_amount", 0)))
                if closing:
                    # Closing: pre_size should drop by at least 90% of intended close `size`
                    closed_qty = pre_size - size_on_exch
                    if closed_qty >= size * 0.9:
                        log(
                            f"LighterClient.maker_chase: {label_str}{asset} CLOSE FILLED @ {price} "
                            f"(pre={pre_size:.6f} → now={size_on_exch:.6f}, closed={closed_qty:.6f})"
                        )
                        return order_id
                else:
                    # Opening: position size grows from 0 (or pre_size) to >= target
                    grown = size_on_exch - pre_size
                    if grown >= size * 0.9:
                        log(f"LighterClient.maker_chase: {label_str}{asset} FILLED @ {price}")
                        return order_id

            # Not filled → cancel and try again at new price.
            # IMPORTANT: if cancel fails, abort immediately — continuing would place
            # a new order on top of the unfilled one, accumulating orphaned orders.
            cancelled = await self.cancel_order(asset, order_id)
            if not cancelled:
                log_warn(
                    f"LighterClient.maker_chase: {label_str}{asset} "
                    f"cancel failed at attempt {attempt+1} — aborting to prevent order accumulation"
                )
                return None

        log_warn(f"LighterClient.maker_chase: {label_str}{asset} exhausted {max_reposts} reposts")
        # Final cleanup cancel — make sure nothing lingers on the book.
        try:
            await self.cancel_order(asset, "exhausted_cleanup")
        except Exception:
            pass
        return None
