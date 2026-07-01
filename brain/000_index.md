---
Type: MOC
Updated: 2026-05-30
---

# 000 — Root MOC: HLCarryBot

## Два режима

| Mode | Entry point | Strategy | Venues |
|------|-------------|----------|--------|
| **HL-only carry** | `main.py` | Perp short + Spot long на Hyperliquid | HL only |
| **Cross-venue arb** | `main_cross.py` | Perp short на high-rate + Perp long на low-rate | HL ↔ Lighter |

**Cross-venue статус (2026-05-30):** 🟢 Live mainnet. Тестовый режим $25/leg. Накоплено 2 успешных закрытых цикла.

---

## LIVE STATE (sync с core/constants.py после каждого изменения параметра)

| Параметр | Значение | Смысл |
|---|---|---|
| CROSS_POSITION_SIZE_USD | **$25/leg** | TEST MODE — поднять до $500+ после 3-5 чистых циклов |
| CROSS_MAX_POSITIONS | 1 | строго 1 монета одновременно |
| CROSS_MIN_HOLD_HOURS | 8h | минимум перед soft-exit |
| CROSS_MAX_HOLD_HOURS | 48h | максимум |
| CROSS_EXIT_COOLDOWN_S | 900s | 15 мин пауза после выхода |
| SPREAD_ENTRY_THRESHOLD | **0.00008 (0.008%/h, ~70% APR)** | мин. спред входа (поднят 2026-06-05 с 0.005% — был ровно на 8h-maker-безубытке, нулевой запас) |
| SPREAD_EXIT_THRESHOLD | 0.00002 (0.002%/h) | soft-exit (maker) |
| SPREAD_EXIT_FLIP | **-0.00005 (-0.005%/h)** | hard-exit при флипе. Расширен 2026-06-05 с -0.002% (≈0 → churn TRX 3× за 11ч). Выход: HL maker + LT taker |
| CROSS_FLIP_MIN_HOLD_HOURS | **1.5h** | анти-чурн (2026-06-06): мелкий флип ждёт реверта 1.5ч вместо 0.25ч |
| CROSS_FLIP_DEEP_MULT | **3.0** | глубокий флип (≤ EXIT_FLIP×3 = −0.015%/h) выходит сразу, минуя floor |
| PERSISTENCE_FEE_MULT | **1.3** | вход только если Σspread за max-серию-подряд ≥ FEE_CROSS_ROUND_TRIP×1.3 (persistence gate ВНЕДРЁН 2026-06-06) |
| CROSS_FLIP_REENTRY_COOLDOWN_S | **14400 (4ч)** | длинный cooldown на ТОТ ЖЕ актив после flip (2026-06-05, против churn) |
| HL_ENTRY_MAKER_TIMEOUT_S | **60s** | длинное maker-окно HL-входа (первая нога, ничего не открыто) — режет taker fallback (2026-06-05) |
| PRICE_DIVERGENCE_KILL_PCT | **0.04 (4%)** | поднят 2026-06-05 с 2% (false-positive на дельта-нейтрали). Выход теперь HL maker + LT taker, не оба taker |
| SPREAD_TWAP_WINDOW_S | 1800s (30 мин) | окно TWAP gate |
| SPREAD_TWAP_MIN_SAMPLES | 20 | ~10 мин прогрева (fail-closed) |
| STABILITY_LOOKBACK_HOURS | 24 | часов истории HL settlements |
| STABILITY_MIN_HIT_RATIO | 0.25 | 25% часов выше threshold (было 0.50 — захардкожено, исправлено) |
| MAKER_CHASE_TIMEOUT_S | 30s | таймаут cover-leg / выхода |
| LT_ENTRY_TIMEOUT_S | 90s | таймаут Lighter entry leg (дольше — HL захеджирован) |
| CROSS_VENUE_WHITELIST | **67 активов** (verified 2026-06-03) | расширен с 21 tier-1: все пары HL+Lighter maxLev≥5x, OI>$1M |
| Network | **MAINNET** | HL_TESTNET=false |

