"""Self-check for the 30-min price drift stop (2026-08-13, at the user's
request): same-day positions that drop (longs) or rise (shorts) more than
PRICE_DRIFT_STOP_PCT from EITHER entry OR the price recorded ~30 min ago get
exited. Built after confirming live that this morning's losers (DFSC, HLIT,
EROC, JACK) were all bought right at the open and all faded 4-8% before the
normal, wider trailing stop caught them.

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
from engine.config import PRICE_DRIFT_STOP_PCT

f = EnhancedExecutor._drift_stop_reason

assert PRICE_DRIFT_STOP_PCT == 1.0

# --- Longs: adverse move is a DROP ---

# Flat / up from both references -> no trigger.
assert f(current=10.10, entry=10.00, last_snapshot=10.05, is_long=True, stop_pct=1.0) is None

# Down > 1% from entry, even if last_snapshot is missing (first check) -> triggers.
assert f(current=9.80, entry=10.00, last_snapshot=None, is_long=True, stop_pct=1.0) is not None

# Flat vs entry but down > 1% from the last 30-min snapshot -> triggers
# (the "faded back" case even when still above the original entry).
reason = f(current=9.85, entry=9.80, last_snapshot=10.00, is_long=True, stop_pct=1.0)
assert reason is not None and "30min" in reason, f"expected a 30min-drift reason, got {reason!r}"

# Exactly at the threshold -> does not trigger (strictly >, not >=).
assert f(current=9.90, entry=10.00, last_snapshot=None, is_long=True, stop_pct=1.0) is None

# --- Shorts: adverse move is a RISE (mirrored) ---

assert f(current=9.90, entry=10.00, last_snapshot=9.95, is_long=False, stop_pct=1.0) is None
assert f(current=10.20, entry=10.00, last_snapshot=None, is_long=False, stop_pct=1.0) is not None
reason = f(current=10.15, entry=10.20, last_snapshot=10.00, is_long=False, stop_pct=1.0)
assert reason is not None and "30min" in reason, f"expected a 30min-drift reason, got {reason!r}"

# --- Missing/invalid references never false-trigger ---
assert f(current=9.00, entry=0.0, last_snapshot=None, is_long=True, stop_pct=1.0) is None, "entry<=0 must not trigger"
assert f(current=9.00, entry=10.0, last_snapshot=0.0, is_long=True, stop_pct=1.0) is not None, "entry alone can still trigger with a bad snapshot"

print("OK: price drift stop triggers on either reference, mirrors correctly for shorts, never false-triggers on missing data")
