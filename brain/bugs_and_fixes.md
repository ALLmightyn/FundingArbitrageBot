---
Type: Bug Graveyard (append-only)
Updated: 2026-05-04
---

# Bug Graveyard — HLCarryBot

Format: **Error → Cause → Fix → File**

---

**BUG-001** `RuntimeError: threads can only be started once` at first DB query
- Cause: `await` before `aiosqlite.connect()` — it's a context manager, not a coroutine
- Fix: `get_db()` returns `aiosqlite.connect(path)` directly; callers: `async with get_db() as conn:`
- File: `database/db.py`

**BUG-002** `float() argument must be a string or real number, not 'NoneType'`
- Cause: testnet assetCtx fields (markPx, oraclePx, funding) can be None
- Fix: `_f(v, default=0.0)` helper + skip assets where mark=0 AND oracle=0
- File: `venues/hyperliquid.py:get_funding_rates()`

**BUG-003** `'Info' object has no attribute 'predicted_fundings'`
- Cause: SDK v0.23 doesn't expose this method
- Fix: raw POST — `self._info.post("/info", {"type": "predictedFundings"})`
- File: `venues/hyperliquid.py:get_predicted_fundings()`

**BUG-004** `FundingScanner: ranked=[]` despite BTC 0.0234%/h
- Cause: `min_rate = ENTRY_THRESHOLD + FEE_ROUND_TRIP/8` was too high (0.02375% > 0.0234%)
- Fix: scanner gates only on `FUNDING_ENTRY_THRESHOLD`; fee amortization in cycle close
- File: `feeds/funding_scanner.py:scan_once()`

**BUG-005** `ValueError: float_to_wire causes rounding`
- Cause: size has more decimals than szDecimals for asset
- Fix: `round_size(asset, size)` using cached szDecimals; call in `place_maker/taker/maker_chase_entry`
- File: `venues/hyperliquid.py`

**BUG-006** `predicted_map` empty, all predicts fallback to current rate
- Cause: expected list of dicts, got `[[asset, [[venue, {fundingRate}], ...]], ...]`
- Fix: rewritten parser — find `venue_name == "HlPerp"` in nested list
- File: `feeds/funding_scanner.py:scan_once()`

**BUG-007** Infinite retry loop after failed entry (every 30s)
- Cause: no cooldown after timeout; `del self.positions[asset]` allows retry on next tick
- Fix: `_entry_cooldowns: Dict[str, int]` — set `now + EXIT_COOLDOWN_SECONDS` after any failed entry
- File: `strategies/funding_carry.py`

**BUG-008** `IronDome: perp cover FAILED: Order has invalid price`
- Cause: legging cover tried to buy spot using perp L2 book (wrong venue)
- Fix: `filled_leg="perp"` → `reduce_only=True` taker on perp only; spot routing is separate
- File: `risk/iron_dome.py:cover_naked_leg()`

**BUG-009** `Price must be divisible by tick size. asset=3` (BTC)
- Cause: `round(ask_px, 1)` gives 79337.3 for BTC but tick=$1 (0 decimals)
- Fix: `HLClient.round_price(px)` → `round(px, max(0, 5 - len(str(int(px)))))` — apply everywhere prices are built
- File: `venues/hyperliquid.py` + `risk/iron_dome.py`

**BUG-010** WS reconnects every 5s (storm, no backoff)
- Cause: no exponential backoff in `ws_listen()`
- Fix: backoff 5→60s, doubles on error, resets to 5 on successful connect
- File: `venues/hyperliquid.py:ws_listen()`

**BUG-011** Spot leg went through perp market (not real spot)
- Symptom: only perp positions visible on UI; funding income ≈ 0 (perp long + perp short cancel out)
- Cause: `exchange.order("BTC", True, ...)` = perp LONG, not spot buy
- Fix: `_ensure_spot_meta()` builds perp→spot mapping (BTC→@142 mainnet, @50 testnet); `place_maker/taker` get `is_spot=True` flag; strategy uses `is_spot=True` for spot leg sized in base-asset units
- File: `venues/hyperliquid.py`, `strategies/funding_carry.py`, `core/models.py`, `risk/iron_dome.py`

