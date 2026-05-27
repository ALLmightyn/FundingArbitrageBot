"""core/constants.py — All tunable parameters for the HL Funding Carry strategy."""

# ── Funding thresholds (PROD BETA — 2026-05-04) ──────────────────────────────
# Entry threshold: 0.025%/h. Round-trip fee floor (worst-case spot taker on entry):
#   0.015 (perp maker) + 0.070 (spot taker) + 0.015 (perp maker) + 0.040 (spot maker)
#   = 0.140% of notional. At 0.025%/h funding → break-even ≈ 5.6h hold.
FUNDING_ENTRY_THRESHOLD: float = 0.00025  # 0.025%/h

# Soft exit: drop below this and we are no longer covering ongoing fee amortization.
FUNDING_EXIT_SOFT: float = 0.00010        # 0.010%/h

# Hard exit: funding flipped negative — we'd be PAYING short funding, immediate exit.
FUNDING_EXIT_FLIP: float = -0.00005       # -0.005%/h (small dead-zone to ignore noise)

# Minimum hold-period funding average to keep the position open.
FUNDING_HOLD_MIN: float = 0.00010         # 0.010%/h

# ── Hold time limits ─────────────────────────────────────────────────────────
# At 0.025%/h on $300, hourly carry = $0.075. Worst-case round-trip fees ≈ $0.42.
# Need ≥ 6h hold to outpace fees with margin. Cap at 48h to bound funding-flip risk.
MIN_HOLD_HOURS: int = 8
MAX_HOLD_HOURS: int = 48

# ── Position sizing ──────────────────────────────────────────────────────────
# Active carry capital (margin + reserve). Sized for one $300 leg with 3× leverage:
# perp margin = $100, spot lockup = $300, plus drawdown buffer.
ACTIVE_CAPITAL_USD: float = 500.0

# Max concurrent carry positions
MAX_POSITIONS: int = 1

# Per-position notional per leg ($300 spot + $300 perp short = $600 hedged notional).
# Larger size amortizes fixed fee drag and tick-size noise.
POSITION_SIZE_USD: float = 300.0

# Perp leverage (isolated margin) — 3× for liquidation headroom
PERP_LEVERAGE: int = 3

# ── Risk thresholds (Iron Dome) ───────────────────────────────────────────────
# Delta drift: if |spot_value - perp_notional| / position > this → rebalance
DELTA_DRIFT_PCT: float = 0.02            # 2% drift triggers rebalance

# Margin ratio danger zone (maintenance_margin / account_value)
# At 50% of liquidation distance → deleverage 33%
MARGIN_RATIO_WARN: float = 0.50
# At 75% → emergency flat (taker close everything)
MARGIN_RATIO_EMERGENCY: float = 0.75

# Circuit breaker: 3 consecutive legging events in 1 hour → kill switch (4h cooldown)
LEGGING_EVENT_LIMIT: int = 3
LEGGING_WINDOW_SECONDS: int = 3600
KILL_SWITCH_COOLDOWN_SECONDS: int = 14400   # 4 hours

# Max single-event loss cap (hard stop)
MAX_SINGLE_LOSS_USD: float = 5.00

# ── Maker Chase params ───────────────────────────────────────────────────────
MAKER_CHASE_MAX_REPOSTS: int = 15        # Max price-chases before waiting
MAKER_CHASE_REPOST_DELAY_S: float = 2.0  # Seconds between repost checks
MAKER_CHASE_TIMEOUT_S: float = 30.0      # Total time before taker fallback on cover leg
MAKER_EXIT_TIMEOUT_S: float = 120.0      # Total time for maker exit before taker fallback
SPOT_TAKER_FALLBACK_S: float = 20.0      # Spot maker timeout before emergency taker buy (naked short prevention)

# ── Funding scanner ──────────────────────────────────────────────────────────
SCAN_TOP_N_ASSETS: int = 30              # Number of perps to scan
SCAN_INTERVAL_SECONDS: int = 60          # Poll predicted fundings every 60s
MARGIN_POLL_INTERVAL_SECONDS: int = 5    # clearinghouseState poll frequency

# ── Cooldown after full exit ─────────────────────────────────────────────────
EXIT_COOLDOWN_SECONDS: int = 300         # 5 min cooldown before re-entering same asset

