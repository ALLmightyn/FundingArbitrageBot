# log.md — Дневник изменений HLCarryBot

Формат: `YYYY-MM-DD HH:MM | ФАЙЛ | ЧТО + ПОЧЕМУ`

---

## 2026-05-28

`2026-05-28 15:35 | strategies/cross_venue_carry.py | BUGFIX BUG-020 — HL funding учитывался с обратным знаком в poll_hl_funding_rest. HL cumFunding.sinceOpen использует COST convention (positive = paid), а наш hl_funding_collected — P&L convention (positive = earned). Старый код: hl_funding_collected = since_open → знак инвертирован И WS-события (которые были корректные) перезаписывались. Подтверждение: NEAR Long, реальный userFunding usdc=-0.000307 (платили), но cumFunding.sinceOpen вернул бы +0.000307. Fix: earned_total = -since_open.`

`2026-05-28 15:40 | venues/lighter.py + strategies/cross_venue_carry.py | BUGFIX BUG-021 — Lighter позиция и ордер оставались "живыми" после "успешного" close_lt_leg(maker). Root cause: maker_chase_entry проверял "filled?" через size_on_exch >= size*0.9 — на закрытии существующая позиция мгновенно удовлетворяет условие, функция возвращает order_id, бот считает что закрыл, реально ордер просто висит. Fix: (1) добавлены параметры closing+reduce_only в maker_chase_entry; (2) snapshot pre_size до первого размещения; (3) при closing — fill условие "closed_qty = pre_size - size_on_exch >= size*0.9"; (4) на timeout/exhaust — best-effort cleanup cancel; (5) в _close_lt_leg после успешного maker close — read-back verification и fallback на taker если remaining > 10%.`

## 2026-05-27

`2026-05-27 23:50 | strategies/cross_venue_carry.py | ЧЕСТНЫЙ P&L — 3 улучшения точности учёта: (1) Lighter funding: заменён account-level collateral delta (сломан при >1 позиций) на rate×time×notional per position — _lt_last_funding_poll[asset] таймер, poll_lighter_funding теперь начисляет sign×rate×notional_usd×elapsed_hours независимо по каждому асету; (2) Фактическая цена входа HL: сразу после fill читаем entryPx из get_clearinghouse() (не mark_price) — логируем slippage%; (3) Фактическая цена входа Lighter: get_position_for_asset().entry_price после fill (не bid/ask mid) — логируем slippage%. Cleanup: _lt_last_funding_poll.pop + _lt_collateral_at_entry.pop при закрытии позиции.`

`2026-05-27 23:10 | strategies/cross_venue_carry.py + main_cross.py | BUGFIX BUG-019 — emergency_margin fires after every restart with open positions. Root cause: no state recovery → position layering → doubled totalMarginUsed → ratio>65% immediately. Added CrossVenueStrategy.recover_open_positions(): reads OPEN DB cycles, checks exchange legs, restores HOLD or marks ABANDONED. Called in main_cross.py after strategy init.`

`2026-05-27 22:43 | venues/lighter.py | BUGFIX BUG-018 — cancel_all_orders(time_in_force=None) → ctypes ArgumentError. SignCancelAllOrders C binding requires int; changed to CANCEL_ALL_TIF_IMMEDIATE=0.`

`2026-05-27 17:30 | strategies/cross_venue_carry.py + feeds/spread_scanner.py + core/constants.py + main_cross.py | 4 улучшения после сравнения с Gajesh2007/funding-arb-bot: (1) sigma-фильтр на вход — отбрасывает funding spikes >2σ от 24ч среднего; (2) price divergence guard — taker-exit если HL/Lighter mid разъехались >2%; (3) portfolio drawdown kill switch — стоп новых входов при session P&L < -$8 (отдельно от CB2 SESSION_LOSS_FLOOR=-$15 в main); (4) periodic drift check каждые 5 мин — реконсилиация booked vs actual size, аварийное закрытие если одна нога <50% ожидаемого размера. Новые константы: SPIKE_SIGMA_THRESHOLD=2.0, SPIKE_HISTORY_MIN_SAMPLES=12, PRICE_DIVERGENCE_KILL_PCT=0.02, PORTFOLIO_DRAWDOWN_KILL_USD=8.0, DRIFT_CHECK_INTERVAL_S=300, DRIFT_TOLERANCE_PCT=0.05.`