---

## DATABASE SCHEMA (cross_venue_cycles)

```sql
cross_venue_cycles:
  id TEXT PK, asset, state TEXT (OPEN/CLOSED/ABANDONED),
  short_venue TEXT, long_venue TEXT,
  entered_at INT, exited_at INT,
  notional_usd REAL, units REAL,
  hl_entry_price REAL, lighter_entry_price REAL,
  hl_rate_at_entry REAL, lighter_rate_at_entry REAL, spread_at_entry REAL,
  entry_fee_usd REAL, exit_fee_usd REAL,
  hl_funding_collected REAL,         -- реальные USD (userFunding API, с BUG-038 фиксом)
  lighter_funding_collected REAL,    -- реальные USD (positionFunding API)
  lighter_last_funding_id INT,       -- idempotency anchor для Lighter (funding_id)
  hl_last_funding_time_ms INT,       -- idempotency anchor для HL (timestamp ms)
  net_pnl_usd REAL,
  exit_reason TEXT
```

**Полезные запросы:**
```bash
# Последние циклы с P&L
sqlite3 database/carry.db "SELECT asset, state, round((exited_at-entered_at)/3600.0,1) AS hold_h, round(hl_funding_collected+lighter_funding_collected,5) AS fund, round(net_pnl_usd,5) AS net, exit_reason FROM cross_venue_cycles ORDER BY entered_at DESC LIMIT 8;"

# Текущая открытая позиция
sqlite3 database/carry.db "SELECT asset, round((strftime('%s','now')-entered_at)/3600.0,1) AS hold_h, round(hl_funding_collected,5) AS hl_f, round(lighter_funding_collected,5) AS lt_f, round(entry_fee_usd,4) AS fee FROM cross_venue_cycles WHERE state='OPEN';"
```

---

## АРХИТЕКТУРА FUNDING TRACKING (актуально после 2026-05-30)

**HL:** `userFunding` REST endpoint → реальные USD суммы, idempotent по `hl_last_funding_time_ms`.
Заменил `cumFunding.sinceOpen` (сбрасывался при каждом закрытии ноги — BUG-038).

**Lighter:** `positionFunding` authenticated API → реальные USD суммы, idempotent по `lighter_last_funding_id`.
Заменил rate×time (завышал в 3.3× — BUG-034).

---

## ENTRY / EXIT LOGIC

### Вход (Perp-First, BUG-036 исправлен)
1. SpreadScanner: исторический score (avg_spread × hit_ratio, 24h) → ranked list
2. Spike filter: 5 семплов warmup, σ-gate 2.5σ
3. TWAP gate: 20 семплов / 30 мин — fail-closed
4. HL leg: maker_chase_entry → при таймауте taker fallback (HL IOC)
5. Lighter leg: maker_chase_entry **15s** (`LT_ENTRY_MAKER_TIMEOUT_S`) @ bid/ask (BUG-036 исправлен)
   → если не заполнено: **taker IOC** (Lighter taker = 0% fee) + 2× ZK-settle check (5s+5s)
   → если всё равно None: 6s ZK-lag guard → проверяем позицию — только потом `_cover_naked_hl_leg`
6. 3 подряд провала (HL leg unfillable / Lighter genuinely failed) → 4h blacklist через `_entry_fail_streak`

