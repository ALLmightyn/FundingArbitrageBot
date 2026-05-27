"""notifier/tg_bot.py — Telegram bot for querying live bot stats.

Commands:
  /status   — current session P&L, balances, uptime
  /pos      — open positions detail
  /spreads  — top-5 current spread opportunities
  /history  — last 5 closed cycles from DB
  /help     — list commands

Runs as a background polling loop alongside the main bot.
Does NOT require webhook — uses long-polling (getUpdates).
"""
from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Optional

import aiohttp

if TYPE_CHECKING:
    from core.models import SessionStats, CrossVenuePosition
    from feeds.spread_scanner import SpreadScanner

_POLL_TIMEOUT = 30   # long-poll seconds
_BASE = "https://api.telegram.org/bot{token}"


class TelegramBot:
    def __init__(
        self,
        token: str,
        chat_id: str,
        stats_ref=None,
        positions_ref: Optional[dict] = None,
        scanner_ref=None,
        db_path: str = "",
        started_at: int = 0,
        proxy: Optional[str] = None,
    ):
        self._token      = token
        self._chat_id    = str(chat_id)
        self._enabled    = bool(token and chat_id)
        self._proxy      = proxy or os.getenv("TELEGRAM_PROXY", "") or None
        self._base       = _BASE.format(token=token)
        self._offset     = 0
        self._started_at = started_at or int(time.time())

        # Live refs — updated by main loop
        self.stats     = stats_ref
        self.positions = positions_ref if positions_ref is not None else {}
        self.scanner   = scanner_ref
        self.db_path   = db_path

    # ── Public ────────────────────────────────────────────────────────────────

    async def run_forever(self) -> None:
        """Background loop. Launch with asyncio.create_task()."""
        if not self._enabled:
            return
        while True:
            try:
                updates = await self._get_updates()
                for upd in updates:
                    self._offset = upd["update_id"] + 1
                    msg = upd.get("message", {})
                    text = msg.get("text", "").strip()
                    cid  = str(msg.get("chat", {}).get("id", ""))
                    if cid == self._chat_id and text.startswith("/"):
                        await self._handle(text.split()[0].lower())
            except asyncio.CancelledError:
                return
            except Exception:
                await asyncio.sleep(5)

    async def send(self, text: str) -> None:
        if not self._enabled:
            return
        try:
            async with aiohttp.ClientSession() as s:
                await s.post(
                    f"{self._base}/sendMessage",
                    json={"chat_id": self._chat_id, "text": text,
                          "parse_mode": "HTML"},
                    proxy=self._proxy,
                    timeout=aiohttp.ClientTimeout(total=10),
                )
        except Exception:
            pass

    # ── Commands ──────────────────────────────────────────────────────────────

    async def _handle(self, cmd: str) -> None:
        handlers = {
            "/status":  self._cmd_status,
            "/pos":     self._cmd_positions,
            "/spreads": self._cmd_spreads,
            "/history": self._cmd_history,
            "/help":    self._cmd_help,
        }
        fn = handlers.get(cmd, self._cmd_unknown)
        await fn()

    async def _cmd_status(self) -> None:
        s = self.stats
        uptime = str(timedelta(seconds=int(time.time()) - self._started_at))
        if s is None:
            await self.send("Bot not running.")
            return
        pnl_sign = "+" if s.total_pnl_usd >= 0 else ""
        open_cnt = sum(
            1 for p in self.positions.values()
            if hasattr(p, "state") and str(p.state.value) == "HOLD"
        )
        text = (
            f"📊 <b>Status</b>\n"
            f"Uptime: <code>{uptime}</code>\n"
            f"─────────────────\n"
            f"Session P&L:   <b>${pnl_sign}{s.total_pnl_usd:.4f}</b>\n"
            f"Funding earned: ${s.total_funding_usd:+.4f}\n"
            f"Fees paid:      ${s.total_fees_usd:.4f}\n"
            f"─────────────────\n"
            f"Cycles total:  {s.total_cycles}  (ok: {s.successful_cycles})\n"
            f"Open now:      {open_cnt}\n"
            f"Legging events: {s.legging_events}\n"
            f"Max drawdown:  ${s.max_drawdown_usd:.4f}"
        )
        await self.send(text)

    async def _cmd_positions(self) -> None:
        hold = {a: p for a, p in self.positions.items()
                if hasattr(p, "state") and str(p.state.value) in ("HOLD", "ENTERING", "EXITING")}
        if not hold:
            await self.send("📋 <b>No open positions</b>")
            return
        lines = ["📋 <b>Open Positions</b>"]
        for asset, pos in hold.items():
            net = pos.total_pnl_usd
            sign = "+" if net >= 0 else ""
            lines.append(
                f"\n<b>{asset}</b> [{pos.state.value}]\n"
                f"  {pos.short_venue}→{pos.long_venue}  ${pos.notional_usd:.0f}/leg\n"
                f"  Hold: {pos.hold_hours:.1f}h  |  Entry spread: {pos.spread_at_entry*100:.4f}%/h\n"
                f"  Funding: ${pos.net_funding_usd:+.4f}\n"
                f"  Net P&L: <b>${sign}{net:.4f}</b>"
            )
        await self.send("\n".join(lines))

    async def _cmd_spreads(self) -> None:
        if self.scanner is None:
            await self.send("Scanner not ready.")
            return
        top = self.scanner.ranked[:5]
        if not top:
            await self.send("🔍 No opportunities above threshold right now.")
            return
        lines = [f"🔍 <b>Top Spreads</b>  (size $25/leg)\n"]
        SIZE = 25.0
        for i, s in enumerate(top, 1):
            daily = SIZE * s.spread * 24
            lines.append(
                f"{i}. <b>{s.asset}</b>  {s.spread*100:.4f}%/h  "
                f"({s.spread_pct_annual:.0f}% APR)\n"
                f"   ${daily:.3f}/day  |  {s.short_venue}→{s.long_venue}"
            )
        await self.send("\n".join(lines))

    async def _cmd_history(self) -> None:
        if not self.db_path:
            await self.send("DB path not configured.")
            return
        try:
            import aiosqlite
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    """SELECT asset, short_venue, long_venue,
                              spread_at_entry, net_pnl_usd, exit_reason,
                              entered_at, exited_at
                       FROM cross_venue_cycles
                       WHERE state='CLOSED'
                       ORDER BY exited_at DESC LIMIT 5"""
                ) as cur:
                    rows = await cur.fetchall()
            if not rows:
                await self.send("📜 No closed cycles yet.")
                return
            lines = ["📜 <b>Last 5 Cycles</b>\n"]
            for r in rows:
                dur_h = (r["exited_at"] - r["entered_at"]) / 3600 if r["exited_at"] else 0
                sign = "+" if r["net_pnl_usd"] >= 0 else ""
                lines.append(
                    f"<b>{r['asset']}</b>  {r['short_venue']}→{r['long_venue']}\n"
                    f"  Spread: {r['spread_at_entry']*100:.4f}%/h  |  Hold: {dur_h:.1f}h\n"
                    f"  P&L: <b>${sign}{r['net_pnl_usd']:.4f}</b>  |  Exit: {r['exit_reason']}\n"
                )
            await self.send("\n".join(lines))
        except Exception as e:
            await self.send(f"DB error: {e}")

    async def _cmd_help(self) -> None:
        await self.send(
            "🤖 <b>FundingArbitrageBot Commands</b>\n\n"
            "/status  — P&L, uptime, session stats\n"
            "/pos     — open positions detail\n"
            "/spreads — top-5 live spread opportunities\n"
            "/history — last 5 closed cycles\n"
            "/help    — this message"
        )

    async def _cmd_unknown(self) -> None:
        await self.send("Unknown command. Send /help for the list.")

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _get_updates(self) -> list:
        try:
            async with aiohttp.ClientSession() as s:
                resp = await s.get(
                    f"{self._base}/getUpdates",
                    params={"offset": self._offset, "timeout": _POLL_TIMEOUT,
                            "allowed_updates": ["message"]},
                    proxy=self._proxy,
                    timeout=aiohttp.ClientTimeout(total=_POLL_TIMEOUT + 5),
                )
                data = await resp.json()
                return data.get("result", [])
        except Exception:
            return []
