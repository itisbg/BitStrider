"""Self-check for the regular-hours liquidity floor split (2026-08-11), the
first-hour volume delay (2026-08-11), and the float-floor delay REMOVAL
(2026-08-17).

MIN_FLOAT_SHARES (200M) and MIN_AVG_DAILY_VOLUME (1M) were justified entirely
by an after-hours incident (BIOA 2026-07-31) but applied around the clock,
which also silently rejected real, liquid, actively-traded names during
regular hours (HTZ 161.9M float, BTDR 145.6M, IHRT 136.4M all got rejected
2026-08-11 despite not being remotely BIOA-like). Regular hours now use the
looser MIN_FLOAT_SHARES_REGULAR_HOURS / MIN_AVG_DAILY_VOLUME_REGULAR_HOURS.

A 7-trading-day backtest (2026-08-03..11) then found the first
REGULAR_HOURS_LOOSE_FLOOR_DELAY_MIN minutes of regular trading behave like
off-hours regardless of guardrail reason -- so the loosened VOLUME floor
waits for that delay to elapse, not just is_regular_hours=True.

2026-08-17, at the user's request ("remove the pre-open/first-hour 200M
block only before market hours"): the FLOAT floor no longer waits on that
same delay -- it loosens the moment is_regular_hours is True. Confirmed
live: CADL (60.1M float) was blocked twice at 09:05/09:10 CDT, 35+ min
into an already-open regular session, purely by the old first-hour rule.
Only the volume floor still keeps the delay -- this ask named the float
number specifically.

Run with:
  python scripts/test_liquidity_floor_split.py
No network calls -- exercises the real _effective_liquidity_floors() against
a stubbed market_state and injected ET timestamps.
"""
import sys
import datetime
from pathlib import Path
from types import SimpleNamespace
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.equity.scan import _effective_liquidity_floors, _ET
from engine.config import (
    MIN_FLOAT_SHARES, MIN_FLOAT_SHARES_REGULAR_HOURS,
    MIN_AVG_DAILY_VOLUME, MIN_AVG_DAILY_VOLUME_REGULAR_HOURS,
    REGULAR_HOURS_LOOSE_FLOOR_DELAY_MIN,
)

# Sanity: regular-hours floors are actually looser, not accidentally inverted.
assert MIN_FLOAT_SHARES_REGULAR_HOURS < MIN_FLOAT_SHARES, "regular-hours float floor should be lower"
assert MIN_AVG_DAILY_VOLUME_REGULAR_HOURS < MIN_AVG_DAILY_VOLUME, "regular-hours volume floor should be lower"

market_open = _ET.localize(datetime.datetime(2026, 8, 12, 9, 30, 0))  # a Wednesday
regular = SimpleNamespace(is_regular_hours=True)
closed = SimpleNamespace(is_regular_hours=False)

# Regular hours, well past the delay (e.g. 90 min in) -> both loosened.
t = market_open + datetime.timedelta(minutes=90)
min_float, min_vol = _effective_liquidity_floors(regular, now_et=t)
assert min_float == MIN_FLOAT_SHARES_REGULAR_HOURS
assert min_vol == MIN_AVG_DAILY_VOLUME_REGULAR_HOURS

# Regular hours, still inside the first-hour delay (e.g. 15 min in) -> FLOAT
# is loosened immediately (2026-08-17 change, CADL), VOLUME still strict
# (unchanged -- the backtest finding behind the volume delay still applies).
t = market_open + datetime.timedelta(minutes=15)
min_float, min_vol = _effective_liquidity_floors(regular, now_et=t)
assert min_float == MIN_FLOAT_SHARES_REGULAR_HOURS, "float floor should loosen immediately on regular hours, no delay"
assert min_vol == MIN_AVG_DAILY_VOLUME, "volume floor should still wait out the first-hour delay"

# Regular hours, the instant it opens (0 min in) -> float already loosened.
t = market_open
min_float, min_vol = _effective_liquidity_floors(regular, now_et=t)
assert min_float == MIN_FLOAT_SHARES_REGULAR_HOURS, "float floor has no time gate left at all -- just is_regular_hours"
assert min_vol == MIN_AVG_DAILY_VOLUME

# Boundary: exactly at the volume delay -> volume loosens too (>=, not >).
t = market_open + datetime.timedelta(minutes=REGULAR_HOURS_LOOSE_FLOOR_DELAY_MIN)
min_float, min_vol = _effective_liquidity_floors(regular, now_et=t)
assert min_float == MIN_FLOAT_SHARES_REGULAR_HOURS
assert min_vol == MIN_AVG_DAILY_VOLUME_REGULAR_HOURS

# Boundary: one minute before the volume delay -> volume still strict, float still loose.
t = market_open + datetime.timedelta(minutes=REGULAR_HOURS_LOOSE_FLOOR_DELAY_MIN - 1)
min_float, min_vol = _effective_liquidity_floors(regular, now_et=t)
assert min_float == MIN_FLOAT_SHARES_REGULAR_HOURS
assert min_vol == MIN_AVG_DAILY_VOLUME

# Pre/after-hours -> original, stricter (BIOA-driven) floors, regardless of time.
t = market_open + datetime.timedelta(minutes=90)
min_float, min_vol = _effective_liquidity_floors(closed, now_et=t)
assert min_float == MIN_FLOAT_SHARES
assert min_vol == MIN_AVG_DAILY_VOLUME

# market_state=None -> fail toward the stricter floors, not a crash.
min_float, min_vol = _effective_liquidity_floors(None)
assert min_float == MIN_FLOAT_SHARES
assert min_vol == MIN_AVG_DAILY_VOLUME

print("OK: float floor loosens immediately on is_regular_hours (no more first-hour delay); "
      "volume floor still waits out REGULAR_HOURS_LOOSE_FLOOR_DELAY_MIN; pre/after-hours "
      "and missing market_state stay strict on both")
