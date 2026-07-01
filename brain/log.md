# log.md — Дневник изменений HLCarryBot

Формат: `YYYY-MM-DD HH:MM | ФАЙЛ | ЧТО + ПОЧЕМУ`

---

- **2026-06-10 (4) — Polymarket REWARD FARMER ЗАПУЩЕН (paper-замер).** `MarketMakerBot/src/reward_farmer.py` (pm2: pm-reward-farmer). Живой расчёт по 5332 reward-рынкам: 183 кандидата (фильтры: не-спорт, не-война WAR-блэклист, mid 0.2-0.8, >7д, рабочий стакан spread≤2v, конкуренты>0). Выбрано 8 рынков, snapshot-оценка **~$280/день gross** при $100/сторона ($1600 кап) — ЗАВЕДОМО завышена (статичная конкуренция, без adverse). Paper-замер: каждые 60s (= реальный семплинг Polymarket) считает нашу долю формулы ((v-s)/v)² в живых стаканах + would-fill события → честная цифра через 3-5 дней. Таблицы rf_markets/rf_samples/rf_fill_events в market_maker.db. **Урок расчёта: «100% доли» = пустые книги свежих военных рынков (Иран, спред 13-44¢ при v=4.5¢) — это плата за риск price discovery, НЕ free money; отрезаны блэклистом.** Live-инфра готова (py_clob_client_v2 + POLY_PRIVATE_KEY в .env). Решение о live ($500-1000) — по paper-цифрам.

- **2026-06-10 (3) — InsiderScanner v2 ЗАПУЩЕН + Polymarket MM дизайн.** (а) Написан `src/enrich_v2.py` (sidecar, детектор не тронут): снапшот стакана CLOB + exec_px_100/300/1000 + ask_depth + реальный gamma_slug/negRisk + block_ts из Polygon RPC + heartbeat 60s + pid-lock + INSERT OR IGNORE. Cursor = MAX(id), бэкфилла нет. pm2 стек (6 процессов) поднят, первая строка: **detect_lag_s=1.6s**, status=ok. Гипотеза пре-зарегистрирована: `InsiderScanner/research/PREREGISTRATION.md` (исполнимые лонгшоты ≥$700, n≥120, 3σ, негативный контроль <$700) — НЕ редактировать. (б) Дизайн Polymarket MM → [[polymarket_mm_design]]: reward-фарминг НЕ латенси-игра (семпл 1/мин), Rust отложен до Ф3; щит от токсичного потока = live-сигналы сканера. (в) Фарминг (стратегия 1) подтверждён юзером — факт продажи аналога CherrySoft = валидация рынка.

- **2026-06-10 (2) — БЭКТЕСТ InsiderScanner → [[insider_scanner_verdict]].** 1.1M сигналов, 985 dedup-юнитов non-sports: общий edge ≈ 0 (юзер был прав). Единственный живой срез — лонгшоты <10¢ (+7.8% edge, 4.3σ, ROI +194% ПО ЦЕНЕ ИНСАЙДЕРА), но replay по CLOB prices-history: первая же доступная цена после сигнала 14.6¢ vs 5.4¢ → ROI −11%. Уточнение per-unit: 91% сигналов исполнимы (цена не прыгает), но исполнимые НЕ выигрывают (6.5% vs 5.5%); победители концентрируются в прыгнувших/аномальных юнитах — исполнимость и информативность взаимоисключающи. Намёк: исполнимые ≥$700 win 11.6% vs 5.6% (1.7σ, 43 юнита — недоказуемо, проверять на новых данных). Побочная ценность: сигнал = детектор токсичного потока для MM (расширять спред). TOP-1/TOP-2 без изменений (farming + структурный Polymarket-арб).

- **2026-06-10 — BROAD RESEARCH (RESEARCH_PROMPT.md, все категории A-E) → [[research_2026-06-10_monetization]].** Вердикт подтверждает пивот 2026-06-07 и конкретизирует: TOP-1 = мульти-venue delta-neutral farming (Ondo Perps $100k USDC/нед live с 9 июня, Pacifica 500k pts/чт + python-sdk, Paradex S3 4M XP/нед + paradex-py); TOP-2 = реанимация Polymarket-инфры (структурный арб/MM, 14/20 топ-кошельков — боты). Отброшены: MEV/ликвидации/снайпинг (латенси-гонка, инфра дороже капитала), LP/lending/DOV (≈benchmark 9.5% APR), pairs trading (нет edge). Ключ: на $1500 платят за активность, не за капитал.

- **2026-06-07 — STRATEGIC VERDICT (по запросу юзера): carry мёртв + Lighter-фарм закрыт.** All-time 29 циклов: net **−$0.35**, funding $0.14 vs fees $0.49. Корень — не баг: спред-edge (~10% APR gross realized) структурно тоньше fee-drag при чурне (19/29 = spread_flip, <2.8h hold, фандинг≈0). Узкий лист (21) был так же убыточен (−$0.04/7ц), как широкий (67, −$0.31/22ц) → ширина whitelist НЕ рычаг. Масштаб/плечо НЕ помогают: fee и funding оба процентные → соотношение инвариантно к размеру. Профит зависит только от `спред×hold > round-trip`. **Lighter points-фарм ЗАКРЫТ:** LIT вышел 30.12.2025, S1+S2 уже раздали (12.5M pts), S3 НЕ анонсирован → фарм сейчас = ставка на неанонсированный ретро (юзер это отверг). HL S3 live, но $1500 = пыль в пропорциональном пуле. **Решение: pure-carry на $1500 не масштабировать, не лить больше времени.** Пивот-вектор (forward): delta-neutral объём на НОВЫХ перп-DEX где объём=поинты/кэш и сезон ранний — Ondo Perps (платит $100k USDC/нед СЕЙЧАС, не retro), Nado/INK (airdrop confirmed Jul-Sep'26), Dango (~50% supply, TGE Q4'26). Реальный множитель = мульти-акк, не капитал.

