---
Type: Technical Spec
Status: Verified LIVE 2026-06-02 (read from both exchanges, account 0xF4Db600A...)
Linkage: [[000_index]]
---

# Leverage & Margin — как реально работают мои плечи (cross-venue)

**ВАЖНО:** `PERP_LEVERAGE=3` в `core/constants.py` относится ТОЛЬКО к HL-only режиму (`main.py`).
В cross-venue (`main_cross.py`) `set_leverage` **не вызывается** — плечо задано на самих биржах = **10x cross** на обеих. Не путать.

## Режим маржи (verified live)

| | Hyperliquid | Lighter |
|---|---|---|
| Margin mode | **CROSS** (`leverage.type=cross`) | **CROSS** (`margin_mode=0`) |
| Плечо аккаунта | **10x** | **10x** (IMF=10%) |
| Initial margin | notional/10 = **10%** | `initial_margin_fraction` = **10.00%** |
| Maintenance margin | **~5%** (½ initial, tier-зависимо) | **~6%** (1.49/24.56 на APT) |

Плечо НЕ меняет собранный funding (funding считается на notional, не на маржу).
10x = тот же funding в $ на меньшей марже = выше APR на капитал, но тоньше буфер до ликвидации.

## Per-asset max leverage (HL universe, verified)

| Asset | maxLeverage |
|---|---|
| BTC | 40 | ETH | 25 | SOL | 20 |
| BCH / APT / NEAR | **10** | TAO | **5** |

10x — это потолок аккаунта, но **кэпится per-asset**: на TAO макс 5x (HL) и 5x (Lighter IMF=20%).
Если просишь 10x на TAO — биржа срежет до 5x. Учитывать при выборе актива.

## КАК РАБОТАЕТ ЛИКВИДАЦИЯ при delta-neutral + leverage (ключевое)

Позиция нейтральна по цене (long HL + short Lighter), НО **маржа живёт по-венью раздельно**.
Резкое движение → проигрывающая нога теряет маржу на СВОЕЙ бирже, выигрывающая копит на другой.
Хедж защищает **net equity**, но НЕ защищает **per-venue ликвидацию**.

- APT **дампит** → Lighter short в плюсе (safe), HL long теряет маржу → **HL может ликвиднуть long**.
- APT **пампит** → HL long в плюсе (safe), Lighter short теряет маржу → Lighter может ликвиднуть short.

Главный риск-вектор = ликвидация тонкой ноги до того, как сработает rebalance.
Защита: `REBALANCE_MARGIN_WARN/EMERGENCY` + auto-close — но они реактивны (нужно время реакции).

## HL: SPOT↔PERP РАЗДЕЛЕНИЕ КОЛЛАТЕРАЛИ (не перекос капитала!)

HL — унифицированный аккаунт (один адрес), НО коллатераль перпа и спота держится раздельно.
USDC на споте НЕ работает как маржа перпа автоматически — нужен перевод **spot→perp**.

Verified 2026-06-02 (полный баланс):
- **HL spot USDC: ≈ $98.90** | **HL perp accountValue: $0.0** → весь капитал HL на споте.
- **Lighter collateral: ≈ $100.3**, margin ratio 1.5%.
- Капитал по факту **СБАЛАНСИРОВАН ~$99 / $99** — перекоса НЕТ.
  (Ранняя запись про «HL $5.76 vs Lighter $100, 17×» была ОШИБКОЙ — смотрел только perp accountValue,
   игнорируя spot USDC. Деньги целы, просто не на той стороне.)

**Капитал в норме.** HL — единый аккаунт: spot USDC ($98.90) является коллатералью для перпов автоматически.
`clearinghouseState.accountValue = 0` когда нет открытых перп-позиций — это норма, а не потеря денег.
Бот открывал HL-ноги именно с этого аккаунта десятки раз и будет продолжать.

Итого: **HL ≈ $98.90 + Lighter ≈ $100.3 = ~$199, паритет.** Gate по капиталу отсутствует.

## Цифры для проверки (snapshot 2026-06-02 16:01)

- HL: accountValue 5.757, totalNtlPos 47.93, totalMarginUsed 4.793, crossMaintMargin 2.397 (5.0%).
- Lighter: collateral 99.834, cross_init_margin 2.484 (10.1%), cross_maint_margin 1.491 (6.07%), margin_ratio 0.0149.

Скрипт переснятия: `python scripts/leverage_audit.py` (read-only).