---

## 2026-05-03

`2026-05-03 | INIT | Создан проект HLCarryBot/ — clean-architecture pivot с Polymarket на HL Funding Carry. Капитал $1,500. Архитектура: core/venues/feeds/strategies/risk/notifier/database.`

`2026-05-03 | database/db.py | BUGFIX BUG-001 — async with await get_db() → RuntimeError. aiosqlite.connect() не корутина. get_db() теперь возвращает context manager напрямую.`

`2026-05-03 | venues/hyperliquid.py | BUGFIX BUG-002 — None fields в testnet assetCtx → float() crash. Добавлен _f() helper, скип inactive assets.`

`2026-05-03 | venues/hyperliquid.py | BUGFIX BUG-003 — predicted_fundings AttributeError. Метода нет в SDK v0.23. Raw POST через info.post().`

`2026-05-03 | feeds/funding_scanner.py | BUGFIX BUG-004 — ranked=[] несмотря на BTC 0.0234%/h. Убран fee компонент из scanner min_rate.`

`2026-05-03 | venues/hyperliquid.py | BUGFIX BUG-005 — float_to_wire rounding crash. Добавлены _get_sz_decimals() кеш + round_size(). Применён во всех order методах.`

`2026-05-03 | feeds/funding_scanner.py | BUGFIX BUG-006 — predicted_map пустой. Неверный парсинг формата [[asset, [[venue, data], ...]], ...]. Переписан парсер, ищем "HlPerp" во вложенном списке.`

`2026-05-03 | strategies/funding_carry.py | BUGFIX BUG-007 — бесконечный retry loop. Добавлен _entry_cooldowns: Dict[str, int], cooldown 300s после failed entry.`

`2026-05-03 | risk/iron_dome.py | BUGFIX BUG-008 — invalid spot order при legging cover. filled_leg="perp" теперь закрывает perp reduce-only taker вместо spot order.`

`2026-05-03 | venues/hyperliquid.py + risk/iron_dome.py | BUGFIX BUG-009 — Price must be divisible by tick size (BTC asset=3). Добавлен round_price(px) в HLClient. Применён во всех местах где строятся цены taker-ордеров в iron_dome.py.`

`2026-05-03 | venues/hyperliquid.py | BUGFIX BUG-010 — WS reconnect storm. Добавлен exponential backoff 5→60s, reset при успешном connect.`

`2026-05-03 | brain/ + CLAUDE.md | INIT — создан brain/ directory (Karpathy Wiki) и CLAUDE.md в HLCarryBot/. Перенесена релевантная документация из MarketMakerBot/src/brain/.`

`2026-05-03 | venues/hyperliquid.py | MAJOR — добавлен spot routing: _ensure_spot_meta() строит perp→spot mapping (BTC→@142 mainnet, BTC→@50 testnet). U-prefix или canonical name match. place_maker/taker/maker_chase_entry получили is_spot=True флаг.`

`2026-05-03 | strategies/funding_carry.py | CRITICAL FIX — spot leg теперь ходит через настоящий spot pair (НЕ perp long как раньше). Sizing в base-asset units (одинаково на обеих ногах = true delta-neutral). Skip если нет spot pair, пустой book, спред>2%, или divergence spot/perp >5%.`

`2026-05-03 | strategies/funding_carry.py | EXIT FEES FIX — _close_position теперь добавляет exit fees (perp + spot) в pos.fee_paid_usd. Различает maker/taker fee когда срабатывает fallback. PnL accounting корректный.`