## 2026-05-31

`2026-05-31 11:15 | strategies/cross_venue_carry.py | BUGFIX — drift TG-спам: добавлен _last_drift_alert cooldown 1800s (30 мин). До патча: check_position_drift() шлёл TG-алерт каждые 5 мин без ограничения при любом дрифте. Первопричина спама: два экземпляра бота работали параллельно (PM2 + ручной nohup), оба открыли ADA → 2× позиции на биржах (HL=191, LT=316 vs booked=105). Оба процесса убиты, ADA закрыта тейкером (HL SELL 191 @ 0.23728, LT BUY 316.4 @ 0.23798), DB записи → ABANDONED. Перезапущено через PM2 единственным экземпляром.`

---

## 2026-05-30

`2026-05-30 20:30 | strategies/cross_venue_carry.py | BUGFIX BUG-042 — drift check: get_position_for_asset → get_position_size_or_raise. Lighter API 403/503 теперь бросает исключение → outer try/except делает continue (пропускает тик). Раньше: 403 глоталось внутри get_positions → возврат {} → lt_size=0.0 → ложный drift_orphan → emergency taker close → убыток $0.02 (APT цикл 2026-05-30 20:11).`

`2026-05-30 | strategies/cross_venue_carry.py, core/constants.py | BUGFIX BUG-040+041 — Lighter-нога: maker 90s → taker IOC fallback (0% fee). Добавлен LT_ENTRY_MAKER_TIMEOUT_S=15s. После 15s maker → place_taker IOC + 2× ZK-settle check (5s+5s). Финальный 6s ZK-lag guard перед _cover_naked_hl_leg предотвращает orphan Lighter short. Устраняет цепочку: timeout → HL cover fee → entry_fail_streak → 4h blacklist лучшего актива.`

---

## 2026-05-28

`2026-05-29 | feeds/spread_scanner.py, venues/lighter.py, strategies/cross_venue_carry.py, core/constants.py | REFACTOR ranking: PRIMARY signal теперь historical sustained spread (24h paired HL+Lighter settlements через /fundings public API + HL fundingHistory). Раз в час refresh_historical_stats() считает avg_spread × hit_ratio для всех whitelist activов. Ranking: hist score, instant используется только для direction/magnitude sanity. Старый stability_gate в _enter_position удалён (дублировал ranking). Пороги откалиброваны на живые данные: 0.025%/h → 0.005%/h (44% APR), MIN_HOLD 6h → 8h. Реалити: BCH единственный с hits>=12/24 при текущем рынке.`

`2026-05-29 | core/constants.py, feeds/spread_scanner.py, venues/hyperliquid.py, strategies/cross_venue_carry.py | FEAT historical stability gate. SPREAD_ENTRY 0.015%→0.025%/h. WHITELIST 33→21 (tier-1 only). MIN_HOLD 4h→6h. COOLDOWN 5min→15min. TWAP window 15min→30min. NEW: scanner.is_stable_carry() — pulls HL fundingHistory за 24h, проверяет ≥50% часов выше threshold в нашу сторону. Защита от instant spike traps вроде NEAR (24h avg 0.0007%/h, max 0.0013%/h vs показанные 0.05%/h).`

`2026-05-29 | venues/lighter.py, strategies/cross_venue_carry.py, core/models.py, database/schema.sql | FEAT BUG-034 — real Lighter settlement accounting через /positionFunding API. Заменил rate×time (завышал в 3.3×). Idempotency через lighter_last_funding_id. Restart-safe: recover_open_positions сбрасывает stale lighter_funding и перенабирает через API.`

`2026-05-29 | strategies/cross_venue_carry.py | BUGFIX BUG-035 — exit_soft/flip теперь на TWAP вместо instant spread. Fallback на instant если TWAP не валиден.`

`2026-05-29 | ui/dashboard.py, main_cross.py | UI: колонка "Entry spr." → "Entry/TWAP" — показывает current TWAP рядом с entry. Видно что мы фактически зарабатываем сейчас.`

`2026-05-29 | database/schema.sql | ALTER cross_venue_cycles ADD lighter_last_funding_id INTEGER. Миграция применена к live DB.`

`2026-05-28 | venues/hyperliquid.py | BUGFIX BUG-033 — 1000PEPE "invalid size". _get_sz_decimals кэш ключи HL names, вызов с Lighter canonical → fallback 4 decimals вместо 0 → HL reject. Fix: translate через self._hl(asset) перед lookup.`

`2026-05-28 | venues/lighter.py | BUGFIX BUG-032 — repost_delay=3s < ZK settlement ~4s → ghost position checks. Fix: min(5.0, ...).`

