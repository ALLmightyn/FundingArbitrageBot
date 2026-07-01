---
Type: Bug Graveyard (append-only)
Updated: 2026-05-30
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

**BUG-034** Lighter funding accounting завышал в ~3.3× (rate×time vs real settlements)
- Symptom: NEAR 2h позиция — бот показал Lighter funding $+0.0051; реально биржа кредитнула $+0.001529. APR заявлен 507%, реальный ~16%.
- Cause: `poll_lighter_funding` использовал `instant_rate × elapsed × notional`. instant_rate из /funding-rates это predicted (current premium/8), не settlement. Settlement = TWAP 60 минутных премиумов за час. Instant ~3× больше TWAP при stable rate, ~10-35× при spikes.
- Fix: новый `LighterClient.get_position_fundings(asset, since_ts)` — реальные settled events через authenticated `/positionFunding` API (lighter.AccountApi). poll_lighter_funding теперь читает реальные суммы. Idempotency: `pos.lighter_last_funding_id` хранит max обработанный funding_id, фильтр `fid > last_seen` гарантирует no double-credit. DB: новая колонка `lighter_last_funding_id`.
- Recovery safety: при recover_open_positions если `lighter_last_funding_id==0 && lighter_funding_collected!=0` — данные из старого rate×time, сбрасываем и перенабираем через API (одноразово после миграции).
- Files: `venues/lighter.py:get_position_fundings`, `strategies/cross_venue_carry.py:poll_lighter_funding/recover_open_positions`, `core/models.py`, `database/schema.sql`

**BUG-035** Exit logic сравнивает instant spread (а не TWAP) → ложные удержания при spike
- Symptom: позиция NEAR держалась несмотря на реальный спред 0.001%/h (settlement TWAP) — бот видел instant 0.05%/h (spike) и SOFT_EXIT не срабатывал.
- Cause: `_manage_hold` использовал `snap.spread` (instant). Exit thresholds сравнивались с зашумлённым значением — то срабатывали на ложный спайк вниз, то наоборот не срабатывали при реально низком settlement.
- Fix: используем `scanner.get_twap_spread()` для exit decisions. instant остается для информации в логах. Fallback на instant если TWAP не валиден (warmup).
- File: `strategies/cross_venue_carry.py:_manage_hold`

**BUG-033** 1000PEPE "Order has invalid size" на HL
- Symptom: все 10 попыток HL maker для 1000PEPE → "Order has invalid size", entry abort
- Cause: `_get_sz_decimals(asset)` кэш заполняется HL именами (`kPEPE`), но вызов приходит с Lighter canonical (`1000PEPE`). `_sz_decimals.get("1000PEPE", 4)` → fallback 4 decimal places. kPEPE на HL имеет szDecimals=0. Размер `2083.3333` с 4 знаками → HL reject.
- Fix: `_get_sz_decimals` теперь переводит `asset` через `self._hl(asset)` (1000PEPE → kPEPE) перед lookup в кэше.
- File: `venues/hyperliquid.py:_get_sz_decimals`

**BUG-032** Lighter maker_chase repost_delay < ZK settlement time → ghost orders
- Symptom: каждая попытка Lighter POST_ONLY заканчивается "no resting order found (already filled)" + position 0. 5 попыток, timeout, HL нога закрыта.
- Cause: `repost_delay = timeout_s / max_reposts = 30/10 = 3.0s`. ZK rollup Lighter требует ~3-4с на settlement. Мы проверяем position за 3s до settlement → position=0. cancel возвращает None (ордер в mempool, не виден в book). Цикл идёт дальше не зная что ордер валиден.
- Fix: `repost_delay = max(5.0, timeout_s / max_reposts)`. Гарантирует 5s между place и check — достаточно для ZK settlement.
- File: `venues/lighter.py:maker_chase_entry`