`2026-05-03 | risk/iron_dome.py | UPDATED — emergency_flat и deleverage_partial используют carry_pos.spot_pair и carry_pos.units для закрытия настоящего spot. Перестроены через is_spot=True.`

`2026-05-03 | core/models.py | ADDED — CarryPosition.units (base-asset count) + spot_pair (HL spot name). Ключевые поля для true delta-neutral.`

`2026-05-03 | main.py | ADDED — _recover_state() при старте читает clearinghouseState + spot balances + DB funding_cycles WHERE state=OPEN. Реконструирует CarryPosition. Циклы без on-chain позиций → ABANDONED.`

`2026-05-03 | TESTNET FINDING | Spot pairs на testnet существуют (BTC=@50, ETH=@1137, SOL=@1165, HYPE=@1035, PURR=PURR/USDC) но их liquidity сломана: BTC empty book, ETH spread 888%, SOL spread 152%. Только PURR/HYPE имеют нормальный стакан, но funding=0. Полное e2e тестирование требует mainnet.`

`2026-05-03 | CLEANUP | Закрыты orphan перп-шорты от старого кода: SOL -3.55, BTC -0.01138, ETH -0.169 (taker reduce-only). Чистое состояние для нового тестирования.`

## 2026-05-27

`2026-05-27 | venues/lighter.py | NEW — LighterClient: full async client for Lighter perp DEX. Public endpoints via raw aiohttp (no SDK). Order placement via lighter Python SDK (lazy init, ImportError if not installed). Methods: get_funding_rates(), get_all_exchange_funding_rates(), get_account(), get_positions(), get_margin_ratio(), get_order_book(), place_maker(), place_taker(), maker_chase_entry(). Rate format: decimal per hour (same as HL).`

`2026-05-27 | feeds/spread_scanner.py | NEW — SpreadScanner: polls Lighter's /funding-rates (returns all 4 exchanges: binance, bybit, hyperliquid, lighter) in one API call. Computes |hl_rate - lt_rate| spread. Ranks opportunities by spread desc. Verified live: 30 assets above 0.01%/h threshold. Top spread: CHIP 2076% APR, ETH 233% APR, SOL 314% APR.`

`2026-05-27 | strategies/cross_venue_carry.py | NEW — CrossVenueStrategy: state machine for HL+Lighter delta-neutral arb. Entry: open HL perp (maker) → open Lighter perp (maker). Both 0-delta (no spot leg). Exit triggers: spread_flip, spread_soft_exit, max_hold. Lighter leg failsafe: cover_naked_hl_leg() closes HL taker. DB table: cross_venue_cycles. Rebalance alerts (manual bridge, not auto).`

`2026-05-27 | core/models.py | ADDED — CrossVenuePosition, CrossVenueState, SpreadSnapshot dataclasses.`

`2026-05-27 | core/constants.py | ADDED — LIGHTER_BASE_URL, SPREAD_ENTRY_THRESHOLD=0.0001/h, SPREAD_EXIT_THRESHOLD=0.00005/h, CROSS_POSITION_SIZE_USD=$300, CROSS_MAX_POSITIONS=2, FEE_CROSS_ROUND_TRIP=0.030%.`

`2026-05-27 | database/schema.sql | ADDED — cross_venue_cycles table with full per-leg accounting.`

`2026-05-27 | main_cross.py | NEW — Entry point for cross-venue arb. Requires LIGHTER_L1_ADDRESS + LIGHTER_API_KEY_INDEX + LIGHTER_API_PRIVATE_KEY in .env + Lighter SDK installed.`

`2026-05-27 | LIGHTER SETUP | Prerequisites for main_cross.py: (1) Install SDK: uv pip install git+https://github.com/elliottech/zklighter-perps-python.git (2) Deposit USDC to Lighter via lighter.xyz (3) Create API keys in lighter.xyz → Settings → API Keys (4) Add LIGHTER_ACCOUNT_INDEX, LIGHTER_L1_ADDRESS, LIGHTER_API_KEY_INDEX, LIGHTER_API_PRIVATE_KEY to .env`

