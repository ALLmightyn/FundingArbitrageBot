"""notifier/telegram.py — Async Telegram alerts via Bot API.

If api.telegram.org is blocked (e.g. Russia), set in .env:
  TELEGRAM_PROXY=socks5://127.0.0.1:1080
  or
  TELEGRAM_PROXY=http://127.0.0.1:8080
"""
from __future__ import annotations

import os
import aiohttp
from core.logger import crash_log, log_warn


class TelegramNotifier:
    BASE = "https://api.telegram.org/bot{token}/sendMessage"

    def __init__(self, token: str, chat_id: str):
        self._token   = token
        self._chat_id = chat_id
        self._enabled = bool(token and chat_id)
        self._proxy   = os.getenv("TELEGRAM_PROXY", "").strip() or None
        if self._proxy:
            log_warn(f"Telegram: using proxy {self._proxy}")

    async def send_alert(self, text: str) -> None:
        if not self._enabled:
            return
        url = self.BASE.format(token=self._token)
        try:
            async with aiohttp.ClientSession() as s:
                await s.post(
                    url,
                    json={"chat_id": self._chat_id, "text": text, "parse_mode": "HTML"},
                    proxy=self._proxy,
                    timeout=aiohttp.ClientTimeout(total=10),
                )
        except Exception as e:
            crash_log("telegram.send_alert", e)

    async def send_report(self, stats, *, final: bool = False) -> None:
        from core.models import SessionStats
        s: SessionStats = stats
        title = "🔴 <b>Session Ended</b>" if final else "📊 <b>Periodic Report</b>"
        msg = (
            f"{title}\n"
            f"Cycles: {s.total_cycles} (ok: {s.successful_cycles})\n"
            f"Funding: ${s.total_funding_usd:+.4f}\n"
            f"Fees: ${s.total_fees_usd:.4f}\n"
            f"Net P&L: ${s.total_pnl_usd:+.4f}\n"
            f"Max DD: ${s.max_drawdown_usd:.4f}\n"
            f"Leggings: {s.legging_events} | "
            f"Deleverage: {s.deleverage_events} | "
            f"KillSwitch: {s.kill_switch_activations}"
        )
        await self.send_alert(msg)
