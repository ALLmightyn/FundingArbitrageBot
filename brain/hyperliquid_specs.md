---
Type: Technical Spec
Status: Verified 2026-05-03
Linkage: [[000_index]]
---

# Hyperliquid API & Funding Specs (Verified)

## Fees (Base Tier, no rebate)

| Leg        | Maker   | Taker   |
|------------|---------|---------|
| Perp       | 0.015%  | 0.045%  |
| Spot       | 0.040%  | 0.070%  |

**Round-trip (4 legs, all maker):** 0.110%
**ALO cancel = $0** — только fill тригерит комиссию.

Maker rebate тиры недоступны на $1,500 (требуют $50–100M+ maker volume/14 дней).
HYPE staking discount: нужно ≥10 HYPE (~$300), нерентабельно на нашем капитале.

## Order Types (Python SDK)

- `tif="Alo"` — Post-Only. Cancel вместо taker cross. Наш primary режим.
- `tif="Ioc"` — IOC taker. Только для emergency cover.
- `reduce_only=True` — Reduce-Only флаг в order object.
- `exchange.order(asset, is_buy, size, price, order_type, reduce_only=False)`

## Rate Limits

- REST: 1200 weight/min per IP
- `l2_snapshot`, `clearinghouseState`: weight=2 каждый
- `meta_and_asset_ctxs`: weight=20
- На нашем профиле (3 positions, 60s scan): ~50–80 req/min. С большим запасом.

## Price Tick Sizes (критично!)

HL требует цены кратные тик-сайзу. Формула для round_price():
```python
round(px, max(0, 5 - len(str(int(px)))))
```
| Asset | Price Range | Tick | Decimals |
|-------|-------------|------|----------|
| BTC   | ~$79,000    | $1.0 | 0        |
| ETH   | ~$2,300     | $0.1 | 1        |
| SOL   | ~$150       | $0.01 | 2       |
| HYPE  | ~$20        | $0.001 | 3      |

**Хардкод `round(px, 1)` = баг для BTC.**

## Size Decimals (szDecimals)

Из meta["universe"][i]["szDecimals"]. Кешируется в HLClient._sz_decimals.
- BTC: 4 (0.0001 BTC min)
- ETH: 4 (0.0001 ETH min)

## Funding Mechanics

- Период: каждый час (1/8 от 8h-эквивалентной ставки).
- Cap: 4%/час (extreme scenario).
- Formula: `F = avg_premium + clamp(0.01% - premium, -0.05%, +0.05%)`
- Premium sampling: каждые 5 секунд в течение часа → TWAP.

## Predicted Fundings API

Endpoint: POST `/info` с `{"type": "predictedFundings"}`
SDK: `info.post("/info", {"type": "predictedFundings"})`
НЕТ метода `info.predicted_fundings()` в SDK v0.23!

**Формат ответа:**
```python
[[asset_name, [[venue_name, {fundingRate: str, nextFundingTime: int}], ...]], ...]
# Нужный venue: "HlPerp"
```
Парсинг:
```python
for entry in data:
    coin, venues = entry[0], entry[1]
    for venue_name, venue_data in venues:
        if venue_name == "HlPerp":
            rate = float(venue_data.get("fundingRate", 0))
```

## meta_and_asset_ctxs

- Возвращает `[meta_dict, [assetCtx, ...]]`
- На testnet некоторые поля могут быть `None` → всегда парсить через `_f(v)` (None-safe float).
- Скипать assets где `markPx == 0.0 AND oraclePx == 0.0` (inactive/delisted).

## WebSocket

- Testnet: `wss://api.hyperliquid-testnet.xyz/ws`
- Mainnet: `wss://api.hyperliquid.xyz/ws`
- Reconnect: exponential backoff 5→60s. Testnet дропает соединения каждые ~60–120s.
- Subscriptions: `{"method": "subscribe", "subscription": {"type": "allMids"}}`

## Subaccounts & Spot Routing (mainnet TODO)

- Spot на HL использует отдельные asset ID (numeric: @1 = BTC/USDC, etc.)
- Для true delta-neutral нужен отдельный routing на spot market.
- На testnet spot leg не реализован — бот закрывает только perp при legging event.
