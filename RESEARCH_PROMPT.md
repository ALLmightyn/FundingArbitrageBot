TASK: Comprehensive Crypto Automation Research
WHO YOU ARE

You are an independent researcher and monetization systems architect. Your task is not to improve the existing bot or continue the old strategy. You are looking at a clean slate.

I have:

    Capital: $1,500 (I can inject more if the strategy proves itself)

    Skills: Programming in any language — Python, JavaScript/TS, Rust, Go, Solidity — without limitations

    Infrastructure: Linux VPS, already running async Python framework for exchange trading

    Time: I can dedicate time to this

THE ONLY RULE

No massive starting capital required. Discard strategies that require $100k+, or where the minimum starting capital is > $10k. Everything else is open.
PHASE 1 — READ CONTEXT (10 mins)

Read these files to understand what has already been tried and WHY it failed:
code Code

Read /root/projects/HLCarryBot/brain/000_index.md
Read /root/projects/HLCarryBot/brain/strategy_options_2026-06.md
Read /root/projects/HLCarryBot/brain/yield_landscape.md
Read /root/projects/HLCarryBot/brain/log.md          (first 15 lines are enough)
Read /root/projects/HLCarryBot/info.md
Read /root/projects/HLCarryBot/news.md

Also, here are my old projects that did not realize their potential:
/root/projects/InsiderScanner
/root/projects/MarketMakerBot

The core reason for the old bot's failure in one sentence: the round-trip fee (

        
0.49)systematicallyexceededthecollectedfunding(
0.49)systematicallyexceededthecollectedfunding(

      

0.14) due to frequent flip-exits — a structural issue, not a code bug.
PHASE 2 — BROAD RESEARCH (Core Work)

Investigate ALL listed categories. For each one, perform WebSearch + WebFetch of documentation. Do not stop at the first results. Look for specific projects, APIs, and figures.
BLOCK A — On-chain / DeFi Mechanics

A1. Liquidation bots

    How liquidations work on Aave, Compound, GMX, dYdX, Hyperliquid, Drift

    What is needed: capital, speed, competition in 2026

    Search: "liquidation bot 2026 profitability aave compound"

    Real cases: how much they earn, how competitive it is

A2. DEX arbitrage (price-based, not funding-based)

    Price of a single token on Uniswap vs Curve vs Raydium — difference = profit

    Tools: mev-boost, Flashbots, Jito (Solana)

    Search: "dex arbitrage bot 2026 profitable small capital"

    Flash loans: borrow $1M without collateral for a single transaction

A3. MEV (Maximal Extractable Value)

    Sandwich attacks, frontrunning, backrunning

    Searcher bots on Ethereum/Solana

    Search: "MEV searcher bot beginner 2026 ethereum solana"

    How accessible is this for a beginner with $1,500?

A4. Yield aggregators / vault automation

    Auto-compounding: Yearn, Beefy, analogs on new networks

    Write your own vault on a new chain where there is no competition

    Search: "yield aggregator new chain 2026 opportunity"

A5. Lending rate arbitrage

    Borrow cheap on one protocol, lend high on another

    Protocols: Aave, Morpho, Euler, Kamino (Solana), MarginFi

    Search: "lending rate arbitrage defi 2026 automated"

A6. LP fee capture + hedging

    Become an LP in a concentrated liquidity pool (Uniswap v3/v4, Meteora)

    Hedge impermanent loss via perps

    Real yield after IL and gas

    Search: "uniswap v3 lp hedging strategy automated 2026"

BLOCK B — CEX/DEX Arbitrage and Market Making

B1. CEX↔DEX arbitrage

    Binance/OKX price vs Uniswap/Hyperliquid — gap = profit

    Tools, APIs, latency requirements

    Search: "cex dex arbitrage bot 2026 python"

B2. Market making on low-liquidity pairs

    Become an MM on a new listing, capture bid-ask spread

    How this is done on HL, Lighter, GRVT

    Search: "market making bot small cap tokens 2026"

    Zero/negative maker fees on these exchanges = advantage

B3. Statistical arbitrage (pairs trading)

    BTC/ETH/SOL correlation — trading deviations

    Independent of funding rate, works regardless of market direction

    Search: "crypto pairs trading statistical arbitrage bot 2026"

B4. Funding rate arbitrage (expanded)

    NOT just HL↔Lighter — look at ALL exchanges

    Including CEX: Binance, Bybit, OKX — they have huge funding rates

    CEX maker fee ~1-2 bps vs our 15 bps = different math

    Search: "funding rate arbitrage binance bybit bot 2026"

