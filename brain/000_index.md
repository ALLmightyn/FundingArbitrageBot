---
Type: MOC
Updated: 2026-05-27
---

# 000 — Root MOC: HLCarryBot

## Два режима

| Mode | Entry point | Strategy | Venues |
|------|-------------|----------|--------|
| **HL-only carry** | `main.py` | Perp short + Spot long on Hyperliquid | HL only |
| **Cross-venue arb** | `main_cross.py` | Perp short on high-rate venue + Perp long on low-rate venue | HL ↔ Lighter |

**Cross-venue статус (2026-05-27):** Код готов. Ждёт: Lighter SDK install + USDC deposit на Lighter + API ключи в .env.

**HL-only статус:** 🟢 mainnet, проверен, работал в мае 2026 (BTC carry).

---

## LIVE STATE (sync with core/constants.py after every param change)

| Param | Value |
|---|---|
| POSITION_SIZE_USD | $300/leg |
| MAX_POSITIONS | 3 |
| PERP_LEVERAGE | 3× |
| FUNDING_ENTRY_THRESHOLD | 0.00025 (0.025%/h) |
| FUNDING_EXIT_SOFT | 0.0001 (0.01%/h) |
| FUNDING_EXIT_FLIP | 0.0 |
| MIN_HOLD_HOURS | 8h |
| MAX_HOLD_HOURS | 48h |
| MAKER_CHASE_TIMEOUT_S | 30s |
| SPOT_TAKER_FALLBACK_S | 20s |
| EXIT_COOLDOWN_SECONDS | 300s |
| Network | **MAINNET** (HL_TESTNET=false) |

---

## DATABASE SCHEMA

```sql
-- funding_cycles
id TEXT PK, asset, state, entered_at INT, exited_at INT, hold_hours REAL,
spot_size_usd REAL, perp_size_usd REAL, perp_leverage INT,
spot_entry REAL, perp_entry REAL, entry_funding_rate REAL,
funding_collected REAL=0, fee_paid REAL=0, net_pnl REAL=0, exit_reason TEXT

-- funding_payments  [UNIQUE INDEX: (asset, paid_at)]
id INT PK AUTOINCREMENT, cycle_id TEXT, asset, paid_at INT(ms), amount_usd REAL, funding_rate_1h REAL

-- legging_events
id INT PK AUTOINCREMENT, asset, occurred_at INT, leg_filled TEXT, cover_action TEXT, cover_cost_usd REAL, cycle_id TEXT

-- deleverage_events
id INT PK AUTOINCREMENT, asset, occurred_at INT, trigger TEXT, margin_ratio REAL, size_before_usd REAL, size_after_usd REAL, cycle_id TEXT

-- kill_switch_log
id INT PK AUTOINCREMENT, triggered_at INT, trigger_reason TEXT, assets_affected TEXT, resume_at INT

-- sessions
id TEXT PK, started_at INT, ended_at INT, starting_nav REAL, ending_nav REAL,
total_cycles INT, total_funding_usd REAL, total_fees_usd REAL, net_pnl REAL
```

---

## CROSS-VENUE CONSTANTS (core/constants.py — обновлено 2026-05-27)

| Параметр | Значение | Смысл |
|----------|----------|-------|
| SPREAD_ENTRY_THRESHOLD | 0.00030 (0.030%/h) | Мин. спред для входа (включает slippage буфер) |
| SPREAD_EXIT_THRESHOLD | 0.00010 (0.010%/h) | Soft exit |
| SPREAD_EXIT_FLIP | -0.00005 (-0.005%/h) | Hard exit (taker) |
| CROSS_POSITION_SIZE_USD | $300/leg | Per-leg notional |
| CROSS_MAX_POSITIONS | 2 | Макс. одновременных пар |
| CROSS_MIN_HOLD_HOURS | 4h | Минимальное время |
| CROSS_MAX_HOLD_HOURS | 48h | Максимальное время |
| MAX_LIGHTER_BOOK_SPREAD_PCT | 0.3% | Отклонить вход если Lighter стакан шире |
| MIN_LIGHTER_FREE_BALANCE_USD | $150 | Минимум свободных средств на Lighter |
| FEE_CROSS_TOTAL_ESTIMATE | 0.050% | Worst-case round-trip (HL maker ×2 + slippage) |
| CROSS_VENUE_WHITELIST | 29 assets | BTC, ETH, SOL, BNB, XRP... (только ликвидные) |

## CROSS-VENUE DB (database/schema.sql)

```sql
cross_venue_cycles:
  id TEXT PK, asset, state, short_venue, long_venue,
  entered_at INT, exited_at INT, notional_usd, units,
  hl_entry_price, lighter_entry_price,
  hl_rate_at_entry, lighter_rate_at_entry, spread_at_entry,
  entry_fee_usd, exit_fee_usd,
  hl_funding_collected, lighter_funding_collected,
  net_pnl_usd, exit_reason
```

## CROSS-VENUE SETUP CHECKLIST

1. `uv pip install git+https://github.com/elliottech/zklighter-perps-python.git`
2. Deposit USDC on Lighter (via Arbitrum). Recommended ≥$500.
3. lighter.xyz → Settings → API Keys → Create (get index + private key)
4. Fill .env: LIGHTER_L1_ADDRESS, LIGHTER_ACCOUNT_INDEX=0, LIGHTER_API_KEY_INDEX, LIGHTER_API_PRIVATE_KEY
5. `python _scan_now.py` — verify live opportunities
6. `python main_cross.py`

## LINKS

- [HL API & Fee Specs](hyperliquid_specs.md)
- [Active Constants & State Machine](current_strategy.md)
- [Bug Graveyard](bugs_and_fixes.md) ← read before touching any order/entry/exit code
- [Change Diary](log.md)

---

## AUDIT QUERIES

```bash
sqlite3 database/carry.db "SELECT asset, state, net_pnl, exit_reason FROM funding_cycles ORDER BY entered_at DESC LIMIT 10;"
sqlite3 database/carry.db "SELECT asset, amount_usd, funding_rate_1h FROM funding_payments ORDER BY paid_at DESC LIMIT 10;"
sqlite3 database/carry.db "SELECT asset, leg_filled, cover_cost_usd FROM legging_events ORDER BY occurred_at DESC LIMIT 5;"
```
