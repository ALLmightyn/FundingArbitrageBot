---
Type: Implementation Plan
Updated: 2026-06-10
---

# impl_plan_2026-06.md — Спеки реализации: farming + Polymarket MM + InsiderScanner v2

## VERIFIED FEES / REWARDS (2026-06-10)
| Venue | Maker | Taker | Награды | API |
|---|---|---|---|---|
| Ondo Perps | **1.5 bps** | 3.5 bps | $100k USDC/нед (метрика расчёта НЕ опубликована — замерить в нед.1) | REST+WS, SIWE→JWT, sandbox |
| Ostium | crypto 3 bps | crypto 10 bps; **стоки/RWA: 4 bps open, 0 close** + hourly rollover | Points S2 live, weekly, Boost Windows 2× | Builder Codes, контракты Arbitrum |
| Pacifica | **0.75 bps** | 2 bps | 500k pts/чт, формула динамическая, retro-slashing за sybil/self-trade | python-sdk официальный |
| Paradex | TBD | TBD | S3: 4M XP/нед | paradex-py |
| Polymarket | 0 | 0 | **Liquidity Rewards: $5M+/апрель, daily payout 00:00 UTC** | py-clob-client |

## ONDO WEEKEND MECHANICS (критично для DN на стоках)
- Выходные = Пт 20:00 ET → Вс 20:00 ET; праздники США так же.
- Цены НЕ замораживаются: mark и коллатерал маркируются непрерывно, **ликвидации работают в выходные**.
- Auto-Exchange в выходные = closed-market settlement fee (~2.5% от объёма продажи коллатерала).
- → DN-пара Ondo↔Ostium: держать маржу с запасом ×2 перед выходными или закрывать ноги в Пт.

## POLYMARKET LIQUIDITY REWARDS — ФОРМУЛА
S(v,s) = ((v−s)/v)² × b, где v = max_incentive_spread рынка, s = факт. спред от мида, b = in-game multiplier.
- Мид в [0.10,0.90]: одностороннее котирование скорится с делителем c=3.0; мид <0.10 или >0.90 — **только двустороннее**.
- Q = min(Q_yes, Q_no) boost → норм. по всем MM → 10,080 семплов/эпоха (раз в минуту, неделя) → payout daily.
- `min_incentive_size`, `max_incentive_spread`, пулы — в CLOB/Markets API per-market.
- Пулы: EPL $10k/игру, NBA $7.7k/игру + finance/politics/culture $1M программа.
- ⚠️ Adverse selection: in-game спорт = токсичный поток. Для $1500 — котировать спокойные рынки (politics/finance long-tail), ближе к max_spread.

## INSIDERSCANNER V2 — ПРОТОКОЛ КРИСТАЛЬНОЙ ЧИСТОТЫ
Уроки бэктеста: (а) поле price ≠ market price (36% юнитов аномальны); (б) timestamp = детект, не сделка; (в) исполнимость не записывалась.
**Схема записи (каждый сигнал, БЕЗ фильтров на записи):**
1. `block_ts` (из чейна) + `detect_ts` (локальный) — лаг меряется по каждой строке.
2. Идемпотентность: PK = (tx_hash, log_index).
3. **Снапшот стакана CLOB /book в момент детекта** (top-10 bid/ask, raw JSON) + рассчитанные: best_ask, executable fill px для $100/$300/$1000 (проход по ask).
4. Gamma-метаданные: РЕАЛЬНЫЙ slug, neg_risk, category, endDate, liquidity, vol24h.
5. Кошелёк: predictions, age, funding source + funded_gap (tracer уже есть).
6. Heartbeat-таблица (раз в минуту) → дыры в аптайме видны; без них месяц-окна не валидны.
7. Алерт-кулдауны — только на уровне Telegram, на уровне DB пишется ВСЁ.
**Пре-регистрация (зафиксировано ДО сбора, менять нельзя):**
- Гипотеза: non-sports, executable best_ask < 10¢, depth ≥ $200 в пределах 1.2× цены сделки, usd_size ≥ $700.
- Unit = (market, side), вход = записанный executable px ($300-уровень), flat stake.
- Критерий успеха: edge ≥ 3σ (binomial) И ROI > 0 при n ≥ 120 юнитов (~3 мес по статистике марта-апреля: ~43/мес).
- Один промежуточный взгляд на n=60 с порогом 3.5σ; иначе не подглядывать.
- Контроль: параллельно считать тот же критерий на заведомо шумовом срезе (<$700) — должен дать 0.

## DN-ПАРЫ FARMING (приоритет)
1. Ondo(маker)↔Ostium на XAU (золото торгуется почти 24/5, меньше weekend-гэп чем у акций): cost ≈ 1.5+1.5(Ondo RT maker)+4(Ostium open)+rollover ≈ **7-9 bps/RT** + двойные награды.
2. Pacifica↔HL (крипта): cost ≈ 2×0.75+2×1.5 = **4.5 bps/RT maker** — дешевле нашего старого HL↔Lighter в 3 раза.
3. Paradex — добавить после замера fee.
Метрика go/no-go per venue: cost-of-volume (bps) vs награда-на-$объёма; считает farm_stats.py.

## LINKS
[[research_2026-06-10_monetization]] · [[insider_scanner_verdict]] · [[000_index]]