BLOCK C — Airdrop / Points Farming (Automatable)

C1. Programmatic airdrop farming

    Which projects reward on-chain actions via API (not UI clicks)

    Search: "airdrop farming api automation 2026 perp dex"

    Specifically: Paradex, GRVT, Variational, Reya, Ondo, Ethereal — who has an API?

C2. Multi-account volume farming

    Create N sub-accounts, cycle volume between them (if allowed by rules)

    Which exchanges allow sub-accounts via API

    Risks: ban, sybil detection

C3. Lending/borrowing for points

    Some projects give points for any on-chain activity (depositing, borrowing)

    Automate looping operations

    Search: "defi points farming automated 2026 lending"

BLOCK D — Niche and Unconventional

D1. NFT sniping

    Buy NFTs below floor price at listing moment

    Blur, OpenSea, Magic Eden — is there an API for automated monitoring?

    Search: "nft sniping bot 2026 profitable blur opensea"

D2. Prediction markets

    Polymarket, Drift prediction markets — automated MM or arbitrage

    Inefficiencies in probability pricing

    Search: "polymarket arbitrage bot automated 2026"

D3. On-chain data → trading signal

    Monitoring large wallets (whale tracking), reacting ahead of the market

    Monitoring the mempool for front-running opportunities

    Search: "on-chain alpha trading bot whale tracking 2026"

D4. Perpetual options (niche protocols)

    Lyra, Dopex, Panoptic — automated strategies

    Sell volatility via options (analogous to covered calls)

    Search: "defi options automated strategy 2026"

D5. Tokenized Real-World Assets (RWA) arbitrage

    Ondo USDY, BlackRock BUIDL trading at a discount/premium

    Arbitrage between on-chain and off-chain price

    Search: "rwa arbitrage automated ondo usdy 2026"

D6. Copy trading infrastructure

    Build a bot that copies top traders via API

    HL, dYdX — public leaderboards with positions

    Search: "copy trading bot hyperliquid leaderboard 2026"

D7. Telegram / Discord bots as a product

    Sell signals, trading assistants, portfolio trackers

    Monetization via subscription rather than trading

    Search: "crypto trading telegram bot business 2026 revenue"

BLOCK E — Solana Specifics (Different Stack, High Potential)

E1. Jito MEV (Solana)

    Jito tips, bundle transactions — Solana MEV specifics

    Search: "jito mev bot solana 2026 beginner"

E2. Raydium / Orca LP + farming

    Concentrated liquidity on Solana, rewards in tokens

    Search: "raydium concentrated liquidity bot 2026"

E3. Pump.fun / new listings

    Sniping new tokens at creation time

    Search: "pump fun sniper bot 2026 profitable"

And explore any other strategies where automation can generate profit that are not in these lists as well.
PHASE 3 — SYNTHESIS AND RECOMMENDATIONS

After researching, deliver a structured analysis:
For each identified strategy (top 8 by potential):
code Code

### [Strategy Name]
**Category:** A1/B2/etc.
**Mechanics:** 2-3 sentences explaining how it works
**Requirements:**
  - Capital: $X minimum
  - Language/stack: Python / Rust / Solidity / etc.
  - Implementation complexity: 1-5 (1=simple, 5=complex)
  - Time to launch: ~X days/weeks
**EV estimate:** $X/month on $1,500 (or % APR)
**Real competition:** high / medium / niche
**API/documentation:** [link]
**Key risk:** ...
**Why NOW:** urgency, window of opportunity

Summary Table (All 8+ strategies):
#	Strategy	EV $/month	Complexity	Urgency	Recommend?
TOP 1 CHOICE: [what to do right now and why]
TOP 2 BACKUP: [what to build in parallel / next]
WHAT WAS REJECTED AND WHY: [briefly for each block]
CRITICALLY IMPORTANT

    Do not obsess over the HL↔Lighter funding carry — this is a dead end, already proven.

    Do not limit yourself to DEX farming — look broader.

    If the strategy requires a CEX (Binance/Bybit) — that is OK, not just DEXs.

    If Rust or Go is needed for latency — that is OK, I can write it.

    Actively use WebSearch — do not rely on 2024 training data; the market has changed significantly.

    Benchmark for comparison: passive GLP on GRVT = ~$143/year on $1,500. An active strategy must beat this.

    Honesty is more important than optimism: if a strategy is competitively dead, say so directly.

BEGIN

First, read brain/ (Phase 1), then perform a WebSearch for each block A-E (Phase 2), and finally run the synthesis (Phase 3). Work autonomously; do not ask for confirmation.