"""Self-check for the regular-hours liquidity floor split + first-hour delay
(2026-08-11, at the user's request).

MIN_FLOAT_SHARES (200M) and MIN_AVG_DAILY_VOLUME (1M) were justified entirely
by an after-hours incident (BIOA 2026-07-31) but applied around the clock,
which also silently rejected real, liquid, actively-traded names during
regular hours (HTZ 161.9M float, BTDR 145.6M, IHRT 136.4M all got rejected
2026-08-11 despite not being remotely BIOA-like). Regular hours now use the
looser MIN_FLOAT_SHARES_REGULAR_HOURS / MIN_AVG_DAILY_VOLUME_REGULAR_HOURS.

A 7-trading-day backtest (2026-08-03..11) then found the first
REGULAR_HOURS_LOOSE_FLOOR_DELAY_MIN minutes of regular trading behave like
off-hours regardless of guardrail reason (avg_volume/low_float/min_price all
went from solidly positive to flat-or-negative before 09:30 CDT, still
within is_regular_hours which starts at 08:30 CDT) — so the loosened floors
now also wait for that delay to elapse, not just is_regular_hours=True.
Pre-open, the first hour, and after-hours all keep the original, stricter
floors.

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

# Regular hours, well past the delay (e.g. 90 min in) -> loosened floors.
t = market_open + datetime.timedelta(minutes=90)
min_float, min_vol = _effective_liquidity_floors(regular, now_et=t)
assert min_float == MIN_FLOAT_SHARES_REGULAR_HOURS
assert min_vol == MIN_AVG_DAILY_VOLUME_REGULAR_HOURS

# Regular hours, still inside the first-hour delay (e.g. 15 min in) -> strict floors.
t = market_open + datetime.timedelta(minutes=15)
min_float, min_vol = _effective_liquidity_floors(regular, now_et=t)
assert min_float == MIN_FLOAT_SHARES, "first-hour trading should NOT get the loosened float floor"
assert min_vol == MIN_AVG_DAILY_VOLUME, "first-hour trading should NOT get the loosened volume floor"

# Boundary: exactly at the delay -> loosened (>=, not >).
t = market_open + datetime.timedelta(minutes=REGULAR_HOURS_LOOSE_FLOOR_DELAY_MIN)
min_float, min_vol = _effective_liquidity_floors(regular, now_et=t)
assert min_float == MIN_FLOAT_SHARES_REGULAR_HOURS
assert min_vol == MIN_AVG_DAILY_VOLUME_REGULAR_HOURS

# Boundary: one minute before the delay -> still strict.
t = market_open + datetime.timedelta(minutes=REGULAR_HOURS_LOOSE_FLOOR_DELAY_MIN - 1)
min_float, min_vol = _effective_liquidity_floors(regular, now_et=t)
assert min_float == MIN_FLOAT_SHARES
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

print("OK: liquidity floors loosen only 60+ min into regular hours; first hour, pre/after-hours, and missing market_state all stay strict")
