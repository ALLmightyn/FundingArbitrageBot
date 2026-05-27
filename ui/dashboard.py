"""ui/dashboard.py — Live Rich terminal dashboard for CrossVenueBot.

Renders:
  ┌──────────────────────────────────────────────────────────────┐
  │  HEADER: status, session P&L, uptime, kill-switch state      │
  ├──────────────────────────────────────────────────────────────┤
  │  POSITIONS table (live)                                       │
  ├──────────────────────────────────────────────────────────────┤
  │  OPPORTUNITIES table (top-5 spreads from scanner)            │
  ├──────────────────────────────────────────────────────────────┤
  │  EVENT LOG (last 12 lines)                                    │
  └──────────────────────────────────────────────────────────────┘

Usage: instantiate Dashboard, call dashboard.start_live() to begin
rendering, then push log lines with dashboard.log(msg, level).
"""
from __future__ import annotations

import time
from collections import deque
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Deque, List, Optional, Tuple

from rich.align import Align
from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box

if TYPE_CHECKING:
    from core.models import CrossVenuePosition, SessionStats, SpreadSnapshot

# ── Log level colours ────────────────────────────────────────────────────────
_LEVEL_STYLE = {
    "info":  "cyan",
    "ok":    "bright_green",
    "warn":  "yellow",
    "error": "bright_red",
}

_MAX_LOG_LINES = 14