`2026-05-28 | feeds/spread_scanner.py, strategies/cross_venue_carry.py, core/constants.py | FEAT TWAP entry gate (BUG-031). _spread_history → _rate_history[(ts,hl,lt)]. Новый get_twap_spread(window_s=900). В _enter_position: 5-й фильтр — TWAP_15min ≥ SPREAD_ENTRY_THRESHOLD. Fail-closed на warmup (<10 семплов). Защита от instantaneous Lighter rate spike при фактически низком settlement TWAP.`

`2026-05-28 | core/constants.py | CROSS_MAX_POSITIONS: 3 → 1. Строго 1 монета одновременно.`

`2026-05-28 | strategies/cross_venue_carry.py | BUGFIX: scanner запрашивал ровно n=1 кандидата при MAX=1. Если топ-кандидат в кулдауне → тик пустой, #2/#3 не пробовались. Fix: запрашиваем n_slots + max(5, len(cooldowns)) кандидатов — с запасом для обхода кулдаунов.`

`2026-05-28 | strategies/cross_venue_carry.py | BUGFIX BUG-030 — одновременное открытие двух монет. tick() считал open_count один раз и затем в for-loop открывал ALL n=(MAX-open_count) кандидатов подряд без перепроверки. Fix: после каждого _enter_position пересчитываем new_count; если new_count > open_count — break. Теперь максимум 1 новая позиция за тик.`

`2026-05-28 | venues/lighter.py, strategies/cross_venue_carry.py | BUGFIX BUG-029 — дублирующий вход в WLD после рестарта. get_position_for_asset() глотает исключения → lt_size=0.0 при падении Lighter API → PARTIAL branch → HL нога закрыта тейкером + DB=ABANDONED → re-entry. Fix: новый метод get_position_size_or_raise() пробрасывает исключения; recover_open_positions() переключён на него.`

---

`2026-05-28 19:25 | venues/lighter.py | BUGFIX BUG-027 — Lighter maker_chase двойные/множественные заполнения (double-fill). Root cause: ZK rollup имеет задержку оседания ~3-4с. cancel_order проверяет order_book_orders: если ордер ещё в mempool (не виден в книге) → возвращал True с "no resting order found". maker_chase интерпретировал это как успешную отмену и делал ещё один ордер → все ордера в итоге оседали, позиция умножалась (7× NEAR). Fix: cancel_order теперь возвращает None (а не True) если ордер не найден в книге. maker_chase: если cancelled is None → sleep 2s (дать цепи осесть) → повторная проверка позиции → если вырос → FILLED; иначе — продолжить следующую попытку.`

`2026-05-28 19:25 | venues/hyperliquid.py | FEAT fast-fail makers. HL maker_chase_entry: (1) "immediately matched" 3× подряд → abort (рынок слишком волатильный для maker); (2) empty book 3× подряд → abort. Предотвращает трату 30с на заведомо неисполнимые maker-ордера.`

`2026-05-28 19:25 | strategies/cross_venue_carry.py | BUGFIX BUG-028 — recover_open_positions: Lighter lt_size читался через key "base_amount" но реальный ключ API — "size". → lt_size всегда 0 → PARTIAL path вместо RESTORE. Fix: проверяем оба ключа: size || base_amount. Параллельно: place_taker() требует price — добавлен lookup mid-price из L2 book перед taker-close в recovery.`

`2026-05-28 18:50 | venues/hyperliquid.py | BUGFIX BUG-025 — KeyError '1000PEPE' в l2_snapshot. name_to_coin заполняется один раз при init SDK, но meta_and_asset_ctxs() возвращает свежие данные включая новые листинги. Fix: (1) в get_funding_rates — дополняем name_to_coin для каждого актива в свежем universe; (2) в get_l2_book — fallback на прямой POST если KeyError.`

`2026-05-28 18:40 | venues/lighter.py | BUGFIX BUG-023 — cancel_all_orders(CANCEL_ALL_TIF_IMMEDIATE) отклоняется сервером с "CancelAllTime should be nil". SDK binary устанавливает cancel_all_time в non-nil значение даже для IMMEDIATE режима. Новый подход: query order_book_orders(market_index, 100) → находим ордер по owner_account_index == account_index → cancel_order(market_index, order_index).`

`2026-05-28 18:40 | strategies/cross_venue_carry.py | BUGFIX BUG-024 — "cannot determine price for 1000PEPE" когда snap.hl_mark_px=0 (актив попал в common_assets через hl_via_lighter, а не через прямые HL данные). Добавлен fallback 2: Lighter mid price из lt_bid/lt_ask которые уже есть в scope из book spread check.`

`2026-05-28 18:15 | core/constants.py | TUNING — снижены пороги для активного фарминга: SPREAD_ENTRY_THRESHOLD 0.030%/h → 0.015%/h (131% APR, реальный NEAR-цикл 108% был выгоден); SPREAD_EXIT_THRESHOLD 0.010%/h → 0.005%/h; SPIKE_HISTORY_MIN_SAMPLES 12 → 5 (warmup 5 мин вместо 12); SPIKE_SIGMA_THRESHOLD 2.0 → 2.5 (меньше ложных отклонений); SPREAD_SCAN_INTERVAL_S 60 → 30 (вдвое быстрее ловит окна); CROSS_MAX_POSITIONS 1 → 3 (диверсификация, 3×$25×2=$150 из $200 = 75% deployed).`