**BUG-031** Transient funding spike → entry → settlement 35× ниже ожидаемого (XLM)
- Symptom: вход XLM при 0.0384%/h (336% APR); реальный settlement 23:00 → 0.0012%/h (Lighter), 0.0001%/h (HL). Net spread 0.0011%/h vs ожидаемых 0.0384%/h = 35× разрыв.
- Cause: Lighter `/funding-rates` возвращает instantaneous predicted rate (current_premium / 8). Реальный settlement = TWAP 60 минутных премиумов за час. 3-мин спайк до 0.04%/h + 57 мин у 0.001%/h → TWAP settlement ≈ 0.003%/h. Spike-filter (z-score) не поймал: z-score был в норме после рестарта с чистой историей.
- Fix: TWAP confirmation gate — `get_twap_spread(asset, window_s=900)` в `SpreadScanner`. Перед входом требуем 15-мин rolling mean spread ≥ SPREAD_ENTRY_THRESHOLD. Хранилище _spread_history переведено в `_rate_history: Deque[(ts, hl_rate, lt_rate)]` для точного временного окна. Warmup: 10 семплов (~5 мин). Fail-closed: если <10 семплов в окне — skip без кулдауна.
- Files: `feeds/spread_scanner.py`, `strategies/cross_venue_carry.py`, `core/constants.py`

**BUG-030** Две монеты открываются за один тик
- Symptom: открылись WLD (15:07) и TAO (15:09) в одном tick-запуске
- Cause: `open_count` вычислялся один раз в начале `tick()`. Затем `for snap in candidates` делал `await _enter_position()` для всех `n = MAX - open_count` кандидатов без перепроверки. При open_count=0 и MAX=3 → 3 позиции за один тик.
- Fix: после каждого `_enter_position` пересчитываем `new_count`; если `new_count > open_count` → `break`. Максимум 1 новая позиция за тик. Следующий тик при необходимости откроет ещё одну.
- File: `strategies/cross_venue_carry.py:tick`

**BUG-029** Bot re-enters WLD (or any asset) after restart despite open position existing on exchange
- Symptom: Already-open WLD position; bot restarts → `recover_open_positions()` runs → immediately re-enters WLD → doubled margin → emergency close
- Cause: `recover_open_positions()` calls `self._lt.get_position_for_asset(asset)` which internally calls `get_positions()`. `get_positions()` catches all exceptions and returns `{}` — indistinguishable from "no positions". So if Lighter API fails at startup, `lt_size = 0.0` (not `-1.0` = unknown). Logic: `hl_ok=True, lt_ok=False, lt_unk=False` → **PARTIAL branch** → closes HL leg taker + marks DB ABANDONED. WLD evicted from `self.positions` → scanner picks it up → new entry.
- Fix: Added `LighterClient.get_position_size_or_raise(asset)` — bypasses all internal exception catching, raises on API error. `recover_open_positions()` now uses this method; outer `except` correctly sets `lt_size = -1.0` (unknown) → `lt_unk=True` → recovery skips PARTIAL close and logs warning instead.
- File: `venues/lighter.py:get_position_size_or_raise`, `strategies/cross_venue_carry.py:recover_open_positions`

**BUG-019** `emergency_margin` fires immediately after position open → closes after 30 min (fee loss)
- Cause: no state recovery on `CrossVenueStrategy.__init__`. Restart during open position → new session opens duplicate position on top of orphan → `totalMarginUsed` doubles → `margin_used/usdc_balance` spikes above 65% threshold immediately → `_EMERGENCY_CLOSE_AFTER_S=1800s` timer runs to completion.
- Fix: `CrossVenueStrategy.recover_open_positions()` — reads OPEN DB cycles, checks exchange (HL `get_perp_position_size` + Lighter `get_position_for_asset`), restores to HOLD or marks ABANDONED/partial-close. Called in `main_cross.py` after strategy init, before tick loop.
- File: `strategies/cross_venue_carry.py:recover_open_positions`, `main_cross.py`

**BUG-022** Bot entered NEAR at funding spike (0.091%/h shown as ~800% APR); real APR was ~108%
- Symptom: Scanner consistently showed NEAR in top with ~800% APR. Entry at 23:05. First settlement (00:00) showed Lighter rate 0.0128%/h — rate had already normalized 7× within 1 hour.
- Cause: `is_spike()` returned `(False, "insufficient_history")` on cold start (0 samples < SPIKE_HISTORY_MIN_SAMPLES=12). **Fail-open** on warmup allowed entry into what was a transient spike.
- Reconstruction: spike required Lighter NEAR rate ~0.091%/h at entry. Normalized to 0.0128%/h by first settlement, 0.0119%/h by second.
- Fix: Changed fail-open to **fail-closed** on warmup — `is_spike()` returns `(True, "warming_up (N/12)")` until 12 samples accumulated (~12 min). Strategy logs "warming up" without setting cooldown (retries every tick). Added `warmup_complete(asset)` helper.
- File: `feeds/spread_scanner.py:is_spike`, `strategies/cross_venue_carry.py:_enter_position`

