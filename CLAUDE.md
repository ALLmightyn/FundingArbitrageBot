# 🤖 ROLE: SENIOR HFT SYSTEMS ARCHITECT — Hyperliquid Funding Carry

Delta-neutral funding carry bot on Hyperliquid. Capital $1,500. Project: `/root/projects/HLCarryBot/`.

---

## 🧠 AGENTIC MEMORY PROTOCOL (KARPATHY WIKI METHOD)
Rely on `brain/` as the bot's personal Wikipedia. Build and maintain it.

### 3 CORE OPERATIONS:
1. **INGEST:** New fact (bug, param, API behavior) → update relevant file in `brain/`.
2. **QUERY:** Any question → start with `brain/000_index.md`. Follow links.
3. **LINT:** Changed a constant or param → find stale mentions in `brain/` and delete them.

### BRAIN TOPOLOGY:
- `brain/000_index.md` — Root MOC: live state, DB schema, links to all notes. **READ FIRST.**
- `brain/hyperliquid_specs.md` — Verified fee/API/funding mechanics (source of truth).
- `brain/current_strategy.md` — Active constants, state machine, entry/exit logic.
- `brain/bugs_and_fixes.md` — Postmortem of all fixed bugs. Don't repeat.
- `brain/log.md` — Diary: every param change or fix — 1 timestamped line explaining WHAT and WHY.

---

## 🛑 GOLDEN RULES

### 1. MAKER FIRST
ALO (`tif="Alo"`) orders are free if unfilled (cancel = $0). Fee only on fill:
- Perp maker: **0.015%** | Perp taker: **0.045%**
- Spot maker: **0.040%** | Spot taker: **0.070%**
- Full round-trip (4 legs, all maker): **0.110%** — minimum entry threshold barrier.

### 2. PERP-FIRST ENTRY (Iron Dome spec)
Order CANNOT change:
1. Perp short ALO → wait fill.
2. Spot buy ALO → wait fill (`MAKER_CHASE_TIMEOUT_S=30s`).
3. Spot timeout → `IronDome.cover_naked_leg()` (close perp reduce-only taker).
4. 3 legging events in 1h → kill-switch 4h.

### 3. PRICE ROUNDING (CRITICAL)
Always use `client.round_price(px)`. Formula: `round(px, max(0, 5 - len(str(int(px)))))`.
NEVER hardcode `round(px, 1)` — causes "Price must be divisible by tick size".

### 4. SIZE ROUNDING
Always `await client.round_size(asset, size)` before any order. `szDecimals` cached in `HLClient._sz_decimals`. `float_to_wire` crashes on extra decimals.

### 5. ENTRY COOLDOWN
After failed entry (timeout/legging) → `_entry_cooldowns[asset] = now + EXIT_COOLDOWN_SECONDS`.
Check in `tick()` before opening position. Without cooldown → busy-wait loop.

---

## 💰 TOKEN ECONOMY & CODE RULES

- **NEVER `cat` full files.** Use `grep -n "def target" file.py`.
- **NEVER output raw logs to chat.** Write numbers to `brain/`, output only verdict.
- **SQL:** one targeted query with `ORDER BY DESC LIMIT 10`. Never `SELECT *`.
- Code changes via Edit (diff) only — never rewrite full files.
- Always read current code before editing (don't trust session memory).

---

## ❌ NEVER (graveyard)

- Taker-only entry/exit without reason (kills maker EV).
- `round(price, 1)` — hardcoded tick. Use `client.round_price(px)`.
- `async with await get_db()` — `aiosqlite.connect()` is not a coroutine, no `await`.
- Naked spot order as legging cover (spot routing ≠ perp). Reduce-only perp only.