`2026-05-28 16:10 | feeds/spread_scanner.py + strategies/cross_venue_carry.py | BUGFIX BUG-022 — spike filter fail-open на холодном старте. is_spike() возвращал (False, "insufficient_history") при <12 сэмплах → бот входил в NEAR при Lighter rate ~0.091%/h (~800% APR), ставка нормализовалась до 0.013%/h за 1 час. Реальный APR ~108% вместо ожидаемых 800%. Fix: is_spike() теперь fail-closed — возвращает (True, "warming_up") пока история не накоплена. Стратегия: при "warming_up" — не ставит cooldown, просто ждёт следующий тик.`

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

`2026-05-29 | venues/lighter.py + strategies/cross_venue_carry.py | BUG-036 FIX — Lighter maker side inverted (buy@ask/sell@bid = POST_ONLY reject = ghost). Исправлено на buy@bid/sell@ask. + HL taker fallback при maker-timeout. + единый _entry_fail_streak (HL timeout ИЛИ Lighter ghost), 3 подряд → 4ч blacklist. Корень всех legging-cover потерь на BCH/NEAR/TAO.`

`2026-05-30 | venues/hyperliquid.py + strategies/cross_venue_carry.py + core/models.py + schema.sql | BUG-038 FIX — HL funding: заменён cumFunding.sinceOpen (сбрасывается при закрытии ноги) на userFunding REST API (реальные USD-суммы, idempotent по time_ms). BUG-039 FIX — recovery orphan close: lt_is_buy=long_v→short_v (SELL вместо BUY удваивал Lighter short).`

`2026-06-02 | AUDIT | Полный аудит за 6 дней (с 05-27). CLOSED: 6 циклов, net −$0.0498 (весь минус = fees на emergency_margin/drift_orphan, funding не собран). ABANDONED: 9 (ghost/дубли, net $0). OPEN: APT short-Lighter/long-HL, hold 45.4h, funding +$0.1706 (HL $0.157 + LT $0.0136), fee $0.0037 → net MTM +$0.163, чистый APR ≈126% на notional. Единственный нормальный закрытый выход: BCH spread_flip +$0.014/9.8h. Economic P&L всего ≈ +$0.11. Механика funding-tracking (BUG-034/038) подтверждена — реальные USD сходятся. Real APR ≠ displayed (дисплей завышает ~7×). pm2 6 рестартов (все краши 28 мая, до текущего запуска 31 мая, сейчас стабилен).`

`2026-06-02 | brain/leverage_and_margin.md | VERIFIED LIVE — снято с обеих бирж. Плечо cross-venue = 10x CROSS на ОБЕИХ (PERP_LEVERAGE=3 = только HL-only main.py, в cross set_leverage не вызывается). Init margin 10%, maint ~5% (HL) / ~6% (Lighter). Per-asset cap: TAO макс 5x. Капитал перекошен: HL accountValue $5.76 (marginUsed 83%) vs Lighter $100 → HL узкое место, скейл невозможен без долива USDC на HL. Per-venue ликвидация: дамп long-ноги → HL ликвиднётся даже при нейтральной нетто.`

`2026-06-02 | ⚠️ FINDING ORPHAN — на HL висит НЕхеджированный ADA short szi -105 (~$23.4), Lighter ADA=0 (flat). Орфан от manual_close_ada_duplicate: закрылась только Lighter-нога, HL осталась голой. Сейчас +$1.54 случайно, но это голый направленный риск + жрёт $2.34 маржи на тонком HL. DB не трекает (не в OPEN). Требует ручного закрытия/рехеджа.`

`2026-06-02 | ⚠️ FINDING FUNDING-BUG — DB hl_funding_collected APT = $0.157, но реальный HL userFunding = $0.012 (49 событий) и cumFunding sinceOpen = -$0.011. Завышено ~13×. Lighter ОК ($0.0136 = total_funding_paid_out). → реальный funding открытой APT ≈ +$0.026 за 45h, real APR ~14-20%, НЕ 126%. Похоже userFunding-аккумуляция в боте не дедупит (anchor на последнем событии, но сумма 13× больше). Кандидат на BUG-042. Аудит P&L от 2026-06-02 надо пересчитать с реальными цифрами.`

`2026-06-02 | strategies/cross_venue_carry.py + main_cross.py | BUG-042 FIX — HL funding overcount ~14× (WS isSnapshot replay, рецидив BUG-017). record_hl_funding идемпотентен по hl_last_funding_time_ms + skip isSnapshot в _on_ws_message. DB открытой APT исправлена 0.157→0.011271. Verified: после рестарта+реконнекта funding $0.0252 не раздулся. Реальный APR открытой APT ~14-20%, НЕ 126%.`

`2026-06-02 | EXCHANGE STATE | ADA-орфан закрыт ботом (drift/orphan logic): Close Short 105 @0.2227 closedPnl +$1.533 fee $0.010. HL equity после: $2.46 ≈ занятая маржа APT $2.45 (буфер ~0). Ledger переводов за 8ч нет — просадка $5.76→$2.46 необъяснима из API (квирк perp/spot аккаунтинга или транзиент). WATCH: HL тонкий, перед скейлом долить USDC.`