**BUG-021** Lighter position + resting order remained after "successful" `_close_lt_leg(maker)` — bot believed leg closed, exchange disagreed
- Symptom: cycle marked CLOSED in DB and Telegram; UI showed live Lighter short and open maker order
- Cause: `maker_chase_entry` was reused for both entry and exit, but its fill check `size_on_exch >= size*0.9` is only valid for ENTRY. On exit, the existing position size already satisfies the threshold → function returns "FILLED" on first attempt while the order rests on book → orphan
- Fix: (1) `closing=True` + `reduce_only=True` flags in `maker_chase_entry`; (2) snapshot `pre_size` before placing anything; (3) on closing path, require `closed_qty = pre_size - size_on_exch >= size*0.9`; (4) cleanup cancel on timeout/exhaust paths; (5) post-close read-back verification in `_close_lt_leg` with taker fallback if `remaining > units * 10%`
- File: `venues/lighter.py:maker_chase_entry`, `strategies/cross_venue_carry.py:_close_lt_leg`

**BUG-020** HL funding accounted with WRONG sign in `poll_lighter_funding` (REST path)
- Symptom: For a NEAR Long paying funding (rate +0.00125%/h), bot booked `hl_funding_collected = +$0.0006` instead of `-$0.0006`
- Cause: HL `cumFunding.sinceOpen` uses **cost convention** (positive = funding paid out); WS `userFundings.usdc` and our `hl_funding_collected` use **P&L convention** (positive = earned). Old code did `hl_funding_collected = since_open` — both flipped sign AND overwrote correct WS events
- Verification: `info.post('/info', {'type':'userFunding'})` for our NEAR Long returned `usdc=-0.000307` (paid), confirming WS sign convention is P&L; `cumFunding.sinceOpen` would have been `+0.000307`
- Fix: `earned_total = -since_open` before assigning
- File: `strategies/cross_venue_carry.py:poll_lighter_funding`

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

---

**BUG-036** Lighter maker orders "ghost" (no resting order / no fill) on liquid assets (BCH/NEAR/TAO) — root cause of all legging-cover fee drain.
- Cause: `LighterClient.maker_chase_entry` placed the POST_ONLY at the WRONG side — `price = ask if is_buy else bid`. A buy at the ask (or sell at the bid) is marketable → POST_ONLY killed on-chain → order never rests → position never grows → bot reads "no resting order (already filled)", times out, covers HL leg taker (~$0.13 loss). Intermittent success only when price moved during ZK settlement lag.
- Fix: `price = bid if is_buy else ask` (passive side). Matches HL convention (hyperliquid.py:419/429 buy→bid, sell→ask). Lighter maker now rests reliably at 0% fee.
- Also added: (1) HL taker fallback in `_enter_position` if HL maker chase times out (+0.03% fee, recouped in ~1-2h at ~100% APR); (2) unified `_register_entry_fail` streak — ANY entry failure (HL timeout OR Lighter ghost) counts; 3 in a row → 4h blacklist (was Lighter-only, so alternating failures never tripped it).
- Files: `venues/lighter.py:maker_chase_entry`, `strategies/cross_venue_carry.py:_enter_position` + `_register_entry_fail`

---