### Выход (`cross_venue_carry.py:806-833`)
**КЛЮЧЕВОЕ — sign-flip, не модуль.** `effective_spread = sign * |TWAP|`, где
`sign = -1 if snap.short_venue != pos.short_venue else +1`. Магнитуда TWAP/instant
**всегда положительна**; выход по флипу триггерит **смена выгодного направления шорта**
(сканер говорит «теперь шорти другую биржу») → наша позиция начинает платить фандинг.
- `spread_flip` (effective ≤ EXIT_FLIP=-0.00005): **анти-чурн 2026-06-06 — два уровня.** Мелкий флип (шум) ждёт `CROSS_FLIP_MIN_HOLD_HOURS=1.5h` (даём реверту шанс, не платим лишний round-trip); глубокий флип (≤ EXIT_FLIP×`CROSS_FLIP_DEEP_MULT`=3 → −0.015%/h, реальный разворот) выходит сразу. Закрытие: **HL maker-chase (→taker fallback) + Lighter taker**. Раньше было `hold ≥ 0.25h` → чурн (15/25 циклов, −$0.20)
- `spread_soft_exit` (effective ≤ EXIT_THRESHOLD=0.00002, hold ≥ 8h): **maker**
- `max_hold` (48h): **maker**
- `price_divergence` (|HL mid − LT mid| > 2%): **taker** немедленно

⚠️ **ГРАБЛИ ЛОГА:** строка `SPREAD FLIP exit | effective=-0.0097%/h (TWAP=0.0097%/h, instant=0.0080%/h)`
печатает TWAP/instant как **модуль** (всегда «+»), а effective — со знаком. Выглядит как
«оба плюс, а вышел» — но это НЕ баг: направление развернулось. Не расследовать заново.

---

## KNOWN LIMITATIONS / WATCH LIST

- **BCH** — единственный стабильный кандидат сейчас (hits=13-21/24). Остальные активы: исторический спред ≤ EXIT_THRESHOLD после вычета Lighter ставки.
- **APR нестабилен по времени суток:** ночью (азиатская/европейская сессия) ~100% APR gross, днём ~10-25%. Средний реальный ~30-50%.
- **Масштаб:** при $2000 капитала ($1000/биржа) + плечо 2-3× → позиция $2000-3000 → ~100% net APR на капитал.

---

## LINKS

- [HL API & Fee Specs](hyperliquid_specs.md)
- [Bug Graveyard](bugs_and_fixes.md) ← читать перед правкой order/entry/exit кода
- [Change Diary](log.md)
- [Yield Landscape & 3 рычага](yield_landscape.md) ← почему carry скромен + где апсайд (ресерч 2026-05-30)
- [Persistence Gate](persistence_gate.md) ← серия часов подряд > hit_ratio, режет убыточные входы (2026-05-31)
- [Strategy Options 2026-06](strategy_options_2026-06.md) ← deep research: чурн-диагноз + 3 пути (carry-фикс / points-farming / гибрид), HL S3 live (2026-06-06)
- [Broad Research 2026-06-10](research_2026-06-10_monetization.md) ← clean-slate ресёрч A-E: TOP-1 мульти-venue farming (Ondo Perps/Pacifica/Paradex), TOP-2 Polymarket-арб; полная таблица отброшенного
- [InsiderScanner Verdict](insider_scanner_verdict.md) ← бэктест 1.1M сигналов: инсайдеры реальны (+7.8% edge на лонгшотах), но follow мёртв — маркет-импакт съедает edge до детекта
- [Implementation Plan 2026-06](impl_plan_2026-06.md) ← verified fees всех venue, формула Polymarket Liquidity Rewards, weekend-механика Ondo, протокол InsiderScanner v2 с пре-регистрацией
- [Leverage & Margin](leverage_and_margin.md) ← плечо 10x cross на ОБЕИХ биржах (НЕ PERP_LEVERAGE=3), per-venue ликвидация, капитал перекошен HL $5.76 / Lighter $100 (verified live 2026-06-02)

**WHITELIST UPDATE (2026-06-02, verified count 2026-06-03):** Расширен 21→**67** активов — все пары HL+Lighter с maxLev≥5x и OI>$1M. Instant scan видит ~30 кандидатов против 1 (BCH). Исторический фильтр всё равно gate-ит по 24h avg hits.