class Dashboard:
    """
    Live terminal dashboard. Designed to be the single source of console
    output while the bot is running.

    Quick integration in main_cross.py:
        dash = Dashboard()
        dash.start_live()
        # replace log() calls with dash.log(msg, "info"|"ok"|"warn"|"error")
    """

    def __init__(self) -> None:
        self._console      = Console()
        self._live: Optional[Live] = None
        self._started_at   = int(time.time())
        self._log_lines: Deque[Tuple[str, str, str]] = deque(maxlen=_MAX_LOG_LINES)

        # State refs set externally by main loop
        self.stats        = None   # SessionStats
        self.positions    = {}     # Dict[str, CrossVenuePosition]
        self.opportunities: List  = []   # List[SpreadSnapshot] from scanner
        self.hl_balance   = 0.0
        self.lt_balance   = 0.0
        self.portfolio_killed = False
        self.bot_status   = "STARTING"   # "RUNNING" | "KILLED" | "HALTED"

    # ── Public API ────────────────────────────────────────────────────────────

    def start_live(self) -> "Dashboard":
        """Start Rich Live context. Call once at bot startup."""
        self._live = Live(
            self._render(),
            console=self._console,
            refresh_per_second=2,
            screen=False,
        )
        self._live.start()
        return self

    def stop(self) -> None:
        if self._live:
            self._live.stop()

    def log(self, msg: str, level: str = "info") -> None:
        """Add a line to the event log panel."""
        ts = datetime.now().strftime("%H:%M:%S")
        self._log_lines.append((ts, level, msg))
        self._refresh()

    def refresh(self) -> None:
        """Force a visual refresh (call after updating stats/positions)."""
        self._refresh()

    # ── Internal render ───────────────────────────────────────────────────────

    def _refresh(self) -> None:
        if self._live and self._live.is_started:
            self._live.update(self._render())

    def _render(self) -> Group:
        return Group(
            self._render_header(),
            self._render_positions(),
            self._render_opportunities(),
            self._render_log(),
        )

    # ── Header ────────────────────────────────────────────────────────────────

    def _render_header(self) -> Panel:
        uptime = str(timedelta(seconds=int(time.time()) - self._started_at))
        pnl = self.stats.total_pnl_usd if self.stats else 0.0
        funding = self.stats.total_funding_usd if self.stats else 0.0
        fees    = self.stats.total_fees_usd    if self.stats else 0.0
        cycles  = self.stats.total_cycles      if self.stats else 0
        ok      = self.stats.successful_cycles if self.stats else 0

        pnl_color = "bright_green" if pnl >= 0 else "bright_red"
        status_color = {
            "RUNNING": "bright_green",
            "STARTING": "yellow",
            "KILLED":  "bright_red",
            "HALTED":  "bright_red",
        }.get(self.bot_status, "white")

        grid = Table.grid(padding=(0, 2))
        grid.add_column(justify="left",  min_width=22)
        grid.add_column(justify="left",  min_width=22)
        grid.add_column(justify="left",  min_width=22)
        grid.add_column(justify="right", min_width=22)

        grid.add_row(
            Text.assemble(("● ", status_color + " bold"), (self.bot_status, status_color + " bold")),
            Text.assemble(("Uptime: ", "dim"), (uptime, "white")),
            Text.assemble(("HL: ", "dim"), (f"${self.hl_balance:.2f}", "cyan"),
                          ("  LT: ", "dim"), (f"${self.lt_balance:.2f}", "cyan")),
            Text.assemble(("Session P&L: ", "dim"), (f"${pnl:+.4f}", pnl_color + " bold")),
        )
        grid.add_row(
            Text.assemble(("Cycles: ", "dim"), (f"{cycles}", "white"), (" ok:", "dim"), (f"{ok}", "bright_green")),
            Text.assemble(("Funding: ", "dim"), (f"${funding:+.4f}", "bright_green")),
            Text.assemble(("Fees: ", "dim"), (f"${fees:.4f}", "dim")),
            Text.assemble(("Net: ", "dim"), (f"${pnl:+.4f}", pnl_color)),
        )

        if self.portfolio_killed:
            grid.add_row(
                Text("⛔  PORTFOLIO KILL ACTIVE — no new entries", style="bright_red bold"),
                "", "", "",
            )

        return Panel(grid, title="[bold white]⚡ FundingArbitrageBot — HL ↔ Lighter[/]",
                     border_style="bright_blue", padding=(0, 1))

    # ── Positions ─────────────────────────────────────────────────────────────

    def _render_positions(self) -> Panel:
        table = Table(
            box=box.SIMPLE_HEAD,
            show_header=True,
            header_style="bold white",
            expand=True,
            padding=(0, 1),
        )
        table.add_column("Asset",       style="bold cyan",      width=8)
        table.add_column("State",       style="white",          width=9)
        table.add_column("Short→Long",  style="dim",            width=15)
        table.add_column("Size USD",    justify="right",        width=9)
        table.add_column("Hold",        justify="right",        width=7)
        table.add_column("Entry spr.",  justify="right",        width=11)
        table.add_column("Funding",     justify="right",        width=10)
        table.add_column("Fees",        justify="right",        width=8)
        table.add_column("Net P&L",     justify="right",        width=10)

        hold_positions = {k: v for k, v in self.positions.items()
                         if hasattr(v, 'state')}

        if not hold_positions:
            table.add_row(
                Text("—", style="dim"), Text("No open positions", style="dim italic"),
                "", "", "", "", "", "", ""
            )
        else:
            for asset, pos in hold_positions.items():
                state_style = {
                    "HOLD":     "bright_green",
                    "ENTERING": "yellow",
                    "EXITING":  "yellow",
                }.get(str(pos.state.value), "white")

                hold_h = pos.hold_hours
                hold_str = f"{hold_h:.1f}h"

                net = pos.total_pnl_usd
                net_style = "bright_green" if net >= 0 else "bright_red"
                fund_style = "bright_green" if pos.net_funding_usd >= 0 else "bright_red"

                table.add_row(
                    asset,
                    Text(str(pos.state.value), style=state_style),
                    f"{pos.short_venue}→{pos.long_venue}",
                    f"${pos.notional_usd:.0f}",
                    hold_str,
                    f"{pos.spread_at_entry*100:.4f}%/h",
                    Text(f"${pos.net_funding_usd:+.4f}", style=fund_style),
                    f"${pos.entry_fee_usd + pos.exit_fee_usd:.4f}",
                    Text(f"${net:+.4f}", style=net_style + " bold"),
                )

        title = f"[bold white]📋 Open Positions ({len(hold_positions)})[/]"
        return Panel(table, title=title, border_style="green", padding=(0, 0))

    # ── Opportunities ─────────────────────────────────────────────────────────

    def _render_opportunities(self) -> Panel:
        table = Table(
            box=box.SIMPLE_HEAD,
            show_header=True,
            header_style="bold white",
            expand=True,
            padding=(0, 1),
        )
        table.add_column("#",          width=3)
        table.add_column("Asset",      style="cyan",   width=8)
        table.add_column("Spread/h",   justify="right", width=10)
        table.add_column("APR",        justify="right", width=8)
        table.add_column("$/day @$25", justify="right", width=11)
        table.add_column("Break-even", justify="right", width=11)
        table.add_column("Direction",  style="dim",     width=18)

        SIZE = 25.0  # must match CROSS_POSITION_SIZE_USD
        FEE  = SIZE * 0.00050

        top5 = self.opportunities[:5]
        if not top5:
            table.add_row("—", Text("Scanning...", style="dim italic"), "", "", "", "", "")
        else:
            for i, s in enumerate(top5, 1):
                daily = SIZE * s.spread * 24
                be_h  = FEE / (SIZE * s.spread) if s.spread > 0 else 999
                spr_style = "bright_green" if s.spread >= 0.0003 else "yellow"
                table.add_row(
                    str(i),
                    s.asset,
                    Text(f"{s.spread*100:.4f}%", style=spr_style),
                    f"{s.spread_pct_annual:.0f}%",
                    f"${daily:.3f}",
                    f"{be_h:.1f}h",
                    f"{s.short_venue}→{s.long_venue}",
                )

        ts = datetime.now().strftime("%H:%M:%S")
        title = f"[bold white]🔍 Top Spreads (updated {ts})[/]"
        return Panel(table, title=title, border_style="blue", padding=(0, 0))

    # ── Event log ─────────────────────────────────────────────────────────────

    def _render_log(self) -> Panel:
        lines = list(self._log_lines)
        text = Text()
        for ts, level, msg in lines:
            style = _LEVEL_STYLE.get(level, "white")
            prefix = {"info": "  ", "ok": "✓ ", "warn": "⚠ ", "error": "✗ "}.get(level, "  ")
            text.append(f"[{ts}] {prefix}", style="dim")
            text.append(f"{msg}\n", style=style)
        if not lines:
            text.append("Waiting for events...", style="dim italic")
        return Panel(text, title="[bold white]📜 Event Log[/]",
                     border_style="dim", padding=(0, 1))
