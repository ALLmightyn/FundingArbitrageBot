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
| SPREAD_ENTRY_THRESHOLD | 0.00005 (0.005%/h, ~44% APR) | минимальный исторический спред для входа |
| SPREAD_EXIT_THRESHOLD | 0.00002 (0.002%/h) | soft-exit (maker) |
| SPREAD_EXIT_FLIP | -0.00002 | hard-exit (taker) при флипе |
| SPREAD_TWAP_WINDOW_S | 1800s (30 мин) | окно TWAP gate |
| SPREAD_TWAP_MIN_SAMPLES | 20 | ~10 мин прогрева (fail-closed) |
| STABILITY_LOOKBACK_HOURS | 24 | часов истории HL settlements |
| STABILITY_MIN_HIT_RATIO | 0.25 | 25% часов выше threshold (было 0.50 — захардкожено, исправлено) |
| MAKER_CHASE_TIMEOUT_S | 30s | таймаут cover-leg / выхода |
| LT_ENTRY_TIMEOUT_S | 90s | таймаут Lighter entry leg (дольше — HL захеджирован) |
| CROSS_VENUE_WHITELIST | 21 tier-1 активов | BTC ETH SOL BNB XRP DOGE ADA AVAX LINK DOT AAVE BCH LTC ATOM UNI SUI ARB OP APT TAO NEAR |
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

### Выход
- `spread_flip` (TWAP ≤ EXIT_FLIP): **taker** немедленно
- `spread_soft_exit` (TWAP ≤ EXIT_THRESHOLD, hold ≥ 8h): **maker**
- `max_hold` (48h): **maker**

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
