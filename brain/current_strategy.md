---
Type: Strategy Reference
Status: Active (testnet)
Updated: 2026-05-04
Linkage: [[000_index]]
---

# Текущая стратегия — Funding Rate Carry

## State Machine

```
SCAN → QUALIFY → ENTER_MAKER → HOLD → EXIT_MAKER → COOLDOWN → SCAN
```

Файл: `strategies/funding_carry.py:CarryStrategy`

### Переходы состояний

| Из          | В            | Триггер                                     |
|-------------|--------------|---------------------------------------------|
| SCAN        | ENTER_MAKER  | asset в top-N кандидатах, нет cooldown       |
| ENTER_MAKER | HOLD         | обе ноги (perp + spot) заполнены            |
| ENTER_MAKER | COOLDOWN     | perp fill fail → del position + cooldown    |
| ENTER_MAKER | legging cover| perp fill OK, spot fail → IronDome          |
| HOLD        | EXIT_MAKER   | funding flip / soft exit / max_hold         |
| EXIT_MAKER  | COOLDOWN     | обе ноги закрыты                            |
| HOLD        | KILLED       | kill-switch армирован во время legging cover |

## Entry Protocol (Perp-First)

```
1. set_leverage(asset, PERP_LEVERAGE, is_cross=False)
2. maker_chase_entry(asset, is_buy=False, size=perp_size, "PERP_SHORT")
   └─ timeout → del position, entry_cooldown, return
3. [perp filled] → maker_chase_entry(asset, is_buy=True, size=spot_size, "SPOT_BUY")
   └─ timeout → IronDome.cover_naked_leg(asset, pos, filled_leg="perp")
4. [both filled] → pos.mark_entered(), save_cycle_open()
```

## Exit Protocol

- **funding_flip** (predicted < 0) → emergency_flat() (taker, both legs, immediate)
- **funding_soft_exit** (predicted < FUNDING_EXIT_SOFT) + hold >= MIN_HOLD → maker exit
- **max_hold** (hold_hours >= 24h) → maker exit
- Maker exit timeout → taker fallback

## Maker Chase (venues/hyperliquid.py:maker_chase_entry)

- Ставим ALO на touch price (best_ask для sell, best_bid для buy)
- Если цена двинулась → cancel + repost на новом touch
- Max reposts: 5 | Timeout: 30s
- **Бесплатно пока не заполнилось** (ALO cancel = $0 комиссия)

## Константы (core/constants.py)

| Константа                  | Значение    | Смысл                              |
|----------------------------|-------------|-------------------------------------|
| POSITION_SIZE_USD          | 300.0       | Размер каждой ноги в USD            |
| MAX_POSITIONS              | 3           | Макс. одновременных позиций         |
| PERP_LEVERAGE              | 3           | Плечо на perp                       |
| FUNDING_ENTRY_THRESHOLD    | 0.00025     | Мин. predicted rate для входа (0.025%/h) |
| FUNDING_EXIT_FLIP          | 0.0         | Немедленный выход при негативе       |
| FUNDING_EXIT_SOFT          | 0.0001      | Плановый выход ниже порога           |
| FUNDING_HOLD_MIN           | 0.0001      | Мин. rate чтобы держать позицию     |
| MIN_HOLD_HOURS             | 8           | Минимальное время до выхода         |
| MAX_HOLD_HOURS             | 48          | Максимальное время до выхода        |
| EXIT_COOLDOWN_SECONDS      | 300         | Пауза после закрытия позиции        |
| MAKER_CHASE_TIMEOUT_S      | 30.0        | Timeout для maker chase             |
| MAKER_CHASE_MAX_REPOSTS    | 5           | Макс. репостов в chase              |
| MAKER_CHASE_REPOST_DELAY_S | 2.0         | Пауза между проверками              |
| MAKER_EXIT_TIMEOUT_S       | 120.0       | Timeout для maker exit              |
| SCAN_INTERVAL_SECONDS      | 60          | Интервал FundingScanner             |
| SCAN_TOP_N_ASSETS          | 30          | Сколько asset анализировать         |
| MARGIN_RATIO_WARN          | 0.50        | Деплевередж при 50% от ликвидации   |
| MARGIN_RATIO_EMERGENCY     | 0.75        | Emergency flat при 75%              |
| LEGGING_EVENT_LIMIT        | 3           | Kill-switch при N legging/час       |
| KILL_SWITCH_COOLDOWN_SECONDS | 14400     | 4h cooldown после kill-switch       |
| FEE_PERP_MAKER             | 0.00015     | 0.015%                              |
| FEE_SPOT_MAKER             | 0.00040     | 0.040%                              |
| FEE_PERP_TAKER             | 0.00045     | 0.045%                              |
| FEE_SPOT_TAKER             | 0.00070     | 0.070%                              |
| FEE_ROUND_TRIP_ALL_MAKER   | 0.00110     | 0.110% — минимальный барьер EV      |

## EV Math (на $300/ноту, все 4 ноги maker)

- Round-trip cost: $300 × 0.110% = **$0.33** за цикл
- Break-even hold: $0.33 / ($300 × 0.0001/h) = **11 часов** при 0.01%/h rate
- При 0.025%/h (типичный BTC testnet): break-even = **4.4 часа**
- При $7.50/day target: нужно ~22.7 funding periods/day / 3 позиции ≈ 7.6 periods/pos

## IronDome Risk Controls

| Контроль          | Порог      | Действие                          |
|-------------------|------------|-----------------------------------|
| Legging cover     | spot miss  | reduce-only taker на perp leg     |
| Kill-switch       | 3 events/h | cooldown 4h, все операции стоп    |
| Deleverage        | margin 50% | taker close 33% каждой ноги       |
| Emergency flat    | margin 75% | taker close 100%, cooldown 1h     |
| Funding watchdog  | predicted<0| immediate taker exit              |
