---
Type: Research / Strategy
Updated: 2026-06-10
---

# research_2026-06-10_monetization.md — Broad-research: куда направить $1500 + движок

Триггер: RESEARCH_PROMPT.md (clean slate, все категории A-E). Carry HL↔Lighter подтверждён мёртвым ([[log]] 2026-06-07: fees $0.49 vs funding $0.14).

## ВЕРДИКТ (top-2)

**TOP-1: Мульти-venue delta-neutral farming на новых перп-DEX с live-наградами.**
- **Ondo Perps** — запуск 9 июня 2026 (вчера), **$100k USDC/неделю** реальным кэшем (payout каждую среду, +2 дня после недели). Не ретро-лотерея. Нужен invite-код (волны waitlist). Не для US. Ethereum-based. API не подтверждён — проверить первым делом.
- **Pacifica** (Solana, #1 перп Solana) — 500k pts каждый четверг, пропорционально notional volume. Официальный Python SDK: github.com/pacifica-fi/python-sdk. Pre-TGE, окно открыто.
- **Paradex** — Season 3 live (с 1 фев 2026), 4M XP/нед. S2 дал 20% supply, TGE $DIME уже прошёл (фев-март) → S3 фармится под следующие распределения. Официальный paradex-py SDK.
- **Variational / Reya (zero fees) / Ethereal** — weekly points, API частично, проверять.
- Sybil-правила: wash-trade между своими кошельками НЕЛЬЗЯ; честный delta-neutral против стакана через 2 разных DEX — общепринято и ОК. Consistency > burst.
- Экосистема подтверждена: существуют публичные мульти-DEX тулкиты (hypurrquant/perp-cli: Pacifica+HL+Lighter; perp-dex-toolkit: EdgeX/Backpack/Paradex/Aster/Lighter/GRVT) — паттерн рабочий, наш движок это уже умеет.

**TOP-2 (бэкап): Реанимация Polymarket-инфры (MarketMakerBot + InsiderScanner).**
- 14 из 20 топ-кошельков Polymarket — боты. Структурный арб (YES+NO < $1, NegRisk, logical arb между коррелированными рынками) жив: 1.5-3%/сделку, MM 2-5%/мес при 70-80% win-rate.
- Кейс: бот на BTC-15min рынках сделал $764 за 48ч (дек 2025). Кейс: Claude-бот $1000→$14216 за 48ч (не экстраполировать, но рынок неэффективен).
- Простые вилки сжаты до 2.7с/0.3% — нужны logical/структурные, не наивные.
- Kalshi-кросс: спреды 2-5%, но Kalshi fee 7% (до 1% на объёме) + резолюция может разойтись.

## ЧИСЛА ПО КАТЕГОРИЯМ (что отброшено и почему)

| Кат. | Стратегия | Вердикт | Причина |
|---|---|---|---|
| A1 | Ликвидации Aave/Compound | ✗ | сотни ботов на позицию, выигрывает custom-инфра; flash-loan снимает капитал-барьер, но не латенси-барьер |
| A2 | DEX-арб/flash loans | ✗ | retail-боты выдавливаются за дни; Flashbots/private mempool обязательны |
| A3 | MEV ETH/SOL | ✗ | winner-take-most; ETH: 90% revenue уходит builder'ам; SOL: $1.58/tx, RPC $1.8-3.8k/мес, tips 50-60% профита |
| A4 | Yield-агрегатор свой | ✗ | строить vault ради $1500 TVL бессмысленно |
| A5 | Lending rate arb | ✗ | спреды сжимаются мгновенно, на $1500 < gas+время |
| A6 | LP v3 + перп-хедж | ✗ | 51% LP в минусе; реальный net 3.6-13% APY при активном менеджменте ≈ benchmark |
| B1 | CEX↔DEX price arb | ~ | жив, но латенси-гонка; ниже farming по EV/усилию |
| B2 | MM на мелких парах | ~ | возможен на HL/Lighter (0 maker fee), но adverse selection на тонких книгах; инструмент, не стратегия |
| B3 | Pairs trading | ✗ | нет статистического edge против проф-десков, не risk-free |
| B4 | Funding arb HL↔CEX | ~ | СТРУКТУРНО лучше HL↔LT: CEX maker 1-2bps, break-even спред ~1.3bps/8h; net 3-12% majors / 20-60% long-tail. Синергия с движком, но потолок ≈ carry |
| C1-C3 | Points farming | ✓ TOP-1 | см. выше |
| D1 | NFT-снайпинг | ✗ | золотая эра закончилась, рынок институционализирован |
| D2 | Prediction markets | ✓ TOP-2 | см. выше |
| D3 | Whale tracking | ✗ | сигнал запаздывает → ты exit liquidity |
| D4 | DeFi options/DOV | ✗ | пассивный депозит, доходность ≈ benchmark, capital-inefficient |
| D5 | RWA NAV-arb | ~ | реален (oracle lag, NAV gap 0.1-0.5%), но низкочастотный; weekly-возможности, не масштаб для бота на $1500 |
| D6 | Copy-trading HL | ✗ | slippage + selection lemon-трейдеров; как продукт — см. D7 |
| D7 | TG-бот как бизнес | ~ | сигнал-боты $3-30k/мес доказаны, dev-кост низкий, но bottleneck — дистрибуция/маркетинг, не код |
| E1 | Jito MEV | ✗ | см. A3 |
| E2 | Raydium/Meteora CLMM | ✗ | см. A6, Meteora сама автоматизирует |
| E3 | Pump.fun снайпинг | ✗ | большинство токенов умирает мгновенно, PvP против инфра-ботов |

## КЛЮЧЕВАЯ ЛОГИКА РЕШЕНИЯ
1. На $1500 проценто-доходные стратегии (carry/LP/lending: 10-60% APR) дают $12-75/мес — не оправдывают время.
2. Латенси-стратегии (MEV/ликвидации/снайпинг) требуют инфры дороже капитала.
3. Асимметрия только там, где платят за **активность, а не за капитал**: points/rewards у новых DEX (Ondo: $100k/нед на пул ранних юзеров) и неэффективности молодых рынков (Polymarket).
4. У нас готовы оба актива: delta-neutral движок (venues/*.py абстракция) и Polymarket CLOB-бот.

## LIVE API ЗОНДЫ (verified с VPS 2026-06-10)
- **Pacifica** `api.pacifica.fi/api/v1/info` → HTTP 200 без авторизации: funding rates, min order **$10**, lev до 50x. Python SDK официальный.
- **Paradex** `api.prod.paradex.trade/v1/markets` → 200: **551 рынок = 71 перп + опционы BTC/ETH**. paradex-py официальный.
- **Ondo Perps** — ЕСТЬ ПОЛНЫЙ API: `docs.ondoperps.xyz`. REST `api.ondoperps.xyz` + WS + **sandbox `api.ondoperps-sandbox.xyz`** (можно строить ДО получения invite-кода). Auth = SIWE (ERC-4361: подпись кошельком → JWT) — полностью скриптуется. Эндпоинты: /v1/perps/orders, /positions, /history, WS markPricesPerps. Рынки: NVDA, QQQ, AMD, XAU (акции/ETF/золото!).
- **Polymarket** CLOB `clob.polymarket.com` + Gamma `gamma-api.polymarket.com` → 200, открыты.
- **Ostium** (Arbitrum, USDC) — хедж-нога для стоковых перпов Ondo: 71 пара (33 акции: NVDA/TSLA/AAPL..., золото, индексы), от $5, Builder Codes для ботов, **и собственная points-программа (airdrop 2026 ожидается)**. Paradex для хеджа акций НЕ подходит (только XAG).
- **Связка-открытие: Ondo Perps ↔ Ostium delta-neutral на NVDA/XAU = двойной фарм** (USDC-кэш Ondo + поинты Ostium) при нулевой дельте. Стоимость: фи обеих сторон + funding Ostium на RWA-парах — замерить до масштабирования. Риск: выходные (рынки акций закрыты, оракулы стоят).

## ЯЗЫК/СТЕК (юзер снял ограничение «только готовый движок»)
- Farming (TOP-1): латенси не важна → **Python** (SDK готовы у всех venue), новый код или venues/-абстракция — что быстрее.
- Polymarket-арб (TOP-2): 73% арб-профита забирают боты <100ms → сканер на Python, **исполнитель на Rust** (tokio + WS) как этап 2. Есть открытые Rust-репо (TopTrenDev/polymarket-kalshi-arbitrage-bot) как референс.

## UPDATE 2026-06-11 (re-research по вопросу «прикинуть заработок фарминга»)
- **Ondo Perps:** public beta live с 10.06, доступ волнами инвайтов. Формула распределения $100k/нед НЕ опубликована (в т.ч. нет в docs.ondoperps.xyz/llms.txt). Первая выплата ср **17.06** → реальный $/объём считается только после неё. ⚠️ Твит 0xasrequired: «$100k rewards this week for **tread users**» — неделя-1 возможно идёт через терминал Tread.fi, верифицировать.
- **Pacifica:** TGE не анонсирован; поинты OTC ~$0.80/pt (сент-2025) и оценки $1.09/pt @ $10B FDV — спекуляция, в EV не закладывать.
- **Вывод:** EV фарминга = (неизвестная формула) × (неизвестная цена поинта) − (известный cost 4.5-9 bps/RT). Непросчитываем до 17.06. Polymarket тем временем имеет ИЗМЕРЕННЫЙ paper-NET +$101/д на спокойных рынках (42.5ч) — приоритет за ним.

## NEXT ACTIONS (по приоритету)
- [ ] **Ondo Perps sandbox СЕГОДНЯ**: SIWE auth + order flow на api.ondoperps-sandbox.xyz — код готов к моменту получения invite-кода
- [ ] Достать invite-код Ondo Perps (waitlist/Discord)
- [ ] Ostium: завести $200-300, builder-code интеграция → DN-пара Ondo↔Ostium (XAU или NVDA)
- [ ] Pacifica: тест-аккаунт, python-sdk, $200-300, замерить cost-of-volume (farm_stats.py)
- [ ] Paradex S3: paradex-py, аналогично
- [ ] Параллельно: аудит MarketMakerBot — NegRisk/logical арб (Python-сканер сперва, Rust-исполнитель потом)
- [ ] HL↔Lighter carry: оставить $25/leg как HL S3 farm-объём либо выключить — не масштабировать
- ⚠️ Google Drive MCP требует реавторизации (`/mcp`) — Drive-ресёрч не выполнен

## ИСТОЧНИКИ
- neuralarb.com/2026/04/24 — HL↔CEX funding arb числа
- airdrops.io/ondo-perps — Ondo Perps rewards
- docs.pacifica.fi/api-documentation/api + github.com/pacifica-fi/python-sdk
- docs.paradex.trade + github.com/tradeparadex/paradex-py
- techflowpost.com/en-US/article/30136, dropstab.com/research — perp-DEX farming ландшафт
- medium ILLUMINATION «Beyond Simple Arbitrage: 4 Polymarket Strategies 2026», finance.yahoo «Arbitrage Bots Dominate Polymarket»
- quicknode/rpcfast — Solana MEV экономика

## LINKS
[[000_index]] · [[strategy_options_2026-06]] · [[yield_landscape]] · [[log]]