`2026-06-02 | EXCHANGE + DB RECONCILED | APT закрыт (drift_orphan, net +$0.0102 по честным числам). Обе биржи FLAT (HL accountValue 0.0, Lighter collateral $100.32), 0 открытых циклов. Бот ОСТАНОВЛЕН (pm2 stop). Причина голой ноги: старый процесс выставил maker close HL-ноги → я рестартнул для BUG-042 fix → recovery снял снимок HL=27.14 ДО исполнения ордера → ордер исполнился → бот считал обе ноги живыми, Lighter остался голый. Drift-детектор поймал и закрыл. РЕШЕНИЕ ПРИНЯТО: чинить реконсиляцию БД↔биржа перед любым возобновлением.`

`2026-06-02 | ROOT CAUSE орфанов (для фикса) | drift-детектор проверяет ТОЛЬКО позиции в in-memory positions dict. Как только цикл помечен ABANDONED/CLOSED — он удаляется из tracking, и любой остаточный exchange-position становится НЕВИДИМЫМ орфаном (ADA dup, XLM ghosts — их закрывал юзер руками). Источники рассинхрона: (1) resting order исполняется после snapshot/restart; (2) дубль-вход (2× ADA за 2 мин при max=1); (3) DB OPEN без exchange position (ghost). Фикс: периодический sweep по ВСЕМ exchange-позициям обеих бирж как source of truth, независимо от того, что бот «трекает».`

`2026-06-02 | strategies/cross_venue_carry.py + main_cross.py | BUG-043 FIX (P0-P3 реконсиляция) — reconcile_orphans() exchange-truth sweep (flatten орфанов вне tracked-циклов, 2-страйк дебаунс, gated recovery); dup-guard в _enter_position (проверка обеих бирж, fail-closed); _cancel_all_resting_orders() в начале recovery (чинит resting-order гонку); _recovery_done флаг. Validated на флэте (no-op) + симуляции орфана (flatten reduce-only). Бот пока ОСТАНОВЛЕН — не возобновлял live.`

`2026-06-02 | core/constants.py | WHITELIST EXPANDED 21→70 активов — добавлены все активы присутствующие на HL (maxLev≥5x, OI>$1M) И на Lighter. Было: 21 (только tier-1 bluechips). Стало: 70 (+49). Новые тир-2 (10x HL): HYPE ZEC TON PUMP WLD PAXG FARTCOIN 1000PEPE ENA ONDO TRX JUP TRUMP CRV 1000BONK 1000SHIB DYDX XPL. Тир-3 (5x HL): LIT ASTER XMR MON ZRO WLFI PENDLE LDO VIRTUAL HBAR ICP EIGEN MNT WIF MORPHO ETHFI TIA STRK FIL POL SPX SEI BERA ZK PYTH KAITO AVNT 1000FLOKI AXS XLM. Instant scan 30/70 выше threshold, top=LDO 508% APR (instant). Исторический фильтр 24h + TWAP gate + sigma-spike = страховка от спайков.`

## 2026-06-03 — Brain lint (аудит ложной инфы)
- **BCH spread_flip exit разобран:** не баг. `effective = sign*|TWAP|`, sign флипнул т.к. сканер сменил выгодное направление шорта (lighter→hl). Лог печатает TWAP/instant как модуль (всегда +), effective со знаком → выглядит как «оба плюс, а вышел». Задокументировал в 000_index чтобы не расследовать повторно.
- **Fix:** whitelist в 000_index был «21»/«70» → реально **67** (verified python count из constants.py).
- **Fix:** exit-секция 000_index переписана — добавлен sign-flip механизм, price_divergence (2%), пометка про граблю лога.
- **Fix:** current_strategy.md помечен ⛔ DEPRECATED — описывал мёртвую HL-only spot+perp как «Active (testnet)», live это cross-venue mainnet.

## 2026-06-03 — spread_flip exit: taker → HL maker / Lighter taker
- **Change:** `cross_venue_carry.py:_close_position` получил per-leg флаги `hl_taker`/`lt_taker` (backward-compat: None → use_taker). spread_flip теперь `hl_taker=False, lt_taker=True`.
- **Why:** taker сильно жрал прибыль (BCH цикл −69% APR из-за HL taker 0.045% на выходе). Фандинг settled раз в час → есть ~1ч окно выйти maker'ом. HL maker экономит 0.045→0.015=0.03%.
- **Почему Lighter оставлен taker:** Lighter taker=0% (бесплатный+мгновенный). Maker там бессмысленен и создал бы голую ногу до 30с (HL закрывается первой → Lighter висел бы один). Текущий порядок (HL→LT) + LT taker = голое окно ~1-2с.
- **Не задеплоено в runtime:** требует `pm2 restart hlcarrybot-cross`.

## 2026-06-03 — restart deploy spread_flip fix → WLD force-closed (см BUG-044)
- Рестарт для деплоя maker-exit фикса при открытой WLD. Recovery закрыл WLD грязно (two-phase, HL maker @0.5013 + LT taker @0.527). HL closedPnl=+$6.24, обе биржи FLAT после. Equity ~break-even.
- Урок: НЕ рестартить с открытой позицией; recovery надо чинить (RESTORE вместо close; orphan reduce_only=True). Записано BUG-044.

## 2026-06-03 — FIX BUG-044: restart больше не закрывает позиции
- `main_cross.py` finally: убрал авто-close позиций на shutdown (дрался с RESTORE). Теперь оставляет открытыми → recovery RESTORE на следующем старте. Флаг FLATTEN_ON_SHUTDOWN=true для принудит. flatten.
- `cross_venue_carry.py` recovery orphan-close Lighter: reduce_only=True (был False).
- Теперь pm2 restart с открытой позицией безопасен — дельта-нейтраль переживает рестарт.

