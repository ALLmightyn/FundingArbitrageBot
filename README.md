# FundingArbitrageBot

Delta-neutral funding-rate arbitrage bot for **Hyperliquid** and **Lighter** perpetuals, live on mainnet.

## What it does

Opens offsetting long/short positions across perpetual venues to harvest funding-rate
spread while staying market-neutral. Two entry points:

- `main.py` — single-venue funding carry on Hyperliquid.
- `main_cross.py` — cross-venue arbitrage between Hyperliquid and Lighter, with a live
  spread scanner and per-venue circuit breakers (API error surge, session-loss floor).

## Architecture

```
feeds/spread_scanner.py    live HL + Lighter spread scanning
strategies/                funding_carry.py, cross_venue_carry.py
venues/                    hyperliquid.py, lighter.py — exchange clients
risk/                      delta_monitor.py, iron_dome.py — position/loss guards
core/                      constants, models, logger
notifier/                  Telegram alerts
scripts/                   offline analysis (persistence, leverage audit, farm stats)
```

## Stack

Python (asyncio), `hyperliquid-python-sdk`, Lighter SDK (`zklighter-perps-python`),
SQLite (`aiosqlite`), Telegram bot API.

## Setup

```bash
pip install -r requirements.txt
# Lighter SDK isn't on PyPI — install separately:
uv pip install git+https://github.com/elliottech/zklighter-perps-python.git

cp .env.example .env   # fill in wallet keys, Telegram token, etc.
python3 main.py         # single-venue
python3 main_cross.py   # cross-venue (HL + Lighter)
```

The bot does not place any orders until started explicitly.

## Disclaimer

Trades real capital on live mainnet venues. Provided as-is for reference; no warranty.
