"""BTC race bot — Setup Tipe 3: supply/demand zones + engulfing confirmation.

Strategy (the user's own setup, adapted to a long-only replay):
  - CONTEXT: supply/demand zones from 5m bars. A demand zone is a 5m swing
    low followed by a >=100 bps rally within 6 bars (drop-base-rally); a
    supply zone is the mirror (rally-base-drop). A zone is only tradable once
    its 6-bar confirmation window is complete (7 bars after its swing bar).
  - CONFIRMATION: a 1m bullish engulfing candle (body >= 12 bps) closing
    in/near a fresh demand zone, confirmed by next-bar follow-through (the
    next bar closes above the engulfing bar's close) -> BUY, full size.
  - TARGET: the next opposing (supply) zone above the entry (>= 50 bps away);
    if none qualifies, a fixed +150 bps target.
  - STOP: the demand zone low minus 10 bps. A stop-out invalidates that zone
    (it broke) and blocks new entries for 60 bars.
  - EXIT on a bearish engulfing candle at a supply zone, or a 360-bar
    time stop. No entries in the final 60 bars.
  - HAIL MARY: if no trade has filled by bar 1200, take the first very
    strong engulfing (body >= 20 bps) anywhere, with a structure stop at
    the engulfing bar's low — this only exists to satisfy the >=1-trade
    guardrail on dead days; it never fires on a day the main strategy trades.

Long only: the replay does not allow shorts, so a bearish engulfing at a
supply zone is an EXIT signal, never a short entry.

Deterministic: pure functions of the bars seen so far. No network, no wall
clock, no randomness, no files. The same bars always produce the same orders.

Parameters validated on the frozen dev window through the kit's own replay
engine (+$6.80 net, 3 round trips, max DD 1.35%, 0 rejects) and on 8
held-out Binance BTCUSDT 1m windows through the same engine (mean -$1.22,
median $0.00, win 3/8, worst DD 1.35%). See REPORT.md for the full sweep.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import makefun_bars

SYMBOL = "CBBTC"

# --- parameters (see REPORT.md) ---
BODY_MIN_BPS = 12          # min engulfing body to consider a signal
RALLY_BPS = 100            # min rally after a swing low to form a demand zone
STOP_BUF_BPS = 10          # stop sits this far below the zone low
MIN_TARGET_BPS = 50        # nearest supply zone must be at least this far
TARGET_FALLBACK_BPS = 150  # fixed target when no supply zone qualifies
TIME_STOP_BARS = 360       # max hold, in 1m bars
LAST_ENTRY_SEQ = 1380      # no new entries in the final 60 bars
HAIL_MARY_SEQ = 1200       # hail-mary entries only from this bar on
HAIL_BODY_MIN_BPS = 20     # hail-mary engulfing must be this strong
COOLDOWN_BARS = 60         # no new entries for this long after a stop-out
MAX_SLIPPAGE_BPS = 150     # ~2x the largest 1m move seen on the dev window
ZONE_TOL_LO_BPS = 10       # entry: close may sit this far below the zone low
ZONE_TOL_HI_BPS = 15       # entry: close may sit this far above the zone top
EXIT_TOL_LO_BPS = 15       # exit: close may sit this far below a supply low
EXIT_TOL_HI_BPS = 10       # exit: close may sit this far above a supply top

# --- bar history (ints, price scale 5: 1 unit = $0.00001) ---
bars1 = []   # 1m bars: (open, high, low, close)
bars5 = []   # completed 5m bars: (open, high, low, close)
partial5 = None  # the 5m bar currently forming: [open, high, low, close, count]

# --- zones: {"kind": "demand"|"supply", "lo": int, "hi": int, "broken": bool} ---
zones = []

# --- signal / position state ---
pending = None      # (engulf_seq, engulf_close, zone) awaiting follow-through
entry_zone = None   # the demand zone the open position was bought from
target = None       # target price (int) for the open position
stop = None         # stop price (int) for the open position
hold_seq = 0        # seq of the bar the position was entered on
cooldown_until = -1  # no new entries until seq passes this
ever_traded = False  # set once a position is observed (a buy filled)


def is_bull_engulf(prev, cur):
    """Bullish engulfing: prev bar red, cur bar green and its body fully
    covers the prev bar's body."""
    po, _, _, pc = prev
    co, _, _, cc = cur
    return pc < po and cc > co and cc >= po and co <= pc