## 2026-06-05 — ПОЛНЫЙ АУДИТ (15 closed cycles)
- **ВЕРДИКТ: операционно исправен, экономически в минусе.** SUM(net) closed = -$0.1438. Funding=$0.1037 < Fees=$0.2475. Все категории выхода net-отрицательны.
- Exit breakdown: spread_flip 5×(-0.050), price_divergence 4×(-0.040), drift_orphan 3×(-0.023), emergency_margin 2×(-0.030, avg 0.5h), shutdown 1.
- **Корень: realized funding (~0.005-0.01%/h) тоньше round-trip fee (~0.03% maker / ~0.09% taker).** Нужно 9-16ч холда только чтобы отбить fee; сигнал флипает/дивергит раньше. Единственный профит: ICP 23.9h +$0.0014 (доказывает: работает ТОЛЬКО при долгом холде).
- **#1 BUG-кандидат — price_divergence false-positive:** порог 2% |HL_mid-LT_mid| → taker-close ОБЕИХ ног. Но позиция delta-neutral → дивергенция цен НЕ ломает нейтральность, а бот её фиксирует в fee-убыток. ZEC дважды за 30мин (10:25 + 10:51) — вход+taker-выход = чистый слив fee. WLD/ICP/ZEC: 2.1-2.7%, похоже на quote-noise (тонкий стакан/устаревший mid), не реальный базис.
- **HL entry уходит в taker (fee 0.045% вместо maker 0.015%):** 23 taker fallback, "Post only would have immediately matched" asset=222 — maker-chase бьётся в движущийся стакан.
- **Lighter size errors:** code 21706 / "invalid size" 12× — ВСЕ на ZEC (szDecimals на цене ~339).
- **RESTORE подтверждён LIVE:** ICP RESTORED to HOLD после рестарта 06-05 06:42 (age 23.7h). BUG-044 фикс работает. Также APT 45.9h, ADA 3.5h, XLM ранее.
- **Маржа HL здорова сейчас:** equity $96.35, used $2.51, ratio 2.6% (раньше был перекос $5.76 → пополнен). emergency_margin больше не триггерит.
- Текущая ADA: HOLD 1.2h, funding +$0.0008, fee $0.0112, net -$0.0104. Нейтральна, медленно копит.

- Сверка фандинга с биржей (2026-06-05): HL биржа +$0.066 vs DB +$0.047, ADA orphan недосчёт ~$0.013. См. WATCH-048. Lighter не сверен.

- Ночной рестарт 06-05 06:42 РАЗГАДАН: apt-daily-upgrade.timer → unattended-upgrades → systemd дёрнул pm2-root.service → SIGTERM. НЕ краш, НЕ деплой. BUG-044 отработал: ICP RESTORED. Повторяется ~еженощно. См. WATCH-049.

## 2026-06-05 — FIX прибыльности (3 рычага, по итогам аудита)
Все категории выхода были net-отрицательны (fees > funding). Внесено:
- **constants.py**: SPREAD_ENTRY_THRESHOLD 0.00005→0.00008 (запас над 8h-maker-безубытком); SPREAD_EXIT_FLIP -0.00002→-0.00005 (против churn на шуме); PRICE_DIVERGENCE_KILL_PCT 0.02→0.04; +CROSS_FLIP_REENTRY_COOLDOWN_S=14400 (4ч); +HL_ENTRY_MAKER_TIMEOUT_S=60.
- **cross_venue_carry.py**: (1) price_divergence теперь HL maker + LT taker (был taker обе) — WATCH-045 закрыт; (2) HL entry maker_chase timeout_s=60 (режет taker fallback) — WATCH-046 смягчён; (3) _set_cooldown(asset, cooldown_s) — после spread_flip ставит 4ч cooldown на тот же актив (против flip-flop); (4) импорты обновлены.
- Логика: цель — держать всё в maker (0.03% round-trip), не выходить рано на шуме, входить только в спред с запасом над fee. Доказательство рабочести: ICP 23.9h был единственный плюс.
- Не трогал: maker_chase внутренний алгоритм (риск), entry threshold можно ещё поднять если будет мало плюсовых.

- BUG-050 FIX: Lighter maker_chase double-fill (cancel-race, MON 2× нога). (1) пост-cancel fill-check всегда; (2) drift-гард тримит избыточную ногу reduce-only. См. bugs BUG-050.

- **2026-06-06 14:xx — AUDIT (полный, по запросу):** Сессия 20h (с 06-05 13:40): 8 циклов, все «ok» (легов-провалов нет), funding **$0.0079** vs fees **$0.1275** → net **−$0.12**. All-time CLOSED (25 циклов): net **−$0.29**, funding $0.11 / fees $0.41, 3 win / 22 loss. **КОРЕНЬ УБЫТКА — НЕ БАГ, А ЧУРН:** 15/25 выходов = spread_flip (−$0.20). Паттерн: вход → флип за 0.3–2.3h → выход ДО первого hourly-фандинга → платим полный 4-ногий round-trip, фандинг≈$0. При $25/leg экономика структурно отрицательна (fee/cycle $0.007–0.022 >> funding/cycle ≤$0.004). Прошлые митигации (06-05: entry thr↑, flip thr↑, 4h reentry cooldown) чурн не убрали. **Рассинхрона НЕТ:** live — Lighter все 15 поз=0.0 / collateral $100.06, HL equity $93.34 (margin check), БД без OPEN — бот flat и сверен. Прошлые desync (MON/ADA Lighter 2× double-fill) пойманы reconcile/drift, закрыты taker (drift_orphan −$0.023, emergency_margin −$0.03) — покрыто BUG-050. API: 502/504-шторм HL 06-06 13:04–13:07 + 429 06-05 17:40 → вероятно 2 рестарта pm2; все как ERR CRASH (loop выжил), но в окно 502 drift/reconcile слепы. Примечание: scripts/leverage_audit.py читает HL accountValue=0.0 (пустой/чужой кошелёк в скрипте) — артефакт скрипта, не desync.

