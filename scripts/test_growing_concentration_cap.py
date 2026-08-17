"""Self-check for the growing per-position concentration cap (2026-08-17,
at the user's request: "maximum holding as 20% of the portfolio value and
growing based on the continued positive returns").

A losing/flat position keeps the plain MAX_POSITION_CONCENTRATION_PCT
(20%) cap. A winning position's cap grows with its unrealized gain --
POSITION_CAP_GROWTH_FACTOR points of extra room per point of gain -- up
to POSITION_CAP_ABSOLUTE_MAX_PCT (35%), and never drops below the 20%
base regardless of how large a loss is.

Run with:
  python scripts/test_growing_concentration_cap.py
No network calls -- exercises the pure cap function directly.
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.execution.enhanced import EnhancedExecutor
from engine.config import (
    MAX_POSITION_CONCENTRATION_PCT, POSITION_CAP_GROWTH_FACTOR, POSITION_CAP_ABSOLUTE_MAX_PCT,
)

assert MAX_POSITION_CONCENTRATION_PCT == 20.0
assert POSITION_CAP_GROWTH_FACTOR == 0.25
assert POSITION_CAP_ABSOLUTE_MAX_PCT == 35.0

f = EnhancedExecutor._effective_concentration_cap_pct

# --- Losing/flat positions: plain 20% base, unaffected by loss size ---
assert f(0.0) == 20.0, "flat (0% gain) -> plain base cap"
assert f(-5.0) == 20.0, "a loser must not get LESS room than the base cap"
assert f(-50.0) == 20.0, "even a big loss stays at the base cap, never drops below it"

# --- Winning positions: cap grows linearly with gain ---
assert f(20.0) == 25.0, "up 20% x 0.25 factor = +5 -> 25% cap"
assert f(40.0) == 30.0, "up 40% x 0.25 = +10 -> 30% cap"
assert round(f(4.0), 2) == 21.0, "up 4% x 0.25 = +1 -> 21% cap"

# --- Absolute ceiling: never exceeds 35% no matter how large the gain ---
assert f(60.0) == 35.0, "up 60% x 0.25 = +15 -> would be 35%, right at the ceiling"
assert f(200.0) == 35.0, "a huge gain must still clamp to the 35% ceiling, not run away"

# --- Monotonic: cap never decreases as gain increases ---
prev = f(0.0)
for gain in range(0, 250, 5):
    cur = f(float(gain))
    assert cur >= prev, f"cap dropped from {prev} to {cur} as gain rose to {gain}%"
    assert cur <= POSITION_CAP_ABSOLUTE_MAX_PCT, f"cap {cur} exceeded the {POSITION_CAP_ABSOLUTE_MAX_PCT}% ceiling"
    prev = cur

print("OK: growing concentration cap stays at the 20% base for losing/flat positions, "
      "grows linearly with unrealized gain for winners, and clamps at the 35% absolute ceiling")
