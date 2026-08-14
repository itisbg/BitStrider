"""Self-check for the price drift stop (2026-08-13, at the user's request;
refined same day). Same-day positions that drop (longs) or rise (shorts)
more than PRICE_DRIFT_STOP_PCT versus their own price PRICE_DRIFT_LOOKBACK_MIN
(30 min) ago get exited. Built after confirming live that this morning's
losers (DFSC, HLIT, EROC, JACK) were all bought right at the open and all
faded 4-8% before the normal, wider trailing stop caught them. Poll
frequency raised from every 30 min to every PRICE_DRIFT_CHECK_INTERVAL_MIN
(10 min, matching the TI-scrape cadence) so a fast 10-15 min collapse has a
real chance of being caught by the very next check -- the lookback window
compared against stays 30 min regardless of how often the check itself runs.

Run with:
  python scripts/test_price_drift_stop.py
No network calls -- exercises the pure decision function _drift_stop_reason
directly.
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.execution.enhanced import EnhancedExecutor
from engine.config import (
    PRICE_DRIFT_STOP_PCT, PRICE_DRIFT_CHECK_INTERVAL_MIN, PRICE_DRIFT_LOOKBACK_MIN,
)

f = EnhancedExecutor._drift_stop_reason

assert PRICE_DRIFT_STOP_PCT == 1.0
assert PRICE_DRIFT_CHECK_INTERVAL_MIN == 10
assert PRICE_DRIFT_LOOKBACK_MIN == 30
assert PRICE_DRIFT_LOOKBACK_MIN % PRICE_DRIFT_CHECK_INTERVAL_MIN == 0, "lookback should be a clean multiple of the poll interval"
assert PRICE_DRIFT_LOOKBACK_MIN // PRICE_DRIFT_CHECK_INTERVAL_MIN == 3, "3 ten-minute ticks = 30 minutes of lookback"

# --- Longs: adverse move is a DROP vs. the 30-min-ago reference ---

assert f(current=10.10, reference=10.05, is_long=True, stop_pct=1.0) is None, "up from reference -> no trigger"
assert f(current=9.80, reference=10.00, is_long=True, stop_pct=1.0) is not None, "down > 1% -> triggers"
assert f(current=9.90, reference=10.00, is_long=True, stop_pct=1.0) is None, "exactly at the threshold -> no trigger (strictly >)"

# No reference yet (not enough history) -> never a false trigger.
assert f(current=5.00, reference=None, is_long=True, stop_pct=1.0) is None
assert f(current=5.00, reference=0.0, is_long=True, stop_pct=1.0) is None

# --- Shorts: adverse move is a RISE (mirrored) ---

assert f(current=9.90, reference=9.95, is_long=False, stop_pct=1.0) is None
assert f(current=10.20, reference=10.00, is_long=False, stop_pct=1.0) is not None
assert f(current=10.10, reference=10.00, is_long=False, stop_pct=1.0) is None, "exactly at the threshold -> no trigger"

# --- Rolling-history mechanics: deque[0] becomes the reference only once full ---

from collections import deque
lookback_ticks = PRICE_DRIFT_LOOKBACK_MIN // PRICE_DRIFT_CHECK_INTERVAL_MIN
history = deque(maxlen=lookback_ticks)
prices = [10.00, 10.02, 10.01, 9.85]  # 4 ticks; window holds only the last 3
for i, p in enumerate(prices):
    ref = history[0] if len(history) == lookback_ticks else None
    if i < lookback_ticks:
        assert ref is None, f"tick {i}: not enough history yet, must not reference"
    else:
        assert ref == prices[i - lookback_ticks], f"tick {i}: reference should be exactly {lookback_ticks} ticks back"
    history.append(p)

print("OK: price drift stop triggers vs. the 30-min-ago reference, mirrors correctly for shorts, rolling history is exactly 3 ticks deep")
