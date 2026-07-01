---
Type: Research / Strategy
Updated: 2026-06-06
---

# strategy_options_2026-06.md — Что делать со стратегией (deep research)

Триггер: аудит 2026-06-06 → бот стабильно −net на чурне. Запрос: глубокий ресерч + куда двигаться.

## ДИАГНОЗ (из аудита [[log]] 2026-06-06)
Бот в **худшем из двух миров**: чурнит (плохо для carry) но на крошечном размере и
низкой частоте (плохо для фарма). Net −$0.29 all-time, funding $0.11 vs fees $0.41.

Два корня в коде:
1. **persistence_gate НЕ внедрён** — предложен 2026-05-31 в [[persistence_gate]], в сканере
   нет ни max_run, ни consecutive-логики. Вход по avg×hit_ratio не отличает 8h-блок от 8
   разбросанных часов → входим в спред, который не доживает до break-even.
2. **flip-выход разрешён при `hold_h >= 0.25` (15 мин)** — `cross_venue_carry.py:825`.
   Фандинг почасовой → выход за 15 мин = 0 фандинга, чистый минус на fee. `CROSS_MIN_HOLD_HOURS=8h`
   гейтит только soft-exit, flip его обходит. Это двигатель чурна.

## РЫНОК (web research, июнь 2026)
- **Чистый carry потолок 10-30% APR** (подтверждает [[yield_landscape]]). На $2k = $200-600/год.
- **Hyperliquid Season 3 LIVE СЕЙЧАС.** ~38.9% HYPE supply зарезервировано community. HyperCore
  (perp+spot) volume = главный драйвер поинтов. Delta-neutral явно одобрен. **Бот уже торгует HL.**
- **Lighter Season 3 не анонсирован**, но 25% ecosystem зарезервировано под будущие сезоны.
  Поинты копятся через organic API-трейдинг. **Sharpe-weighted** (консистентность > raw volume),
  wash/self-trade фильтруется. Наш delta-neutral = высокий Sharpe = хорошо ложится.
- Pre-token венчи: **Reya (zero fees!)**, Vest, Paradex (20% airdrop), Ethereal (Ethena).
  API/автоматизация не подтверждены — нужно проверять напрямую перед заводом капитала.

## ТРИ ПУТИ

### Путь 1 — Починить carry-движок (остановить кровь)
Цель: хотя бы break-even / скромный плюс на чистом carry.
- Внедрить persistence gate: вход только если Σspread за max-серию-подряд ≥ fee×1.3.
- Убить flip-выход на 15 мин: не выходить по флипу пока realized funding < round-trip fee
  ИЛИ пока не собран ≥1 settlement. Floor эффективного hold ~4h (break-even при entry 0.008%/h).
- Confirmation-окно на флип (флип ≈ шум, ревертит — см. ZK/MON 3× churn).
Потолок апсайда: 10-30% APR = $200-600/год на $2k. Низкий абсолют.

### Путь 2 — Перенацелить движок на points/airdrop farming (РЕКОМЕНДУЮ к обсуждению)
Метрика меняется полностью: **points-per-fee-dollar + Sharpe**, а не net carry.
Малый отрицательный carry = себестоимость фарма, ОК если < ценности поинтов.
- HL S3 live + Lighter S3 likely — бот уже на обеих. Каждый $ объёма «покупает» поинты.
- Движок надо развернуть: сейчас минимизирует объём (1 поз, долгий hold). Фарму нужен
  максимум delta-neutral объёма при контроле fee-burn и высоком Sharpe.
- Это рычаг C из [[yield_landscape]] — то, на чём реально поднимали в 2025.
Апсайд асимметричный (HYPE — ликвидный дорогой токен), риск — lottery + окно.

### Путь 3 — Гибрид (default)
Carry-фиксы (Путь 1) как база «не теряем», поверх — учёт фарма: считать volume/Sharpe
по обеим биржам, не выходить если позиция набирает farm-объём дёшево. Carry не обязан быть
плюсовым — он должен быть менее отрицательным, чем ценность поинтов.

## РЕШЕНИЕ ЮЗЕРА (2026-06-06): Путь 3 — ГИБРИД. «делай все если поможет заработать».

## СДЕЛАНО 2026-06-06 (ждёт `pm2 restart 0` чтобы вступило в силу)
- [x] Анти-чурн flip: `cross_venue_carry.py:825` два уровня (mild 1.5h floor / deep ×3 сразу).
      Константы CROSS_FLIP_MIN_HOLD_HOURS=1.5, CROSS_FLIP_DEEP_MULT=3.0.
- [x] Persistence gate ВНЕДРЁН: `spread_scanner.py` считает max_run/run_earn (серия подряд),
      фильтр входа требует run_earn ≥ FEE_CROSS_ROUND_TRIP×PERSISTENCE_FEE_MULT(1.3).
- [x] Фарм-оптика: `scripts/farm_stats.py` (read-only) — volume/венчу, Sharpe, cost-of-volume bps.
      Базлайн 2026-06-06: 25 циклов / 9.8д → vol $2.5k, cost 1.16bps, carry Sharpe −16.9, −0.5%/yr.

## NEXT (опционально)
- [ ] Наблюдать после рестарта: упал ли spread_flip-чурн, появились ли net+ циклы.
- [ ] Если фармим осознанно — поднять размер ($25→$150+) для большего points-объёма ПОСЛЕ
      подтверждения что cost-of-volume приемлем (farm_stats.py).

## LINKS
- [[000_index]] · [[yield_landscape]] · [[persistence_gate]] · [[log]] · [[current_strategy]]