**BUG-038** HL funding severely undercounted — `cumFunding.sinceOpen` resets on every HL leg close/reopen (legging covers, recovery after restart), causing chronic undercount.
- Cause: REST poll used `cumFunding.sinceOpen` which HL resets each time the perp position is closed and reopened. During chaotic sessions (multiple ghost/legging events + restarts), `sinceOpen` was overwritten to a smaller value and the code did `pos.hl_funding_collected = earned_total` (SET, not ADD), losing all prior accumulated funding.
- Fix: Replaced with `userFunding` REST endpoint (`{"type":"userFunding","user":addr,"startTime":since_ms}`). Returns actual USD amounts like Lighter's positionFunding. Idempotent via `hl_last_funding_time_ms` (ms timestamp of last credited event, persisted to DB). Matches exact figures shown in HL UI.
- Files: `venues/hyperliquid.py:get_user_funding_history`, `strategies/cross_venue_carry.py:poll_lighter_funding`, `core/models.py`, `database/schema.sql`

---

**BUG-039** Recovery Lighter orphan close: SELL instead of BUY when closing Lighter short orphan — doubled the short position.
- Cause: `lt_is_buy = (long_v == "lighter")` in partial-recovery close logic. For short_venue="lighter" this evaluates to False → places SELL (adds to short) instead of BUY (closes short).
- Fix: `lt_is_buy = (short_v == "lighter")` — if Lighter was the short leg, we BUY to close; if Lighter was the long leg, we SELL to close.
- File: `strategies/cross_venue_carry.py:recover_open_positions` partial-close block

**BUG-040** Lighter-нога 90s maker-only → timeout → HL cover тейкером → 4h blacklist лучшего актива.
- Cause: `LT_ENTRY_TIMEOUT_S=90s` давал 9 maker-репостов без taker-fallback. BCH: каждый раз заполняла HL-нога, Lighter зависала (тонкий ask), через 100s → cover HL тейкером ($0.003 loss) + `_register_entry_fail`. 3 фейла → 4h blacklist (01:32:55). Потеряли всё вечернее окно BCH (hits 19/24, 0.0079%/h).
- Fix: `LT_ENTRY_MAKER_TIMEOUT_S=15s` + немедленный taker IOC fallback. Lighter taker = 0% fee → нет пенальти. 2 ZK-settle check (5s+5s) перед подтверждением fill.
- File: `strategies/cross_venue_carry.py:_enter_position` Step 2; `core/constants.py:LT_ENTRY_MAKER_TIMEOUT_S`

**BUG-041** ZK-lag orphan: `maker_chase` timeout → cover HL → Lighter order приземляется через 3-4s → Lighter short без HL-хеджа (PARTIAL HL=0.029 LT=0.083 при рестарте 00:44).
- Cause: `maker_chase_entry` возвращает None после timeout; стратегия сразу вызывает `_cover_naked_hl_leg`, но ZK-rollup ещё не смаркировал fill в state. Recovery правильно закрывал orphan, но это delta-риск + лишние fees.
- Fix: после всех maker/taker попыток — 6s final ZK-lag guard: проверяем `get_position_for_asset`. Если size ≥ 90% target → `zk_lag_fill`, НЕ крываем HL.
- File: `strategies/cross_venue_carry.py:_enter_position` ZK-lag guard block (после lt_order_id is None)

**BUG-042** HL funding завышен ~14× в БД (APT: real $0.0113 хранилось как $0.157) → ложный APR 126% вместо реального ~14-20%.
- Cause: WS-путь `record_hl_funding()` делал blind `pos.hl_funding_collected += amount` без идемпотентности и НЕ двигал `hl_last_funding_time_ms`. HL шлёт полную историю funding (`isSnapshot`) на КАЖДОМ (ре)коннекте WS → каждый из ~13 реконнектов (6 рестартов pm2 + авто-reconnect) пере-добавлял всю историю. Плюс REST-поллер видел те же события «новыми» (анкер не двигался) и добавлял ещё раз. Тот же класс, что BUG-017 (HL-only), но не был портирован в cross.
- Fix: (1) `record_hl_funding(asset, amount, time_ms)` — идемпотентен по общему с REST анкеру `hl_last_funding_time_ms`: кредитует только события строго новее последнего, двигает анкер; события без time_ms дропает (REST = ground truth). (2) `main_cross.py:_on_ws_message` — skip `isSnapshot` фреймов + прокидывает `f["time"]` в record_hl_funding.
- Recovery: DB открытой APT исправлена 0.157→0.011271 (real userFunding sum, 46 событий, совпало с cumFunding.sinceOpen), anchor=1780401600006. После рестарта+WS-реконнекта funding остался $0.0252, не раздулся.
- File: `strategies/cross_venue_carry.py:record_hl_funding`; `main_cross.py:_on_ws_message`