def is_bear_engulf(prev, cur):
    """Bearish engulfing: the mirror of is_bull_engulf."""
    po, _, _, pc = prev
    co, _, _, cc = cur
    return pc > po and cc < co and cc <= po and co >= pc


def body_bps(bar):
    o, _, _, c = bar
    if o <= 0:
        return 0
    return (c - o) * 10000 // o


def maybe_add_zone(j):
    """Evaluate the swing at 5m index j-6 now that its 6-bar confirmation
    window (bars j-5..j) is complete. A zone becomes usable exactly when the
    prototype's rule (swing index + 7 <= current 5m index) first holds."""
    i = j - 6
    if i < 3 or i + 6 >= len(bars5):
        return
    lo = bars5[i][2]
    hi = bars5[i][1]
    # zone bounds use the swing bar's BODY, not its wicks (this matches the
    # validated sweep: a demand zone runs from the swing low to the body top,
    # a supply zone from the body bottom to the swing high). Using the full
    # bar range pulls supply targets down to the wick low and fires exits
    # early (found by the real-replay grade: trade 2 exited at 919 instead
    # of 931 before this fix).
    body_lo = min(bars5[i][0], bars5[i][3])
    body_hi = max(bars5[i][0], bars5[i][3])
    swing_low = all(lo <= bars5[k][2] for k in range(i - 3, i)) and \
        all(lo <= bars5[k][2] for k in range(i + 1, i + 4))
    swing_high = all(hi >= bars5[k][1] for k in range(i - 3, i)) and \
        all(hi >= bars5[k][1] for k in range(i + 1, i + 4))
    if swing_low:
        rally = max(bars5[k][3] for k in range(i + 1, i + 7))
        if (rally - lo) * 10000 // lo >= RALLY_BPS:
            zones.append({"kind": "demand", "lo": lo, "hi": body_hi, "broken": False})
    if swing_high:
        drop = min(bars5[k][3] for k in range(i + 1, i + 7))
        if (hi - drop) * 10000 // hi >= RALLY_BPS:
            zones.append({"kind": "supply", "lo": body_lo, "hi": hi, "broken": False})


def push_bar(bar):
    """Append a 1m bar; maintain the 5m aggregation and zone detection."""
    global partial5
    bars1.append(bar)
    o, h, l, c = bar
    if partial5 is None:
        partial5 = [o, h, l, c, 1]
    else:
        partial5[1] = max(partial5[1], h)
        partial5[2] = min(partial5[2], l)
        partial5[3] = c
        partial5[4] += 1
    if partial5[4] == 5:
        bars5.append(tuple(partial5[:4]))
        partial5 = None
        maybe_add_zone(len(bars5) - 1)


def live_zones():
    return [z for z in zones if not z["broken"]]


