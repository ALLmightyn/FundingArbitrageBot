-- HLCarryBot database schema
-- Apply once on first run via db.py:init_db()

CREATE TABLE IF NOT EXISTS funding_cycles (
    id              TEXT PRIMARY KEY,           -- uuid
    asset           TEXT NOT NULL,
    state           TEXT NOT NULL DEFAULT 'OPEN',
    entered_at      INTEGER NOT NULL,
    exited_at       INTEGER,
    hold_hours      REAL,
    spot_size_usd   REAL NOT NULL,
    perp_size_usd   REAL NOT NULL,
    perp_leverage   INTEGER NOT NULL DEFAULT 3,
    spot_entry      REAL NOT NULL DEFAULT 0,
    perp_entry      REAL NOT NULL DEFAULT 0,
    spot_exit       REAL,
    perp_exit       REAL,
    entry_funding_rate  REAL,                   -- hourly rate at entry
    funding_collected   REAL DEFAULT 0.0,
    exit_pnl        REAL DEFAULT 0.0,
    fee_paid        REAL DEFAULT 0.0,
    net_pnl         REAL DEFAULT 0.0,
    exit_reason     TEXT                        -- 'funding_flip','max_hold','manual','iron_dome'
);

CREATE INDEX IF NOT EXISTS idx_cycles_asset ON funding_cycles(asset);
CREATE INDEX IF NOT EXISTS idx_cycles_state ON funding_cycles(state);

CREATE TABLE IF NOT EXISTS funding_payments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id        TEXT NOT NULL REFERENCES funding_cycles(id),
    asset           TEXT NOT NULL,
    paid_at         INTEGER NOT NULL,
    amount_usd      REAL NOT NULL,
    funding_rate_1h REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_payments_cycle ON funding_payments(cycle_id);
-- BUG-017: Prevent WS-replay duplicates. (asset, paid_at) is unique per HL funding event.
CREATE UNIQUE INDEX IF NOT EXISTS uq_payments_asset_time ON funding_payments(asset, paid_at);

CREATE TABLE IF NOT EXISTS legging_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    asset           TEXT NOT NULL,
    occurred_at     INTEGER NOT NULL,
    leg_filled      TEXT NOT NULL,             -- 'perp' or 'spot'
    cover_action    TEXT NOT NULL,             -- 'maker_chase','taker_cover','reversed'
    cover_cost_usd  REAL DEFAULT 0.0,
    cycle_id        TEXT
);

CREATE TABLE IF NOT EXISTS deleverage_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    asset           TEXT NOT NULL,
    occurred_at     INTEGER NOT NULL,
    trigger         TEXT NOT NULL,             -- 'margin_warn','margin_emergency'
    margin_ratio    REAL NOT NULL,
    size_before_usd REAL NOT NULL,
    size_after_usd  REAL NOT NULL,
    cost_usd        REAL DEFAULT 0.0,
    cycle_id        TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    id              TEXT PRIMARY KEY,           -- uuid
    started_at      INTEGER NOT NULL,
    ended_at        INTEGER,
    starting_capital REAL NOT NULL DEFAULT 1500.0,
    total_cycles    INTEGER DEFAULT 0,
    successful_cycles INTEGER DEFAULT 0,
    total_funding_usd REAL DEFAULT 0.0,
    total_fees_usd  REAL DEFAULT 0.0,
    total_pnl_usd   REAL DEFAULT 0.0,
    legging_events  INTEGER DEFAULT 0,
    deleverage_events INTEGER DEFAULT 0,
    kill_switch_activations INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS kill_switch_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    triggered_at    INTEGER NOT NULL,
    trigger_reason  TEXT NOT NULL,
    assets_affected TEXT,                      -- JSON list
    resume_at       INTEGER NOT NULL
);

-- Cross-venue (HL + Lighter) funding arb cycles
CREATE TABLE IF NOT EXISTS cross_venue_cycles (
    id                      TEXT PRIMARY KEY,  -- uuid
    asset                   TEXT NOT NULL,
    state                   TEXT NOT NULL DEFAULT 'OPEN',
    short_venue             TEXT NOT NULL,     -- 'hl' or 'lighter'
    long_venue              TEXT NOT NULL,
    entered_at              INTEGER NOT NULL,
    exited_at               INTEGER,
    notional_usd            REAL NOT NULL,     -- per-leg USD notional
    units                   REAL NOT NULL,     -- base-asset units (same on both legs)
    hl_entry_price          REAL NOT NULL DEFAULT 0,
    lighter_entry_price     REAL NOT NULL DEFAULT 0,
    hl_rate_at_entry        REAL DEFAULT 0,
    lighter_rate_at_entry   REAL DEFAULT 0,
    spread_at_entry         REAL DEFAULT 0,
    entry_fee_usd           REAL DEFAULT 0,
    exit_fee_usd            REAL DEFAULT 0,
    hl_funding_collected    REAL DEFAULT 0,
    lighter_funding_collected REAL DEFAULT 0,
    net_pnl_usd             REAL DEFAULT 0,
    exit_reason             TEXT
);

CREATE INDEX IF NOT EXISTS idx_cv_cycles_asset  ON cross_venue_cycles(asset);
CREATE INDEX IF NOT EXISTS idx_cv_cycles_state  ON cross_venue_cycles(state);