`ВАЖНО для аудитов:` displayed/DB funding до 2026-06-02 16:00 завышен этим багом. Lighter accounting корректен (positionFunding API). Реальный P&L закрытых циклов считать по userFunding REST, не по DB hl_funding_collected.

**BUG-043** Невидимые орфаны: рассинхрон БД↔биржа оставлял голые ноги на бирже, которые drift-детектор не видел (он итерирует только self.positions; ABANDONED/CLOSED цикл уходит из трекинга → остаток на бирже невидим). Юзер закрывал руками. Источники: (1) resting close-ордер исполняется ПОСЛЕ recovery-снимка (поймали вживую 2026-06-02: HL maker close висел → рестарт → recovery снял HL=27.14 до исполнения → Lighter остался голый); (2) дубль-вход (2× ADA за 2 мин при max=1); (3) DB OPEN без exchange position (ghost).
- Fix P0 — `reconcile_orphans()` (cross_venue_carry.py): sweep ВСЕХ позиций обеих бирж как source of truth; любая ненулевая позиция вне tracked-цикла (ENTERING/HOLD/EXITING) → flatten reduce-only taker. Gated до конца recovery; 2-страйк дебаунс против транзиентных API-чтений; вызывается в main_cross после drift-check.
- Fix P1 — dup-guard в `_enter_position`: перед открытием проверка позиции на ОБЕИХ биржах, fail-closed; если есть остаток → abort + cooldown.
- Fix P2/P3 — `_cancel_all_resting_orders()` в начале `recover_open_positions` + sleep 1s: гасит висящие HL-ордера ДО снимка позиций. `_recovery_done` флаг гейтит sweep.
- Helpers: `_hl_all_positions` / `_lt_all_positions` → {asset: signed_size}.
- Validated 2026-06-02: на флэт-аккаунте sweep no-op (0 ложных); на симуляции APT-орфана (LT short -27.14) — страйк1 без ордера, страйк2 flatten reduce-only buy, tracked → skip.
- Files: `strategies/cross_venue_carry.py`, `main_cross.py`

## BUG-044 (FIXED) — restart с открытой позицией → грязный two-phase close (2026-06-03)
**Что:** pm2 restart при открытой WLD. Recovery нашёл OPEN cycle, **сам закрыл HL-ногу** maker'ом (SELL @0.5013, 16:45:07), затем re-check показал «PARTIAL HL=0 LT=62.4» и закрыл Lighter **taker reduce_only=False** @0.527. Лог выглядит как orphan, хотя обе ноги были на месте — recovery закрыл их в 2 этапа с ~8с разрывом.
**Последствие:** WLD force-closed (state=ABANDONED, recover_partial_close) вместо естественного выхода. HL-нога зафиксировала closedPnl=+$6.24, дельта-нейтраль сохранилась, equity ~break-even/+. Голой ноги/runaway НЕ было. Но ноги закрылись на расходящихся ценах (HL 0.5013 vs LT 0.527 = 5%) → риск проскальзывания на масштабе.
**Грабли:** материализовался риск «restart с открытой позицией» (уже видели db_ghost/recover_partial_close раньше). reduce_only=False на orphan-close — потенциально опасно (мог бы открыть, а не закрыть; спасло то что Lighter нетит позицию).
**КОРЕНЬ:** `main_cross.py` finally-блок закрывал ВСЕ позиции на shutdown (`reason="shutdown"`). pm2 SIGKILL ~1.6с после SIGTERM → handler успевал закрыть только HL-ногу maker'ом → реальный partial → recovery дочищал Lighter. Shutdown-close ДРАЛСЯ с RESTORE-логикой recovery. RESTORE 28 мая сработал только потому что тогда был жёсткий SIGKILL без finally.
**FIX (2026-06-03):**
1. `main_cross.py` finally: больше НЕ закрывает позиции на shutdown — дельта-нейтраль безопасна между рестартами, recovery её RESTORE'ит. Escape-hatch: env `FLATTEN_ON_SHUTDOWN=true` для принудительного flatten при полной остановке.
2. `cross_venue_carry.py` recovery orphan-close Lighter: добавлен `reduce_only=True` (был дефолт False → риск открыть вместо закрыть).
**Итог:** теперь pm2 restart с открытой позицией → позиция остаётся на биржах → recovery RESTORE to HOLD. Безопасно.