# ── Fee constants (verified 2026-05-03) ─────────────────────────────────────
FEE_PERP_MAKER: float = 0.00015         # 0.015%
FEE_PERP_TAKER: float = 0.00045         # 0.045%
FEE_SPOT_MAKER: float = 0.00040         # 0.040%
FEE_SPOT_TAKER: float = 0.00070         # 0.070%
FEE_ROUND_TRIP_ALL_MAKER: float = 0.00110   # sum of all 4 maker legs

# ── HL API endpoints ─────────────────────────────────────────────────────────
HL_REST_URL: str = "https://api.hyperliquid.xyz"
HL_WS_URL: str = "wss://api.hyperliquid.xyz/ws"
HL_TESTNET_REST_URL: str = "https://api.hyperliquid-testnet.xyz"
HL_TESTNET_WS_URL: str = "wss://api.hyperliquid-testnet.xyz/ws"

# ── State machine states ─────────────────────────────────────────────────────
STATE_SCAN = "SCAN"
STATE_QUALIFY = "QUALIFY"
STATE_ENTER_MAKER = "ENTER_MAKER"
STATE_HOLD = "HOLD"
STATE_EXIT_MAKER = "EXIT_MAKER"
STATE_COOLDOWN = "COOLDOWN"
STATE_KILLED = "KILLED"

# ── Lighter API endpoints ─────────────────────────────────────────────────────
# REST queries use /api/v1 path; SDK SignerClient appends it itself, so pass
# the bare host for the SDK to avoid /api/v1/api/v1/nextNonce double-path.
LIGHTER_BASE_URL: str = "https://mainnet.zklighter.elliot.ai/api/v1"
LIGHTER_SDK_URL: str  = "https://mainnet.zklighter.elliot.ai"
LIGHTER_EXPLORER_URL: str = "https://explorer.elliot.ai/api"

# ── Cross-Venue Spread Thresholds ─────────────────────────────────────────────
# Total round-trip cost = 2 × HL_perp_maker (0.015%) + 2 × Lighter_maker (0.0%)
#   = 0.030% of notional.
# At SPREAD_ENTRY_THRESHOLD=0.0001/h on $300: $0.03/h revenue.
# With MIN_HOLD=4h: $0.12 revenue > $0.09 fees → +$0.03 net.
# ── Spread entry/exit thresholds ─────────────────────────────────────────────
# Cost breakdown per round-trip:
#   HL perp maker entry:  0.015%
#   HL perp maker exit:   0.015%
#   Lighter maker entry:  0.000%
#   Lighter maker exit:   0.000%
#   Bid-ask slippage est: 0.020%  (conservative, mainly on Lighter)
#   ----------------------------------
#   Total worst-case:     0.050%
#
# At SPREAD_ENTRY_THRESHOLD = 0.0003/h and $300 notional:
#   Hourly income: 0.0003 * $300 = $0.09/h
#   Total costs (0.050%): $0.15
#   Break-even: 0.15 / 0.09 = 1.67h  → safe with MIN_HOLD=4h
#
# The 0.0001/h threshold was too low — the "fake cluster" assets (HL=0.010%,
# LT=0.0096%, spread=0.0004%/h) would break even at 75h but MAX_HOLD=72h
# → guaranteed loss. 0.0003/h clears this cleanly.
SPREAD_ENTRY_THRESHOLD: float = 0.00030    # 0.030%/h minimum spread (after slippage buffer)
SPREAD_EXIT_THRESHOLD: float  = 0.00010    # 0.010%/h — soft exit (maker close)
SPREAD_EXIT_FLIP: float       = -0.00005   # -0.005%/h — hard flip (taker close immediately)

# Maximum allowed bid-ask spread on Lighter order book before entry.
# If Lighter's top-of-book spread is wider than this, skip the asset
# (indicates thin market, slippage would exceed profit).
MAX_LIGHTER_BOOK_SPREAD_PCT: float = 0.003  # 0.3% max bid-ask spread

# Cross-venue hold time
CROSS_MIN_HOLD_HOURS: int = 4
CROSS_MAX_HOLD_HOURS: int = 48   # reduced from 72: funding rates can reverse in days

# Cross-venue position sizing
# ── TEST MODE ($100/side) ────────────────────────────────────────────────────
# $25/leg keeps HL margin ~$8-12 (2-3× lev) and covers min order sizes on most
# assets (ETH 0.01=~$25, SOL 0.1=~$15, TAO 0.01=~$4). Raise to $150+ for live.
CROSS_POSITION_SIZE_USD: float = 25.0      # per-leg USD notional  ← TEST
CROSS_MAX_POSITIONS: int = 1               # max concurrent pairs  ← TEST

