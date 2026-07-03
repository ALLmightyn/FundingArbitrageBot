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
LT_ENTRY_TIMEOUT_S: float = 90.0        # Lighter entry leg: longer wait since HL is hedged
LT_ENTRY_MAKER_TIMEOUT_S: float = 15.0  # Maker window before taker fallback (Lighter taker = 0%)
HL_ENTRY_MAKER_TIMEOUT_S: float = 60.0  # HL is the FIRST leg — nothing is exposed until it fills,
#   so we can afford a long maker window before the 0.045% taker fallback. Added 2026-06-05 to
#   cut taker-entry leakage (23 fallbacks in history tripled entry fee 0.015%→0.045%).
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
# At SPREAD_ENTRY_THRESHOLD = 0.00015/h and $25 notional (TEST):
#   Hourly income: 0.00015 * $25 = $0.00375/h
#   Total costs (0.050%): $25 * 0.0005 = $0.0125
#   Break-even: $0.0125 / $0.00375 = 3.33h → safe with MIN_HOLD=4h
#   Real NEAR cycle ran at 0.01155%/h → profitable → 0.00015 includes it.
# Calibrated 2026-05-29 against live 24h historical paired settlements (HL + Lighter):
#   BCH 0.0056%/h, ADA 0.0045, DOT 0.0031, APT 0.0024, BNB 0.0018, AVAX 0.0010
# Threshold 0.025%/h would block 100% of assets on a quiet market → bot idle.
# At 0.005%/h (44% APR) about half the whitelist would qualify in normal regimes,
# matching podcast practitioner baseline ("50-100% average annual yield").
# Break-even math @ $25/leg: 0.005% × $25 = $0.00125/h vs $0.0074 round-trip fees →
# 5.9h to recoup — safe with CROSS_MIN_HOLD_HOURS=8.
SPREAD_ENTRY_THRESHOLD: float = 0.00008    # 0.008%/h (~70% APR) — raised 2026-06-05 from 0.005%.
#   0.005% sat exactly on the 8h-maker breakeven (0.00375%/h) → zero margin, marginal
#   assets (ADA 24h-avg 0.0045) bled fees. 0.008% × 8h = 0.064% income vs 0.03% maker
#   round-trip → guaranteed +0.034% even if held only the 8h floor. Fewer trades, each net+.
SPREAD_EXIT_THRESHOLD: float  = 0.00002    # 0.002%/h (~17.5% APR) — exit when carry decays
SPREAD_EXIT_FLIP: float       = -0.00005   # -0.005%/h — hard flip. Widened 2026-06-05 from
#   -0.002% (≈0): the old near-zero band triggered on noise → TRX churned in/out 3× in 11h,
#   paying full fees each round. -0.005% requires a real direction reversal, not a wobble.

# Maximum allowed bid-ask spread on Lighter order book before entry.
# If Lighter's top-of-book spread is wider than this, skip the asset
# (indicates thin market, slippage would exceed profit).
MAX_LIGHTER_BOOK_SPREAD_PCT: float = 0.003  # 0.3% max bid-ask spread

# Cross-venue hold time. Min 8h ensures we cover 8 hourly settlements at the
# new 0.005%/h threshold — 8 × 0.00125 = $0.010 > $0.0074 round-trip fees, so
# even a position that stays exactly at threshold is guaranteed positive.
CROSS_MIN_HOLD_HOURS: int = 8
CROSS_MAX_HOLD_HOURS: int = 48