---

## WATCH-045 — price_divergence taker-выход = систематический слив (АУДИТ 2026-06-05)
**Статус:** ОТКРЫТ (кандидат на фикс, не баг исполнения — логика).
**Симптом:** 4 цикла закрыты по price_divergence, все net-отрицательны (-$0.040 суммарно). ZEC дважды за 27мин.
**Механика:** `cross_venue_carry.py` price_divergence: при |HL_mid − LT_mid| > 2% → немедленный taker-close ОБЕИХ ног.
**Почему это слив:** позиция delta-neutral (short одна биржа / long другая). Расхождение mid-цен между биржами НЕ ломает нейтральность — PnL ног движется в противофазе. Но бот фиксирует это taker'ом (~0.09% round-trip) в гарантированный fee-убыток. Значения (ZEC 339.39/332.27, WLD 0.5246/0.5388, ICP 2.61/2.55) похожи на quote-noise (тонкий стакан / устаревший mid), а не реальный 2% базис.
**Варианты фикса (не применены — нужен дизайн-ребют с юзером):**
1. Поднять порог до 3-4% ИЛИ требовать N последовательных снапшотов (debounce от шума).
2. Не выходить если дельта-нейтраль цела (проверять обе ноги живы) — divergence сам по себе не риск ликвидации.
3. Если выходить — хотя бы HL maker (как сделали для spread_flip), не обе taker.
**Связано:** тонкий realized spread (см. log 2026-06-05 аудит) — на жирном карри fee не так больно; на 0.005%/h любой ранний taker-выход = минус.

## WATCH-046 — HL maker-chase бьётся в taker fallback (АУДИТ 2026-06-05)
23 taker fallback на входе. "Post only order would have immediately matched" (asset=222 и др.). HL entry уходит в taker 0.045% вместо maker 0.015% → утраивает entry fee. Maker-chase цена пересекает движущийся стакан. Нужно: либо менее агрессивный chase-offset, либо принять и считать в EV.

## WATCH-047 — Lighter size error на ZEC (АУДИТ 2026-06-05)
code=21706 "invalid order base or quote amount" / "Order has invalid size" — 12× ВСЕ на ZEC (цена ~339). szDecimals/round_size для ZEC неверный. Проверить кэш szDecimals для ZEC на Lighter.

## WATCH-048 — DB funding ≠ биржа: ADA orphan (СВЕРКА 2026-06-05)
**Найдено сверкой HL userFunding API против cross_venue_cycles.**
HL leg итог: **биржа +$0.06642 vs DB +$0.04727** → DB недосчитывает ~$0.019 (29%).
Совпадают точно: ICP, WLD, TRX, ZEC. Расходятся:
- **ADA: DB −$0.00774 vs биржа +$0.01209** (свинг $0.02). Причина: 31.05 были 2 дубль-цикла ADA (manual_close_ada_duplicate, закрыты 07:15), НО реальная HL-позиция szi=−105 осталась жить на бирже и копила +$0.0003/ч ~2 суток (до 06-02 16:00, рядом с рестартом). DB пометил ABANDONED → ~$0.013 фандинга НЕ записаны. Orphan позиция = невидимый для учёта фандинг.
- **NEAR: DB $0 vs биржа −$0.00062** — пропущен HL settlement.
**Текущий учёт ОК:** живой ADA-цикл (06-05) DB +$0.00023 = биржа (11:00+12:00). Баг исторический, от дубль/abandon инцидента, не от текущего кода userFunding.
**Вывод для отчётности:** нельзя доверять SUM(funding) из DB как истине — сверять с биржей. Реальный HL +$0.066, не +$0.047.
**Lighter leg (DB +$0.087) с биржей НЕ сверен** — нужен authenticated positionFunding API. TODO.