# Minimum Lighter free balance required before opening a new position
# (protects against over-leveraging Lighter side)
MIN_LIGHTER_FREE_BALANCE_USD: float = 20.0  # ← TEST (was 150)

# Rebalancing alerts (monitor only, user manually bridges USDC)
REBALANCE_MARGIN_WARN: float      = 0.40   # 40% margin usage — warn user
REBALANCE_MARGIN_EMERGENCY: float = 0.65   # 65% — urgent alert, close position if not resolved

# Lighter fee structure (0% for standard accounts)
FEE_LIGHTER_MAKER: float = 0.0
FEE_LIGHTER_TAKER: float = 0.0

# Cross-venue round-trip cost: 2 × HL_perp_maker + slippage estimate
FEE_CROSS_ROUND_TRIP: float    = 0.00030   # 0.030% hard fees (HL maker only)
SLIPPAGE_ESTIMATE: float       = 0.00020   # 0.020% estimated Lighter slippage
FEE_CROSS_TOTAL_ESTIMATE: float = 0.00050  # 0.050% total worst-case cost

# Only trade these assets — liquid perps available on both HL and Lighter
# (prevents bot from entering thin-book alts where slippage kills the trade)
# Note: MAX_LIGHTER_BOOK_SPREAD_PCT is a second-line filter for thin books.
# Whitelist catches assets without reliable Lighter markets entirely.
CROSS_VENUE_WHITELIST: set = {
    # Large-caps
    "BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX",
    "LINK", "DOT", "NEAR", "AAVE", "BCH", "XMR", "LTC", "ATOM",
    "UNI", "ARB", "OP", "SUI", "APT", "INJ", "TIA", "FIL",
    "MKR", "COMP", "CRV", "TAO", "PENDLE",
    # Mid-caps with consistent Lighter liquidity (verified 2026-05-27)
    "XLM", "WLD", "ZEC", "1000PEPE", "TRUMP",
    # Commodity & FX perps (active on both venues)
    "WTI", "BRENTOIL", "USDJPY",
    # Recent high-spread assets — confirmed >0.03%/h spread, book spread <0.3%
    "0G", "FOGO", "STABLE", "TSM",
}

# Scan interval for cross-venue spread scanner
SPREAD_SCAN_INTERVAL_S: int = 60

# Cooldown after cross-venue exit
CROSS_EXIT_COOLDOWN_S: int = 300

# ── Anti-spike entry filter (borrowed from Gajesh2007/funding-arb-bot) ───────
# Reject entry if current spread > rolling_mean + N*sigma — funding spike
# is likely to revert before MIN_HOLD elapses, leaving you holding the bag.
SPIKE_SIGMA_THRESHOLD: float = 2.0       # 2 standard deviations
SPIKE_HISTORY_MIN_SAMPLES: int = 12      # ~12 min at 60s scan interval
SPIKE_HISTORY_MAX_SAMPLES: int = 1440    # ~24h rolling window at 60s

# ── Price divergence guard ────────────────────────────────────────────────────
# If HL mid and Lighter mid for the same asset diverge by > this %, one venue's
# oracle is broken or there is a real arb that the funding model cannot model.
# Exit immediately (taker) to avoid getting stuck with a non-hedged book.
PRICE_DIVERGENCE_KILL_PCT: float = 0.02   # 2% mid-price divergence triggers taker exit

# ── Portfolio drawdown kill switch ────────────────────────────────────────────
# If cumulative session P&L drops below this floor, halt all new entries and
# close existing positions (maker first, taker if maker times out).
# Distinct from CB2 SESSION_LOSS_FLOOR in main_cross.py — this is a softer
# strategy-internal kill that does NOT terminate the bot, only pauses entries.
PORTFOLIO_DRAWDOWN_KILL_USD: float = 4.0  # halt if session P&L < -$4 (~2% of $200 capital, TEST)

# ── Position drift verification ───────────────────────────────────────────────
# Periodically reconcile that |HL_short_size + Lighter_long_size| ≈ 0 (units).
# If actual sizes diverge from booked, alert and attempt auto-rebalance.
DRIFT_CHECK_INTERVAL_S: int = 300        # check every 5 min
DRIFT_TOLERANCE_PCT: float = 0.05        # tolerate 5% deviation per leg