# Cross-venue position sizing
# ── TEST MODE ($100/side) ────────────────────────────────────────────────────
# $25/leg keeps HL margin ~$8-12 (2-3× lev) and covers min order sizes on most
# assets (ETH 0.01=~$25, SOL 0.1=~$15, TAO 0.01=~$4). Raise to $150+ for live.
CROSS_POSITION_SIZE_USD: float = 25.0      # per-leg USD notional  ← TEST
CROSS_MAX_POSITIONS: int = 1               # 1 position at a time (deliberate — single active cycle)

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
# Symbol name mapping between Lighter (canonical) and Hyperliquid.
# Lighter uses "1000X" prefix; HL uses "kX" for the same 1000× contracts.
# Keys = Lighter names, Values = HL names.
LIGHTER_TO_HL_SYMBOL: dict = {
    "1000PEPE":  "kPEPE",
    "1000SHIB":  "kSHIB",
    "1000BONK":  "kBONK",
    "1000FLOKI": "kFLOKI",
}
HL_TO_LIGHTER_SYMBOL: dict = {v: k for k, v in LIGHTER_TO_HL_SYMBOL.items()}

# Note: MAX_LIGHTER_BOOK_SPREAD_PCT is a second-line filter for thin books.
# Whitelist catches assets without reliable Lighter markets entirely.
CROSS_VENUE_WHITELIST: set = {
    # Verified 2026-06-02: all assets present on BOTH HL + Lighter with maxLeverage ≥ 5x
    # and OI > $1M on HL (liquidity filter). The scanner's 24h historical stability filter
    # + TWAP gate + sigma-spike filter protect against illiquid/spike entries — no need
    # to manually restrict. Expanding from 21 → 73 assets gives the scanner ~3.5× more
    # opportunities to find sustained carry. MAX_LIGHTER_BOOK_SPREAD_PCT (0.3%) is the
    # runtime thin-book guard; the whitelist is now just an "exists on both venues" list.

    # Original tier-1 (all 10x on HL):
    "BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX",
    "LINK", "DOT", "AAVE", "BCH", "LTC", "ATOM", "UNI", "SUI",
    "ARB", "OP", "APT", "TAO", "NEAR",

    # New tier-2 — 10x on HL, OI > $1M (highly liquid):
    "HYPE", "ZEC", "TON", "PUMP", "WLD", "PAXG", "FARTCOIN",
    "1000PEPE", "ENA", "ONDO", "TRX", "JUP", "TRUMP", "CRV",
    "1000BONK", "1000SHIB", "DYDX", "XPL",

    # New tier-3 — 5x on HL, OI > $2M (liquid enough):
    "LIT", "ASTER", "XMR", "MON", "ZRO", "WLFI", "PENDLE", "LDO",
    "VIRTUAL", "HBAR", "ICP", "EIGEN", "MNT", "WIF", "MORPHO",
    "ETHFI", "TIA", "STRK", "FIL", "POL", "SPX", "SEI", "BERA",
    "ZK", "PYTH", "KAITO", "AVNT", "1000FLOKI", "AXS",  # DUSK removed: HL 500 on funding_history
    "XLM",
}

# Scan interval for cross-venue spread scanner
SPREAD_SCAN_INTERVAL_S: int = 30

# Cooldown after cross-venue exit. Raised to 15 min to avoid bouncing into the
# same asset right after exit when its spike-then-revert pattern is still active.
CROSS_EXIT_COOLDOWN_S: int = 900

# Longer re-entry cooldown on the SAME asset after a spread_flip exit. A flip means
# the funding direction just reversed; re-entering 15 min later (the old behaviour)
# bounced straight back into the same decaying/oscillating spread and paid fees again
# (TRX in/out 3× in 11h). 4h lets the regime actually change before we touch it again.
CROSS_FLIP_REENTRY_COOLDOWN_S: int = 14400  # 4h same-asset cooldown after spread_flip

# ── Anti-churn: flip-exit hold floor (2026-06-06) ────────────────────────────
# AUDIT 2026-06-06: 15/25 closed cycles exited on spread_flip at hold≥0.25h (15 min),
# banking ≈$0 funding (hourly settlement) and paying a full round-trip fee each time →
# net −$0.29 all-time. Funding flips are mostly NOISE that reverts (ZK/MON re-opened
# within hours). Two-tier flip exit:
#   • mild flip (noise): wait CROSS_FLIP_MIN_HOLD_HOURS so a revert can save the round-trip.
#   • deep flip (real regime reversal, ≤ SPREAD_EXIT_FLIP × DEEP_MULT): exit immediately.
CROSS_FLIP_MIN_HOLD_HOURS: float = 1.5   # mild-flip hold floor before acting (was 0.25h)
CROSS_FLIP_DEEP_MULT: float      = 3.0   # ×SPREAD_EXIT_FLIP (−0.015%/h) = real reversal → exit now