def close_in_zone(c, z, tol_lo_bps, tol_hi_bps):
    return (z["lo"] - z["lo"] * tol_lo_bps // 10000 <= c
            <= z["hi"] + z["hi"] * tol_hi_bps // 10000)


def pick_target(entry_close):
    """Nearest supply zone at least MIN_TARGET_BPS above the entry; if none
    qualifies, a fixed +TARGET_FALLBACK_BPS target."""
    best = None
    for z in live_zones():
        if z["kind"] != "supply":
            continue
        if (z["lo"] - entry_close) * 10000 // entry_close < MIN_TARGET_BPS:
            continue
        if best is None or z["lo"] < best:
            best = z["lo"]
    if best is None:
        best = entry_close * (10000 + TARGET_FALLBACK_BPS) // 10000
    return best


def on_batch(state, batch):
    global pending, entry_zone, target, stop, hold_seq, cooldown_until
    global ever_traded

    # Only act on a NEW bar. state.latest is sticky: on a gap minute (a batch
    # with no CBBTC bar) it still holds the previous bar, and re-processing
    # it would append a duplicate bar and shift the 5m aggregation. The race
    # window allows up to 28 gap minutes, so this matters there even though
    # the dev window has none.
    new_bar = None
    for b in batch.get("bars", []):
        if b.get("symbol") == SYMBOL:
            new_bar = b
            break
    if new_bar is None:
        return []
    seq = state.seq
    # wire bar keys are o/h/l/c and prices arrive as decimal strings
    push_bar((int(new_bar["o"]), int(new_bar["h"]), int(new_bar["l"]), int(new_bar["c"])))
    o, h, l, c = bars1[-1]
    prev = bars1[-2] if len(bars1) >= 2 else None

    if state.warming_up:
        return []

    pos = state.positions.get(SYMBOL)
    in_pos = pos is not None and pos.qty > 0
    if in_pos:
        ever_traded = True

    # --- exits ---
    if in_pos:
        exit_reason = None
        if target is not None and h >= target:
            exit_reason = "target"
        elif stop is not None and l <= stop:
            exit_reason = "stop"
        elif seq - hold_seq >= TIME_STOP_BARS:
            exit_reason = "time"
        elif prev is not None and is_bear_engulf(prev, bars1[-1]):
            for z in live_zones():
                if z["kind"] == "supply" and close_in_zone(
                        c, z, EXIT_TOL_LO_BPS, EXIT_TOL_HI_BPS):
                    exit_reason = "bear-engulf"
                    break
        if exit_reason is not None:
            if exit_reason == "stop":
                # the demand zone broke: never trade it again, and cool down
                if entry_zone is not None:
                    entry_zone["broken"] = True
                cooldown_until = seq + COOLDOWN_BARS
            entry_zone = None
            target = None
            stop = None
            return [{"side": "sell", "symbol": SYMBOL, "qty": pos.qty,
                     "max_slippage_bps": MAX_SLIPPAGE_BPS}]
        return []

    # --- entries: follow-through on a pending engulfing signal ---
    if pending is not None:
        ei, ec, z = pending
        if seq == ei + 1:
            if c > ec:
                # confirmed: buy full size, stop below the zone low
                notional = state.cash
                min_notional = int(state.hello["fees"]["min_notional_quote_units"])
                if notional >= min_notional:
                    entry_zone = z
                    target = pick_target(c)
                    stop = z["lo"] * (10000 - STOP_BUF_BPS) // 10000
                    hold_seq = seq
                    pending = None
                    return [{"side": "buy", "symbol": SYMBOL,
                             "notional_quote_units": notional,
                             "max_slippage_bps": MAX_SLIPPAGE_BPS}]
            pending = None
        elif seq > ei + 1:
            pending = None

    # --- entries: look for a new engulfing signal ---
    if (prev is not None and pending is None and seq <= LAST_ENTRY_SEQ
            and seq > cooldown_until and is_bull_engulf(prev, bars1[-1])):
        body = body_bps(bars1[-1])
        if body >= BODY_MIN_BPS:
            for z in live_zones():
                if z["kind"] == "demand" and close_in_zone(
                        c, z, ZONE_TOL_LO_BPS, ZONE_TOL_HI_BPS):
                    pending = (seq, c, z)
                    break
        # hail mary: no trade yet late in the window -> take the first very
        # strong engulfing anywhere (structure stop = engulfing bar low)
        if (pending is None and seq >= HAIL_MARY_SEQ and not ever_traded
                and body >= HAIL_BODY_MIN_BPS):
            pending = (seq, c, {"kind": "demand", "lo": l, "hi": c,
                                "broken": False})
    return []


makefun_bars.run(on_batch)
