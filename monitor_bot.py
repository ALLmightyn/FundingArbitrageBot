"""monitor_bot.py — emit a line ONLY when bot state changes meaningfully."""
import sqlite3
import sys
import time
from datetime import datetime

DB = "database/carry.db"


def ts(v):
    if v is None:
        return "—"
    try:
        return datetime.fromtimestamp(float(v)).strftime("%H:%M:%S")
    except Exception:
        return str(v)


def snap():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row

    session = con.execute(
        "SELECT id, started_at, ended_at, total_cycles, total_pnl_usd, legging_events "
        "FROM sessions ORDER BY started_at DESC LIMIT 1"
    ).fetchone()

    open_pos = con.execute(
        "SELECT id, asset, state, short_venue, long_venue, entered_at, notional_usd "
        "FROM cross_venue_cycles WHERE state='OPEN'"
    ).fetchall()

    recent = con.execute(
        "SELECT id, asset, state, entered_at, exited_at, net_pnl_usd, exit_reason "
        "FROM cross_venue_cycles ORDER BY entered_at DESC LIMIT 3"
    ).fetchall()

    kill = con.execute("SELECT COUNT(*) as n FROM kill_switch_log").fetchone()

    con.close()
    s = dict(session) if session else {}
    return (
        s,
        [dict(r) for r in open_pos],
        [dict(r) for r in recent],
        dict(kill),
    )


def state_key(session, open_pos, recent, kill):
    """Fingerprint of the state — change = emit."""
    s_id     = session.get("id", "")[:8]
    s_ended  = session.get("ended_at")
    s_cycles = session.get("total_cycles", 0)
    s_legs   = session.get("legging_events", 0)
    s_pnl    = round(session.get("total_pnl_usd", 0) or 0, 4)
    kills    = kill.get("n", 0)
    open_ids = tuple(sorted(p["id"] for p in open_pos))
    rec_ids  = tuple((r["id"], r["state"], r.get("exit_reason")) for r in recent)
    return (s_id, s_ended, s_cycles, s_legs, s_pnl, kills, open_ids, rec_ids)


prev_key = None
heartbeat_counter = 0

while True:
    try:
        session, open_pos, recent, kill = snap()
        key = state_key(session, open_pos, recent, kill)
        heartbeat_counter += 1

        changed = (key != prev_key)
        heartbeat = (heartbeat_counter % 20 == 0)  # every 10 min

        if changed or heartbeat:
            now = datetime.now().strftime("%H:%M:%S")
            tag = "CHANGE" if changed else "HEARTBEAT"
            sid = session.get("id", "?")[:8]
            status = "RUNNING" if session.get("ended_at") is None else f"ENDED@{ts(session.get('ended_at'))}"

            lines = [
                f"[{now}] {tag} | SESSION {sid} {status} | "
                f"cycles={session.get('total_cycles',0)} "
                f"pnl=${session.get('total_pnl_usd',0):.4f} "
                f"leggings={session.get('legging_events',0)}"
            ]

            for p in open_pos:
                lines.append(
                    f"  [OPEN ] {p['asset']:6} short={p['short_venue']} long={p['long_venue']} "
                    f"since={ts(p['entered_at'])} ${p['notional_usd']:.0f}"
                )
            if not open_pos:
                lines.append("  [OPEN ] none")

            for r in recent:
                pnl = f"${r.get('net_pnl_usd', 0):.4f}" if r.get('net_pnl_usd') else "$0.0000"
                lines.append(
                    f"  [{r['state']:6}] {r['asset']:6} "
                    f"in={ts(r['entered_at'])} out={ts(r['exited_at'])} "
                    f"pnl={pnl} {r.get('exit_reason') or ''}"
                )

            if kill.get("n", 0) > 0:
                lines.append(f"  *** KILL_SWITCH FIRED: {kill['n']} events ***")

            for l in lines:
                print(l)
            print("---")
            sys.stdout.flush()

            prev_key = key

    except Exception as e:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] [monitor error] {e}")
        sys.stdout.flush()

    time.sleep(30)