# ── Persistence gate (entry) — implements persistence_gate.md (designed 2026-05-31) ──
# hit_ratio cannot tell an 8h continuous block from 8 scattered 1h hits. Break-even needs
# N CONSECUTIVE hours. Require the cumulative spread over the longest consecutive run above
# threshold to exceed the round-trip fee with margin, else the carry never reaches break-even.
PERSISTENCE_FEE_MULT: float = 1.3        # required run_earn ≥ FEE_CROSS_ROUND_TRIP × 1.3

# ── Anti-spike entry filter (borrowed from Gajesh2007/funding-arb-bot) ───────
# Reject entry if current spread > rolling_mean + N*sigma — funding spike
# is likely to revert before MIN_HOLD elapses, leaving you holding the bag.
SPIKE_SIGMA_THRESHOLD: float = 2.5       # 2.5 sigma — less aggressive rejection, fewer missed entries
SPIKE_HISTORY_MIN_SAMPLES: int = 5       # ~5 min warmup (was 12) — faster first entry
SPIKE_HISTORY_MAX_SAMPLES: int = 2880    # ~24h rolling window at 30s intervals

# ── TWAP entry confirmation gate ──────────────────────────────────────────────
# Lighter /funding-rates returns instantaneous predicted rate (current premium/8).
# Actual settlement = TWAP of 60 per-minute premiums over the hour.
# A 3-min spike at 0.04%/h with 57 min at 0.001%/h → TWAP settlement ≈ 0.003%/h.
# Gate: require the 30-min rolling mean spread to be above threshold before entry.
# 30 min covers half a settlement period — strong predictor of next TWAP.
SPREAD_TWAP_WINDOW_S: int  = 1800  # 30-min lookback for TWAP gate
SPREAD_TWAP_MIN_SAMPLES: int = 20  # ~10 min of samples at 30s scan interval (fail-closed)

# ── Historical stability filter (primary defence against fake-spike carries) ──
# Even after TWAP confirms a sustained 15-30 min spread, the asset may still
# have been a 100%/h spike that's been flat for the past 24h. We pull HL's real
# hourly TWAP settlements and require ≥60% of recent hours to have been above
# threshold in our shorting direction. If NEAR averaged 0.0007%/h for 24h with
# max 0.0013%/h, no number of TWAP-confirmed spikes makes it a real carry.
# Per podcast wisdom: "stable funding sustained over a month" — the sustainable
# pattern. NEAR live data: 0/24 settlements ≥ 0.025% → would have been rejected.
STABILITY_LOOKBACK_HOURS: int   = 24      # hours of HL settlement history to inspect
STABILITY_MIN_HIT_RATIO: float  = 0.25    # 25% of past hours must be above threshold
# Note: was 0.50, lowered to 0.25 because most good carries are episodic (OP=37%, ADA=25%).
# The TWAP gate (30 min) already guards against entering at a bad moment.

# ── Price divergence guard ────────────────────────────────────────────────────
# If HL mid and Lighter mid for the same asset diverge by > this %, one venue's
# oracle is broken or there is a real arb that the funding model cannot model.
# Exit immediately (taker) to avoid getting stuck with a non-hedged book.
PRICE_DIVERGENCE_KILL_PCT: float = 0.04   # 4% mid-price divergence. Raised 2026-06-05 from 2%:
#   the position is delta-neutral, so a 2-3% mid divergence between venues is NOT a loss
#   (leg PnL offsets) — it was usually transient quote-noise on a thin book. The old 2% +
#   taker-both-legs turned 4 neutral positions into guaranteed fee losses (ZEC twice in 27min).
#   4% keeps a safety net for a genuine oracle break; exit is now HL-maker + LT-taker, not taker-both.

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