## WATCH-049 — ночной рестарт = unattended-upgrades (РАЗГАДАНО 2026-06-05)
**Вопрос:** бот сам перезапустился ночью 06-05 06:42, без деплоя и без краша. Почему?
**Причина (точно по journalctl + apt history):**
- `apt-daily-upgrade.timer` сработал 06:42:04 → `unattended-upgrade` обновил 13 пакетов (dovecot, mysql-server-8.0, liblzma5/xz-utils, libwww-perl, **python3-urllib3**).
- В ходе апгрейда systemd делал `daemon-reload` + каскад рестартов сервисов → в 06:42:30 **остановил pm2-root.service** ("Stopping PM2 process manager") → SIGTERM боту.
- BUG-044 handler отработал штатно: "Leaving 1 position open for recovery: ICP" (НЕ force-close). pm2 поднялся 06:42:39 → recovery **RESTORED ICP to HOLD**. Голой ноги не было.
- Машина НЕ ребутилась (uptime с 27.05). Это сервис-рестарт, не reboot.
**Повторяемость:** `apt-daily-upgrade.timer` фактически срабатывает каждую ночь (next: 06-06 ~06:12). Значит рестарт бота — РЕГУЛЯРНОЕ ночное событие, не разовое.
**Вывод:** BUG-044 фикс ровно для этого и нужен — теперь ночные апгрейды безопасны (позиция переживает, RESTORE). Раньше каждую ночь был бы force-close.
**Опции если мешает:** (1) ничего — фикс делает безопасно; (2) `systemctl disable --now apt-daily-upgrade.timer` чтобы убрать ночные апгрейды; (3) перевести бот на отдельный systemd-unit, не завязанный на pm2-root, чтобы апгрейды его не дёргали. python3-urllib3 обновился под системным питоном (бот на /usr/bin/python3) — перезапуск подхватил новую версию без ошибок.

---
**WATCH-045 / 046 — ADDRESSED 2026-06-05:** price_divergence теперь HL maker + LT taker, порог 2→4%. HL entry maker-окно 60s (timeout_s) против taker fallback. + flip-порог -0.005%, 4ч flip-reentry cooldown, entry threshold 0.008%. Деплой session=391fc1a7, ADA RESTORED live. Эффект мониторить по net следующих циклов. См. log 2026-06-05.

## BUG-050 — Lighter maker_chase double-fill (cancel-race) → 2× нога (2026-06-05)
**Симптом:** MON вошёл, дрейф-гард показал booked=1155.8 HL=1156 (0%) **LT=2311.6 (100%)** — Lighter-нога вдвое. Позиция НЕ дельта-нейтральна ~15 мин (1155 MON нехеджированного шорта), пока flip-выход не оставил orphan → reconcile flatten taker.
**Root cause (по логам 13:48):** maker_chase repost-ветка: attempt1 SELL 1155.8 → 5с → fill-check мимо (ZK ещё не оселся) → `cancel_order` вернул **"OK"** (НЕ None) хотя ордер уже исполнился (fill+cancel-ack в одном блоке ZK) → attempt2 долил ещё 1155.8 → 2311.6. Пост-проверка филла в `venues/lighter.py` стояла ТОЛЬКО в ветке `cancelled is None`, ветку `cancelled OK` не покрывала.
**Дыра №2:** `check_position_drift` реагировал только на НЕДОСТАЮЩУЮ ногу (<50%=голая), избыточную (200%) только варнил, не выправлял.
**FIX (2026-06-05):**
1. `venues/lighter.py` maker_chase: пост-cancel проверка филла теперь ВСЕГДА (не только при cancelled is None) — wait 2s ZK settle + сравнить дельту позиции → если налилось, return FILLED (не репостить). Закрывает cancel-OK-but-filled гонку.
2. `cross_venue_carry.py` check_position_drift: если Lighter-нога > booked на >DRIFT_TOLERANCE_PCT → reduce-only taker тримит excess немедленно (reduce_only не может флипнуть). Defense-in-depth.
**Связано:** [[WATCH-047]] (ZK size errors), семейство BUG-036 (Lighter maker ghost).
**Статус:** задеплоено session TBD. Мониторить: больше нет "DRIFT ... 100%" без немедленного trim.