- **2026-06-06 — ГИБРИД-ФИКС (anti-churn + persistence + farm-optics).** Решение юзера: Путь 3 из [[strategy_options_2026-06]]. (1) **constants.py:** +CROSS_FLIP_MIN_HOLD_HOURS=1.5, +CROSS_FLIP_DEEP_MULT=3.0, +PERSISTENCE_FEE_MULT=1.3. (2) **cross_venue_carry.py:825:** flip-выход два уровня — мелкий флип (шум) ждёт 1.5ч реверта вместо 0.25ч; глубокий (≤EXIT_FLIP×3=−0.015%/h) выходит сразу. Корень чурна (15/25 циклов spread_flip за 15мин, фандинг≈0, −$0.20). (3) **spread_scanner.py:** persistence gate ВНЕДРЁН (был только на бумаге в [[persistence_gate]] с 05-31) — refresh_historical_stats считает max_run/run_earn по серии часов ПОДРЯД выше порога (сортировка по ts), фильтр входа режет если run_earn < FEE_CROSS_ROUND_TRIP×1.3. (4) **scripts/farm_stats.py** (read-only, новый): фарм-оптика — volume/венчу, Sharpe, cost-of-volume. Базлайн: vol $2.5k/9.8д, cost 1.16bps, Sharpe −16.9. Синтаксис+импорты OK. **НЕ перезапущено** — ждёт `pm2 restart 0` от юзера.

- **2026-06-10 ~22:30 — PAPER-ФАРМ: первые 12ч adverse-замера.** NET все рынки **−$110/день** ($290 gross наград − $400 филлов). 100% убытка — 4 волатильных рынка (Перу 2й тур считают live: мид 0.10→0.55/12ч; Hyperbeat FDV; Claude arena). Спокойный поднабор (range мида 24ч ≤8¢): **NET +$26/день при нуле филлов** — выше критерия $15. Вторая находка: R:R перевёрнут — take +1¢ avg +$7 vs hard_stop −5¢ avg −$20…−34 (мид гэпает за стоп между 60с-семплами), 17 стопов/18 тейков. Решение юзера: правила НЕ менять, мониторить до 72ч в чистом виде. rf_report.py дополнен сплитом calm/volatile (порог 8¢/24ч). Кандидаты-фиксы после 72ч: vol-фильтр в селекторе, блэклист выборов в день подсчёта, пересмотр R:R.

- **2026-06-10 ~23:30 — ИССЛЕДОВАНИЕ REQUOTING (вопрос юзера «можем же переставлять?»).** Да: ws market-канал пушит каждую сделку/изменение книги, cancel бесплатен, лимиты 3500/3000 за 10с, наша латенси до CLOB (AWS eu-west-2) 2.5мс connect / цикл реакции ~50-100мс. Анатомия 38 paper-филлов по трейд-лентам: 37% фантомы (мид без сделок), 34% сделка не дошла до уровня, 8% уворачиваемы (warn 6-61с), **21% одиночные свипы — неустранимы**. Paper-модель завышает adverse в 3-5×; rf_report.py дополнен строкой «NET при реальной доле филлов». Но волатильная корзина минусовая даже с идеальным квотером (16 свипов/день × -$20 > наград) → vol-фильтр селектора остаётся защитой №1, ws-requote — №2. Детали в [[polymarket_mm_design]].

- **2026-06-11 ~10:00 — АНАЛИЗ 35ч paper-данных.** ГЛАВНОЕ: vol-фильтр доказан данными — из 72 закрытых позиций **71 открыта в рынках с range(6ч)>2¢ (PnL −$514), и лишь 1 в спокойном (−$21)** → гейт «range 6ч ≤2¢ на момент входа» отсёк бы 96% потерь, теряя <15% наград. Тренд 12ч-окон: кровавое окно 06-10 12:00 (−$318/29 филлов, пик Перу), последнее окно — 6 филлов/−$56. Перу сертифицирован → Fujimori-рынки успокоились, корзины перетекли как предсказано. НО: селектор затащил новую порцию свежих тонких книг (губернаторы Бразилии est=$363/д при comp=2-11 и спреде 7-11¢ — снова «плата за price discovery»), и **дыра в WAR-блэклисте: will-russia-enter-pokrovskoe прошёл** (нет слов russia|ukraine в регексе). Пост-72ч список фиксов: (1) vol-гейт 6ч≤2¢, (2) блэклист += russia|ukraine|pokrovsk|kursk|donbas|front, (3) re-check book_spread на каждом семпле, не только при селекции. Решение по live — после 72ч (осталось ~36ч).

