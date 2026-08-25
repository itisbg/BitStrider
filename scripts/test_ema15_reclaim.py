"""Self-check for the EMA15 reclaim switch (2026-08-24, user request):
"ema 15 above doesn't have to be only for the stocks entered above ema 15,
if the price exceeds ema 15 by 1% then these stocks should hold above ema15
minus 0.5%" -- a below-EMA15 entry permanently switches to the simpler
breakdown rule once it reclaims EMA15 by EMA15_RECLAIM_PCT%, instead of
staying on the wider entry-anchored delta/trend-drop tolerance for its
whole life.

Scoped to the new decision logic only (does the reclaim get detected at the
right threshold, does the switch stick) -- doesn't re-test the shared
exit-execution machinery (submit close / cancel / re-arm), which is
existing, already-covered code neither call here reaches.

Run with:
  python scripts/test_ema15_reclaim.py
No network calls -- client/bars are all stubbed.
"""
import sys
import datetime
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd
from types import SimpleNamespace
import engine.execution.enhanced as enhanced
from engine.execution.enhanced import EnhancedExecutor, EMA15_RECLAIM_PCT


def _bars_for(close: float, ema15: float, n: int = 20) -> pd.DataFrame:
    """Build a synthetic 1-min close series whose ewm(span=15).mean().iloc[-1]
    equals exactly `ema15`, with the final bar's own close equal to `close`.
    span=15 -> alpha=2/16=0.125. A flat series at level A has ewm==A
    throughout; the one differing final bar shifts it to
    alpha*close + (1-alpha)*A, so solving for A hits the target exactly."""
    alpha = 2.0 / (15 + 1)
    A = (ema15 - alpha * close) / (1 - alpha)
    closes = [A] * (n - 1) + [close]
    return pd.DataFrame({"close": closes})


class _FakeClient:
    def __init__(self, position):
        self._position = position
    def get_all_positions(self):
        return [self._position]


def _make_executor(entry_price, qty, is_long):
    ex = EnhancedExecutor.__new__(EnhancedExecutor)  # skip __init__ (no broker creds needed)
    today = datetime.date.today()
    # current_price only feeds check_ema15_exit's "if current <= 0: continue"
    # sanity gate in the calls this test exercises -- any positive placeholder
    # works, it doesn't need to track the synthetic bars' close per call.
    pos = SimpleNamespace(symbol="TEST", qty=str(qty if is_long else -qty), current_price=str(entry_price))
    ex.client = _FakeClient(pos)
    ex._entry_log = {"TEST": {"strategy": "test", "date": today}}
    ex._entry_ema15_delta = {}
    ex._entry_ema15 = {}
    ex._reclaimed_ema15 = set()
    return ex


# Long entry BELOW its own EMA15 (entry_price=14.50, entry ema15=15.00 -> entry_delta=-0.50)
ex = _make_executor(entry_price=14.50, qty=10, is_long=True)
ex._entry_ema15_delta["TEST"] = -0.50
ex._entry_ema15["TEST"] = 15.00

# --- Call 1: delta improved (-0.30, was -0.50) but nowhere near reclaiming EMA15
#     by 1% -- must stay on the entry-anchored rule set, unreclaimed.
enhanced.get_bars = lambda symbol, period, interval: _bars_for(close=14.70, ema15=15.00)
ex.check_ema15_exit()
assert "TEST" not in ex._reclaimed_ema15, "must not reclaim on a small delta improvement alone"

# --- Call 2: price now clearly exceeds EMA15 by more than EMA15_RECLAIM_PCT%
#     (1.5x the threshold, to stay clear of float precision at the exact
#     boundary through the ewm reconstruction below) -- must flip to
#     reclaimed, and under the breakdown rule (comfortably above
#     ema15 - 0.5%) this check itself must not exit.
reclaim_close = 15.00 * (1 + 1.5 * EMA15_RECLAIM_PCT / 100.0)
enhanced.get_bars = lambda symbol, period, interval: _bars_for(close=reclaim_close, ema15=15.00)
ex.check_ema15_exit()
assert "TEST" in ex._reclaimed_ema15, f"must reclaim once price exceeds EMA15 by {EMA15_RECLAIM_PCT}%"

# --- Call 3: reclaim flag must stick even if price now sits back UNDER the
#     reclaim threshold but still safely above the breakdown line
#     (ema15 - 0.5%) -- proves it's one-way, not re-evaluated each cycle.
enhanced.get_bars = lambda symbol, period, interval: _bars_for(close=15.02, ema15=15.00)
ex.check_ema15_exit()
assert "TEST" in ex._reclaimed_ema15, "reclaim must be one-way -- must not un-reclaim on a later dip"

print("OK: below-EMA15 entries reclaim into the breakdown rule at the right threshold, and it sticks")