**BUG-012** Exit fees not counted in PnL (overstated by ~$0.165/cycle)
- Cause: `pos.fee_paid_usd` only updated at entry; `_finalize_close` used accumulated value without adding exit costs
- Fix: `_close_position` adds `exit_perp_fee + exit_spot_fee` to `pos.fee_paid_usd`; uses maker vs taker fee based on `was_taker` flag
- File: `strategies/funding_carry.py:_close_position`

**BUG-013** No state recovery after restart (orphan positions)
- Fix: `_recover_state()` in `main.py` before tick loop — reads clearinghouseState + spot balances + DB OPEN cycles; reconstructs CarryPosition in HOLD; cycles without on-chain position → ABANDONED
- File: `main.py:_recover_state()`

**BUG-014** MakerChase phantom fills — race condition on cancel
- Symptom: bot thinks order unfilled after cancel; exchange actually filled it → naked perp or double entry
- Cause: `maker_chase_entry` checked open_orders only, not actual position size
- Fix: snapshot `_pre_size` before chase via `get_perp_position_size()`; after each cancel if `|post_sz - pre_sz| > size * 0.5` → phantom fill, return order_id
- File: `venues/hyperliquid.py`

**BUG-015** Naked short — spot leg timeout → unnecessary cover_naked_leg
- Cause: spot maker timeout (90s) went straight to perp close; losing trade + paying taker on both legs
- Fix: `SPOT_TAKER_FALLBACK_S=20s`; on timeout try `place_taker(spot_pair, ask*1.005, is_spot=True)` first; only if taker also FAILED → `cover_naked_leg`
- File: `strategies/funding_carry.py`, `core/constants.py`

**BUG-016** Position layering after restart (orphan exchange positions)
- Cause: `_recover_state` went DB→exchange but not exchange→DB; positions without DB cycle got new entries stacked on top
- Fix: `reconcile_with_exchange()` after `_recover_state()` — for each exchange position not in `strategy.positions` → emergency taker close + Telegram alert
- File: `main.py`

**BUG-017** WS userFundings replay on reconnect → duplicate payment records
- Fix: 3-layer dedup: (1) in-memory `_seen_fundings` set by `(asset, paid_at_ms)`; (2) `UNIQUE INDEX uq_payments_asset_time` + `INSERT OR IGNORE`; (3) temporal guard — ignore events where `paid_at < pos.entered_at * 1000`
- File: `strategies/funding_carry.py`, `schema.sql`

**BUG-019** `emergency_margin` fires immediately after position open → closes after 30 min (fee loss)
- Cause: no state recovery on `CrossVenueStrategy.__init__`. Restart during open position → new session opens duplicate position on top of orphan → `totalMarginUsed` doubles → `margin_used/usdc_balance` spikes above 65% threshold immediately → `_EMERGENCY_CLOSE_AFTER_S=1800s` timer runs to completion.
- Fix: `CrossVenueStrategy.recover_open_positions()` — reads OPEN DB cycles, checks exchange (HL `get_perp_position_size` + Lighter `get_position_for_asset`), restores to HOLD or marks ABANDONED/partial-close. Called in `main_cross.py` after strategy init, before tick loop.
- File: `strategies/cross_venue_carry.py:recover_open_positions`, `main_cross.py`

**BUG-018** `ArgumentError: argument 1: TypeError: 'NoneType' object cannot be interpreted as an integer` in `cancel_order`
- Cause: `cancel_all_orders(time_in_force=None)` — the SDK's `sign_cancel_all_orders` passes the value directly to a ctypes C function `SignCancelAllOrders`, which requires an integer. `None` crashes ctypes at argument 1.
- Fix: `time_in_force=self._signer.CANCEL_ALL_TIF_IMMEDIATE` (value `0`). Old comment claiming `None` was required was a server-error misread.
- File: `venues/lighter.py:cancel_order`

---

## TESTNET KNOWN LIMITATIONS

| Asset | Perp funding | Spot pair | Spot liquidity | Tradable? |
|-------|-------------|-----------|----------------|-----------|
| BTC   | 0.030%/h ✓  | @50       | empty book ❌  | NO |
| ETH   | 0.027%/h ✓  | @1137     | spread 888% ❌ | NO |
| SOL   | varies      | @1165     | spread 152% ❌ | NO |
| HYPE  | 0%/h ❌     | @1035     | spread 2% ✓   | NO (no funding) |

Full e2e testing requires mainnet. On testnet bot correctly skips all assets and idles.