- **2026-06-12 ~10:00 — ПОДГОТОВКА К LIVE: ночной ЧМ-урок + 3 фикса квотера. LIVE ЗАБЛОКИРОВАН ТОЛЬКО ДЕПОЗИТОМ.**
  (1) Ночью paper-катастрофа: анонс хедлайнера халфтайм-шоу ЧМ-2026 → 3 коррелированных рынка (Cabello/Dua Lipa/Adele) гэпнули 6¢→22-47¢, paper −$2095 за окно. Calm-корзина при этом ЦЕЛА: 6 филлов / +$9.9 all-time во всех окнах (rf_report врёт про «calm −$210/д» — его сплит по текущему 24ч-range, не по full-period; верить windowed-SQL).
  (2) ДЫРА: SPORT-регекс не знал fifa/world-cup/halftime, game_start_time у futures-рынков пуст → shadow-квотер выбрал 6 ЧМ-рынков включая халфтайм Burna Boy. Фикс: SPORT2-регекс в live_quoter.py (fifa|world cup|halftime|olympi|euro 202|copa|goal|extra time|group stage).
  (3) Добавлен VOL_GATE_24H=0.04 поверх 6ч-гейта (vol_6h→vol_range(interval)) — закрыта вчерашняя дыра gpt-5.6 «тихие 6ч после бурного дня».
  (4) WS-BAG: ping_interval держал соединение, но pong не обновлял last_ws_msg → в тихих рынках watchdog считал тишину смертью, FAIL-CLOSED каждые 1-4 мин = потерянные reward-семплы в live. Фикс: heartbeat-таск (явный ws.ping()/pong → last_ws_msg). После рестарта 2.5 мин чисто.
  Новая селекция без ЧМ: Yeet-токен / UMich Sentiment / Durable Goods, est ~$4.5/д на $180 (упало с $9 — ЧМ-пулы были жирные, но токсичные). Ключи/API-creds в config/.env есть, sig_type=2. **БЛОКЕР: USDC=0 на proxy и EOA (газ 2.63 POL есть). Юзеру: депозит → DRY_RUN=0 + pm2 restart.**

- **2026-06-11 ~22:00 — АНАЛИЗ УСТОЙЧИВОСТИ ЗАКОНОМЕРНОСТЕЙ (вопрос юзера «что может поменяться до 72ч?»).** По 12ч-окнам: (1) СТАБИЛЬНО — calm-рынки почти не наливают (2 филла из 88 all-time, PnL $0, во всех окнах); (2) СТАБИЛЬНО — vol-рынки минусуют в КАЖДОМ окне (−18/−144/−318/−56/−53), структурно, не только Перу; (3) ШАТКО — оценка наград calm-подмножества скачет $20→$399/день между окнами (ротация селектора + наша аппроксимация доли). Вывод: 72ч закрывает вопрос tail-риска calm-рынков, но НЕ вопрос размера наград — он решается только live (выплаты daily 00:00 UTC → ground truth за 24-48ч micro-теста). Критический путь к live: USDC на кошелёк + POLY_PRIVATE_KEY. ⚠️ Shadow-квотер выбрал gpt-5.6-рынок, который rf-классификация считает волатильным (24ч range>8¢) — vol-гейт 6ч≤2¢ пропустил; следить.

- **2026-06-11 — RE-RESEARCH ФАРМИНГА (вопрос юзера «можно ли уже прикинуть заработок?»).** Ondo Perps: public beta live с 10.06 (волны инвайтов), формула распределения $100k/нед ВСЁ ЕЩЁ не опубликована («trading activity»), в доках (llms.txt) страницы rewards нет; первая выплата ср 17.06 — до неё $/объём непросчитываем. Твит 0xasrequired: «$100k rewards this week … for tread users» — возможно неделя-1 идёт через терминал Tread.fi, проверить при получении доступа. Pacifica: TGE не подтверждён, поинты OTC ~$0.80 (сент-2025, спекулятивно). Вердикт: у фарминга обе компоненты дохода неизвестны (формула + цена поинта), у Polymarket — собственный замер $101/д NET на спокойных. Приоритет Polymarket подтверждён, по Ondo — только дешёвая подготовка (waitlist+sandbox), пересчитать 17-18.06. Детали в [[research_2026-06-10_monetization]].

- **2026-06-11 ~10:10 — LIVE-КВОТЕР НАПИСАН И ЗАПУЩЕН В SHADOW (DRY_RUN).** `MarketMakerBot/src/live_quoter.py` (pm2 pm-live-quoter). Всё из дизайна: двусторонняя котировка БЕЗ шорта (BUY YES + BUY NO), ws market-канал с requote при |Δmid|≥1¢, trade-pull 60с на любую сделку в рынке, vol-гейт 6ч≤2¢ при селекции (главный фикс по данным 71/72), WAR2-регекс (russia|ukraine|pokrovsk...), fail-closed при молчании ws>45с, инвентарь take+1¢/hard-stop−5¢/кап $60/рынок. DRY_RUN=1 дефолт: полный цикл пишется в lq_quotes/lq_events, ордера не шлются. Смоук: селектор 237→3 спокойных (rng6h 0-1¢), цены/размеры корректны, пойман и зафиксен дабл-плейс (гонка ws-init vs watchdog → гард st.placing/order_ids). Для live: DRY_RUN=0 + POLY_PRIVATE_KEY/POLY_PROXY_ADDRESS + USDC. Оценка micro-теста: ~$9/д на $180 ($30/сторона × 3 рынка).