`2026-05-27 | RISK AUDIT | Обнаружены 5 проблем, все исправлены: (1) SPREAD_ENTRY_THRESHOLD 0.0001→0.0003/h — fake cluster активы (90 штук, spread=0.0004%/h) проходили фильтр но break-even 75h > MAX_HOLD=72h → гарантированный убыток; (2) Добавлен slippage буфер в математику: FEE_CROSS_TOTAL_ESTIMATE=0.050%, SLIPPAGE_ESTIMATE=0.020%; (3) CROSS_MAX_HOLD_HOURS 72→48h; (4) Добавлен CROSS_VENUE_WHITELIST — 29 ликвидных перпов, применён в scanner (scan phase) и strategy (entry phase); (5) Добавлены pre-entry guards в _enter_position: whitelist check, Lighter free balance >= $150, Lighter book spread <= 0.3%; (6) Добавлен emergency auto-close в _check_rebalance: если margin в emergency зоне >30 мин — taker close всех позиций.`

## 2026-05-04

`2026-05-04 | venues/hyperliquid.py | BUG-FIX-PHANTOM — MakerChase race condition. Добавлен get_perp_position_size() + _pre_size снимок в начале chase. После repost-cancel и timeout-cancel: сравниваем post_size с pre_size. Если изменение >50% target size — возвращаем order_id как phantom fill (биржа успела исполнить в тот же ms).`

`2026-05-04 | strategies/funding_carry.py | BUG-FIX-NAKED-SHORT — Spot leg timeout 90s→20s (SPOT_TAKER_FALLBACK_S). При timeout вместо немедленного cover_naked_leg: сначала попытка taker spot buy (ask+0.5%). Только если taker тоже FAILED — тогда cover_naked_leg закрывает perp. Slippage на spot taker << риск naked short.`

`2026-05-04 | strategies/funding_carry.py | BUG-FIX-IMPORT — FEE_PERP_TAKER и FEE_SPOT_TAKER не были импортированы. Все пути с taker exit (funding_flip, perp/spot close fallback) вызывали бы NameError. Добавлены в import block + SPOT_TAKER_FALLBACK_S.`

`2026-05-04 | main.py | FEATURE — reconcile_with_exchange(). Вызывается после _recover_state(). Находит orphan перп-позиции (есть на бирже, нет в strategy.positions) → emergency taker close + Telegram alert. Предотвращает "наслоение" позиций при перезапуске.`

`2026-05-04 | core/constants.py | ADDED — SPOT_TAKER_FALLBACK_S=20.0 (таймаут spot maker перед taker fallback).`

`2026-05-04 | DB | RECONCILE — закрыты 1 OPEN cycle (BTC, manual_close_ui), 7 active sessions, удалены 7 спурьезных funding_payments от BUG-017 replay.`

`2026-05-04 | strategies/funding_carry.py + main.py + schema.sql | BUG-017 FIX — WS userFundings replay при каждом reconnect дублировал записи. Введён 3-уровневый dedup: (1) in-memory _seen_fundings set по (asset, paid_at_ms); (2) UNIQUE INDEX uq_payments_asset_time + INSERT OR IGNORE — атомарно блокирует cross-session дубли; (3) temporal guard — игнорировать events с paid_at < pos.entered_at*1000. paid_at теперь в миллисекундах из HL события (не int(time.time())).`

`2026-05-04 | core/constants.py | PROD-BETA TUNING — FUNDING_ENTRY_THRESHOLD 0.00001→0.00025 (0.001%/h→0.025%/h). MIN_HOLD 1→8h, MAX_HOLD 4→48h. POSITION_SIZE_USD 20→300. ACTIVE_CAPITAL 50→500. FUNDING_EXIT_SOFT 0.000005→0.0001. FUNDING_HOLD_MIN 0.000005→0.0001. Цель: +EV в худшем сценарии (taker spot entry) за 6h hold.`
