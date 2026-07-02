# FundingArbitrageBot

**Delta-neutral funding-rate arbitrage on Hyperliquid & Lighter — live on mainnet.**

![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![Status](https://img.shields.io/badge/status-live%20mainnet-brightgreen)
![License](https://img.shields.io/badge/license-proprietary-lightgrey)

---

## ⚠️ Disclaimer

This bot trades real capital on live perpetual futures markets. It is shared here as a **portfolio / code-sample repository**, not as an invitation to copy-trade or as financial advice. Nothing here is investment guidance; perpetual futures carry real risk of loss.

## Overview

Two independent strategies for collecting funding-rate carry with a delta-neutral book, sharing one codebase:

| Mode | Entry point | Strategy | Venues |
|---|---|---|---|
| **HL-only carry** | `main.py` | Perp short + spot long on the same exchange | Hyperliquid |
| **Cross-venue arbitrage** | `main_cross.py` | Perp short on the high-funding venue + perp long on the low-funding venue | Hyperliquid ↔ Lighter |

The cross-venue mode is the current focus: it captures the *spread* between funding rates on two exchanges rather than depending on one venue's rate staying positive, and it doesn't need a spot leg — both legs are perps, so there's no bridge/rebalancing exposure on that side.

**🟢 Status: live on mainnet.** The strategy has been iterated through multiple real trading cycles with a documented bug-fix history (see `brain/`), and the tradable-asset whitelist has been expanded from an initial 21 to 67 pairs after live verification of leverage/liquidity on both venues.

## Key features

- **Perp-first entry sequencing** — opens the hedged leg before the exposed leg, with automatic cover-on-timeout so the book is never left one-sided longer than necessary
- **Maker-first execution** — chases maker fills before falling back to taker, since round-trip maker fees are the difference between a profitable and unprofitable spread at this scale
- **Anti-churn exit logic** — a two-tier flip detector (shallow vs. deep spread reversal) prevents the bot from round-tripping in and out of a position on noise, which was a real, measured problem earlier in development
- **Persistence gate** — requires the spread to have held for a minimum consecutive-hours streak before entering, filtering out spikes that won't survive to collect meaningful funding
- **Real settlement accounting, not estimates** — funding collected is read from each venue's actual settlement API (not `rate × time`), because the naive estimate was found to overstate results by 3×+ on one venue
- **Idempotent state recovery** — restarts (deploy, crash) reconcile against the exchanges' own funding history rather than in-memory counters, so no double-counting or lost fills across restarts
- **Telegram alerting** — position lifecycle, fills, and failures are pushed live rather than requiring the operator to poll dashboards

## Tech stack

Python (async), SQLite for state and cycle history, Hyperliquid & Lighter exchange SDKs, Telegram Bot API for alerting. Deployed via `pm2` on a VPS.

## Architecture

```
feeds/spread_scanner.py, feeds/funding_scanner.py   → rank venues/assets by historical funding score
strategies/cross_venue_carry.py                     → entry/exit state machine, sign-flip exit logic
core/models.py, core/constants.py                   → typed position state, tunable thresholds
database/                                            → SQLite: cross_venue_cycles table, full cycle audit trail
notifier/                                            → Telegram alerts (position + operational)
main.py / main_cross.py                              → entry points for the two modes
```

Full parameter history, the bug-fix graveyard, and design-decision rationale are documented in `brain/` (`000_index.md` is the entry point) — kept as a living technical journal rather than static docs, updated after every meaningful change.

## Project structure

```
FundingArbitrageBot/
├── main.py                # HL-only carry entry point
├── main_cross.py           # cross-venue arbitrage entry point
├── config.py
├── core/                   # models, constants, logging
├── strategies/              # cross_venue_carry.py — the state machine
├── feeds/                   # funding & spread scanners
├── database/                 # schema + accessors
├── notifier/                 # Telegram integration
└── brain/                   # technical design log (params, bugs, research)
```

## Setup

```bash
cp .env.example .env   # fill in exchange keys, Telegram token — see comments in the file
pip install -r requirements.txt
python main_cross.py    # or main.py for HL-only mode
```

`HL_TESTNET=true` runs against Hyperliquid's testnet for a dry run before touching real funds.

## License

Proprietary — shared for demonstration purposes only. Not licensed for reuse, redistribution, or commercial use.
