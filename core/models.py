"""core/models.py — Pure domain dataclasses. Zero external dependencies."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Dict


class CarryState(str, Enum):
    SCAN        = "SCAN"
    QUALIFY     = "QUALIFY"
    ENTER_MAKER = "ENTER_MAKER"
    HOLD        = "HOLD"
    EXIT_MAKER  = "EXIT_MAKER"
    COOLDOWN    = "COOLDOWN"
    KILLED      = "KILLED"


@dataclass
class FundingSnapshot:
    """Single funding-rate observation for one asset."""
    asset: str
    funding_rate_1h: float      # Current hourly rate (e.g. 0.0001 = 0.01%/h)
    predicted_rate_1h: float    # Next-hour predicted rate
    funding_8h_avg: float       # 8-hour rolling average (derived)
    mark_price: float
    spot_price: float           # Oracle / spot index price
    basis_pct: float            # (mark - spot) / spot
    sampled_at: int = field(default_factory=lambda: int(time.time()))

    @property
    def annualized_pct(self) -> float:
        """Current funding rate as APR (%)."""
        return self.funding_rate_1h * 8760 * 100  # hourly → annual %

    @property
    def predicted_annualized_pct(self) -> float:
        return self.predicted_rate_1h * 8760 * 100


@dataclass
class CarryPosition:
    """Tracks one active spot+perp delta-neutral carry position."""
    asset: str
    state: CarryState = CarryState.SCAN

    # Sizing
    spot_size_usd: float = 0.0      # USD notional of spot long
    perp_size_usd: float = 0.0      # USD notional of perp short
    perp_leverage: int = 3
    units: float = 0.0              # Base-asset units (BTC count) — equal on both legs for delta-neutrality
    spot_pair: Optional[str] = None # HL spot pair name (e.g. "@142" for UBTC/USDC)

    # Entry fill prices
    spot_entry_price: float = 0.0
    perp_entry_price: float = 0.0

    # Order IDs (None until placed)
    spot_order_id: Optional[str] = None
    perp_order_id: Optional[str] = None

    # Timestamps
    entered_at: Optional[int] = None
    exited_at: Optional[int] = None
    cooldown_until: int = 0

    # P&L tracking
    funding_collected_usd: float = 0.0     # Accumulated funding payments received
    exit_pnl_usd: float = 0.0             # Realized PnL from entry/exit spread
    fee_paid_usd: float = 0.0

    # Risk state
    legging_events: int = 0                # Count of legging incidents this position
    margin_ratio: float = 0.0             # Last observed margin ratio
    delta_drift_pct: float = 0.0          # Last observed delta drift

    # Metadata
    entry_funding_rate: float = 0.0        # Funding rate when entered
    cycle_id: Optional[str] = None         # DB funding_cycles.id

    @property
    def hold_hours(self) -> float:
        if self.entered_at is None:
            return 0.0
        return (int(time.time()) - self.entered_at) / 3600

    @property
    def total_pnl_usd(self) -> float:
        return self.funding_collected_usd + self.exit_pnl_usd - self.fee_paid_usd

    @property
    def is_cooling_down(self) -> bool:
        return int(time.time()) < self.cooldown_until

    def mark_entered(self) -> None:
        self.entered_at = int(time.time())
        self.state = CarryState.HOLD

    def mark_exited(self, pnl: float) -> None:
        self.exited_at = int(time.time())
        self.exit_pnl_usd = pnl
        self.state = CarryState.COOLDOWN


@dataclass
class AssetState:
    """Live market data for one asset (updated by FundingScanner)."""
    asset: str
    funding_snapshot: Optional[FundingSnapshot] = None
    best_bid_perp: float = 0.0
    best_ask_perp: float = 0.0
    best_bid_spot: float = 0.0
    best_ask_spot: float = 0.0
    last_updated: int = 0

    @property
    def perp_spread_pct(self) -> float:
        if self.best_bid_perp <= 0 or self.best_ask_perp <= 0:
            return 999.0
        return (self.best_ask_perp - self.best_bid_perp) / self.best_ask_perp

    @property
    def is_stale(self) -> bool:
        return (int(time.time()) - self.last_updated) > 120


class CrossVenueState(str, Enum):
    SCAN     = "SCAN"
    ENTERING = "ENTERING"
    HOLD     = "HOLD"
    EXITING  = "EXITING"
    COOLDOWN = "COOLDOWN"


@dataclass
class SpreadSnapshot:
    """Funding rate spread between HL and Lighter for one asset."""
    asset: str
    hl_rate: float          # HL hourly funding rate (decimal, e.g. 0.0003 = 0.03%/h)
    lighter_rate: float     # Lighter hourly funding rate
    spread: float           # |hl_rate - lighter_rate|
    short_venue: str        # "hl" or "lighter" — where to short (higher rate)
    long_venue: str         # "hl" or "lighter" — where to long (lower rate)
    hl_mark_px: float = 0.0
    lighter_mark_px: float = 0.0
    sampled_at: int = field(default_factory=lambda: int(time.time()))

    @property
    def spread_pct_annual(self) -> float:
        return self.spread * 8760 * 100


@dataclass
class CrossVenuePosition:
    """Tracks one cross-exchange (HL + Lighter) delta-neutral arb position."""
    asset: str
    state: CrossVenueState = CrossVenueState.SCAN

    # Which venue is short (earning) vs long (paying)
    short_venue: str = "hl"      # "hl" or "lighter"
    long_venue: str = "lighter"  # "hl" or "lighter"

    # Sizing (same units on both legs for delta-neutrality)
    notional_usd: float = 0.0   # per-leg USD notional
    units: float = 0.0          # base-asset units (e.g. BTC count), same on both legs

    # Entry prices
    hl_entry_price: float = 0.0
    lighter_entry_price: float = 0.0

    # Funding rates at entry (for P&L attribution)
    hl_rate_at_entry: float = 0.0
    lighter_rate_at_entry: float = 0.0
    spread_at_entry: float = 0.0  # net spread = |hl - lighter| at entry

    # Timestamps
    entered_at: Optional[int] = None
    exited_at: Optional[int] = None
    cooldown_until: int = 0

    # P&L (signed: positive = received, negative = paid)
    hl_funding_collected: float = 0.0       # funding payments from HL WS
    lighter_funding_collected: float = 0.0  # funding payments from Lighter (polled)
    entry_fee_usd: float = 0.0
    exit_fee_usd: float = 0.0

    # Order IDs
    hl_order_id: Optional[str] = None
    lighter_order_id: Optional[str] = None

    # DB reference
    cycle_id: Optional[str] = None

    @property
    def hold_hours(self) -> float:
        if self.entered_at is None:
            return 0.0
        return (int(time.time()) - self.entered_at) / 3600

    @property
    def net_funding_usd(self) -> float:
        return self.hl_funding_collected + self.lighter_funding_collected

    @property
    def total_pnl_usd(self) -> float:
        return self.net_funding_usd - self.entry_fee_usd - self.exit_fee_usd

    @property
    def is_cooling_down(self) -> bool:
        return int(time.time()) < self.cooldown_until

    def mark_entered(self) -> None:
        self.entered_at = int(time.time())
        self.state = CrossVenueState.HOLD

    def mark_exited(self) -> None:
        self.exited_at = int(time.time())
        self.state = CrossVenueState.COOLDOWN


@dataclass
class SessionStats:
    """Accumulated session statistics."""
    started_at: int = field(default_factory=lambda: int(time.time()))
    total_cycles: int = 0
    successful_cycles: int = 0
    total_funding_usd: float = 0.0
    total_fees_usd: float = 0.0
    total_pnl_usd: float = 0.0
    legging_events: int = 0
    deleverage_events: int = 0
    kill_switch_activations: int = 0
    max_drawdown_usd: float = 0.0
    peak_pnl_usd: float = 0.0

    def update_drawdown(self) -> None:
        if self.total_pnl_usd > self.peak_pnl_usd:
            self.peak_pnl_usd = self.total_pnl_usd
        drawdown = self.peak_pnl_usd - self.total_pnl_usd
        if drawdown > self.max_drawdown_usd:
            self.max_drawdown_usd = drawdown
